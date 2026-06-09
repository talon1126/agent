"""Implement MCP tool adapters around the internal RAG query runtime.

The MCP layer is a transport adapter. It converts tool arguments into the
existing Phase D ``QueryRuntime`` contract, returns public
``KnowledgeHubResponse`` JSON, and keeps internal retrieval diagnostics out of
Agent-visible output. It does not implement retrieval algorithms, reranking,
PostgreSQL repositories, or answer generation.

E2 owns ``query_knowledge_hub``. Collection listing and document summary tools
remain placeholders until E3.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import psycopg

from src.core.config import DatabaseSettings, RagSettings, load_settings
from src.core.errors import DatabaseError
from src.core.response import KnowledgeHubResponse
from src.storage.postgres import PostgresPool, init_schema

MAX_IMAGE_BASE64_BYTES = 1_000_000


class QueryExecutionLike(Protocol):
    """Describe the query execution fields visible to the MCP tool."""

    response: KnowledgeHubResponse


class QueryRuntimeLike(Protocol):
    """Describe the runtime method used by ``query_knowledge_hub``."""

    def execute(
        self,
        query: str,
        *,
        collection: str,
        top_k: int,
        no_rerank: bool,
        trace_id: str,
    ) -> QueryExecutionLike:
        """Execute one query and return a public response wrapper."""


class MetadataReaderLike(Protocol):
    """Describe read-only metadata operations exposed as MCP tools."""

    def list_collections(self) -> list[dict[str, Any]]:
        """Return public searchable collection overviews."""

    def get_document_summary(
        self,
        *,
        document_id: str | None = None,
        source_uri: str | None = None,
        collection: str | None = None,
    ) -> dict[str, Any] | None:
        """Return one public document summary or ``None`` when absent."""


PoolFactory = Callable[[DatabaseSettings], PostgresPool]
SchemaInitializer = Callable[[PostgresPool], None]
RuntimeBuilder = Callable[[RagSettings, PostgresPool, bool], QueryRuntimeLike]
MetadataReaderFactory = Callable[[PostgresPool], MetadataReaderLike]
TraceIdFactory = Callable[[], str]
SettingsLoader = Callable[[], RagSettings]


class QueryKnowledgeHubTool:
    """Expose the configured RAG Retrieval pipeline as one MCP tool."""

    def __init__(
        self,
        *,
        settings_loader: SettingsLoader = load_settings,
        pool_factory: PoolFactory | None = None,
        schema_initializer: SchemaInitializer = init_schema,
        runtime_builder: RuntimeBuilder | None = None,
        trace_id_factory: TraceIdFactory | None = None,
        max_image_base64_bytes: int = MAX_IMAGE_BASE64_BYTES,
    ) -> None:
        """Configure resource factories without opening external resources.

        Args:
            settings_loader: Loads validated RAG settings for each tool call.
            pool_factory: Creates a PostgreSQL pool from database settings.
                ``None`` uses ``PostgresPool.from_settings``.
            schema_initializer: Ensures the schema exists before querying.
            runtime_builder: Creates the Phase D query runtime. ``None`` lazily
                imports the CLI runtime composition to avoid startup side
                effects.
            trace_id_factory: Produces per-call query trace IDs.
            max_image_base64_bytes: Upper bound for explicit image byte
                embedding in stdio payloads.
        """

        self._settings_loader = settings_loader
        self._pool_factory = pool_factory or PostgresPool.from_settings
        self._schema_initializer = schema_initializer
        self._runtime_builder = runtime_builder or _default_runtime_builder
        self._trace_id_factory = trace_id_factory or (
            lambda: f"mcp-query-{uuid4().hex}"
        )
        self._max_image_base64_bytes = max_image_base64_bytes

    async def query_knowledge_hub(
        self,
        query: str,
        collection: str | None = None,
        top_k: int | None = None,
        no_rerank: bool = False,
        include_image_base64: bool = False,
    ) -> dict[str, Any]:
        """Query the knowledge hub and return public MCP-safe JSON.

        Args:
            query: User question passed by AImodel or another MCP client.
            collection: Optional collection override. ``None`` uses the
                configured default collection.
            top_k: Optional final result count. ``None`` uses
                ``settings.retrieval.final_top_k``.
            no_rerank: Whether to preserve filtered RRF order and skip rerank.
            include_image_base64: When true, attach bounded image bytes to each
                returned image with an existing managed file path.

        Returns:
            ``ok=true`` public RAG response or ``ok=false`` structured business
            error for recoverable request validation failures.

        Raises:
            Database, provider, and configuration failures are intentionally not
            converted to ``ok=false`` because they are system-level failures
            that should be visible to the MCP host and app log.
        """

        validation_error = self._validate_request(
            query=query,
            collection=collection,
            top_k=top_k,
            no_rerank=no_rerank,
            include_image_base64=include_image_base64,
        )
        if validation_error is not None:
            return validation_error

        settings = self._settings_loader()
        active_collection = (
            collection.strip()
            if isinstance(collection, str) and collection.strip()
            else settings.retrieval.filters.default_collection
        )
        active_top_k = (
            settings.retrieval.final_top_k if top_k is None else top_k
        )
        pool = self._pool_factory(settings.database)
        try:
            pool.open()
            self._schema_initializer(pool)
            runtime = self._runtime_builder(settings, pool, no_rerank)
            execution = runtime.execute(
                query.strip(),
                collection=active_collection,
                top_k=active_top_k,
                no_rerank=no_rerank,
                trace_id=self._trace_id_factory(),
            )
            payload = execution.response.model_dump(mode="json")
            if include_image_base64:
                self._attach_image_base64(payload)
            return payload
        except ValueError as error:
            return _business_error("invalid_request", str(error))
        finally:
            pool.close()

    @staticmethod
    def _validate_request(
        *,
        query: str,
        collection: str | None,
        top_k: int | None,
        no_rerank: bool,
        include_image_base64: bool,
    ) -> dict[str, Any] | None:
        """Validate MCP request primitives before opening external resources."""

        if not isinstance(query, str) or not query.strip():
            return _business_error("invalid_request", "query must not be blank")
        if collection is not None and (
            not isinstance(collection, str) or not collection.strip()
        ):
            return _business_error(
                "invalid_request",
                "collection must be a non-blank string when provided",
            )
        if top_k is not None and (
            isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0
        ):
            return _business_error("invalid_request", "top_k must be a positive integer")
        if not isinstance(no_rerank, bool):
            return _business_error("invalid_request", "no_rerank must be a boolean")
        if not isinstance(include_image_base64, bool):
            return _business_error(
                "invalid_request",
                "include_image_base64 must be a boolean",
            )
        return None

    def _attach_image_base64(self, payload: dict[str, Any]) -> None:
        """Attach bounded image bytes to an already public response payload."""

        for image in payload.get("images", []):
            if not isinstance(image, dict):
                continue
            file_path = image.get("file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                continue
            path = Path(file_path)
            if not path.is_file() or path.stat().st_size > self._max_image_base64_bytes:
                continue
            image["base64_content"] = base64.b64encode(path.read_bytes()).decode("ascii")


def _business_error(code: str, message: str) -> dict[str, Any]:
    """Return the stable recoverable MCP business-error envelope."""

    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }


class MetadataTool:
    """Expose collection catalog and document summaries as MCP tools.

    The tool owns MCP request validation, pool lifecycle, schema initialization,
    and public response envelopes. Actual PostgreSQL read queries are delegated
    to ``PostgresMetadataReader`` so tests can inject a fake reader and so the
    server boundary does not embed SQL.
    """

    def __init__(
        self,
        *,
        settings_loader: SettingsLoader = load_settings,
        pool_factory: PoolFactory | None = None,
        schema_initializer: SchemaInitializer = init_schema,
        reader_factory: MetadataReaderFactory | None = None,
    ) -> None:
        """Configure metadata tool dependencies without opening resources.

        Args:
            settings_loader: Loads validated settings for each tool call after
                request-level validation succeeds.
            pool_factory: Creates the PostgreSQL pool. ``None`` uses
                ``PostgresPool.from_settings``.
            schema_initializer: Ensures metadata tables exist before reads.
            reader_factory: Creates a read-only metadata reader bound to the
                open pool. ``None`` uses the production PostgreSQL reader.
        """

        self._settings_loader = settings_loader
        self._pool_factory = pool_factory or PostgresPool.from_settings
        self._schema_initializer = schema_initializer
        self._reader_factory = reader_factory or PostgresMetadataReader

    async def list_collections(self) -> dict[str, Any]:
        """List searchable collections with public document and chunk counts.

        Returns:
            ``ok=true`` with a ``collections`` list when at least one collection
            has successfully ingested documents, otherwise ``ok=false`` with a
            readable ``no_collections`` business error.

        Raises:
            Configuration and database failures are system failures and remain
            visible to the MCP host rather than being converted into business
            errors.
        """

        settings = self._settings_loader()
        pool = self._pool_factory(settings.database)
        try:
            pool.open()
            self._schema_initializer(pool)
            collections = self._reader_factory(pool).list_collections()
            if not collections:
                return _business_error(
                    "no_collections",
                    "no searchable collections are available",
                )
            return {"ok": True, "collections": collections}
        finally:
            pool.close()

    async def get_document_summary(
        self,
        document_id: str | None = None,
        source_uri: str | None = None,
        collection: str | None = None,
    ) -> dict[str, Any]:
        """Return one document summary by ID or source URI.

        Args:
            document_id: Optional stable document ID generated by the ingestion
                pipeline.
            source_uri: Optional source path/URI stored as ``source_path``.
            collection: Optional collection filter used to disambiguate a
                source URI that appears in multiple collections.

        Returns:
            ``ok=true`` with a public ``document`` summary, ``ok=false`` with
            ``invalid_request`` for bad lookup arguments, or ``ok=false`` with
            ``document_not_found`` when no matching document exists.
        """

        validation_error = self._validate_summary_request(
            document_id=document_id,
            source_uri=source_uri,
            collection=collection,
        )
        if validation_error is not None:
            return validation_error

        settings = self._settings_loader()
        pool = self._pool_factory(settings.database)
        try:
            pool.open()
            self._schema_initializer(pool)
            summary = self._reader_factory(pool).get_document_summary(
                document_id=document_id.strip() if isinstance(document_id, str) else None,
                source_uri=source_uri.strip() if isinstance(source_uri, str) else None,
                collection=collection.strip() if isinstance(collection, str) else None,
            )
            if summary is None:
                return _business_error(
                    "document_not_found",
                    "document summary was not found",
                )
            return {"ok": True, "document": summary}
        except ValueError as error:
            return _business_error("invalid_request", str(error))
        finally:
            pool.close()

    @staticmethod
    def _validate_summary_request(
        *,
        document_id: str | None,
        source_uri: str | None,
        collection: str | None,
    ) -> dict[str, Any] | None:
        """Validate summary lookup identity before opening external resources."""

        has_document_id = isinstance(document_id, str) and bool(document_id.strip())
        has_source_uri = isinstance(source_uri, str) and bool(source_uri.strip())
        if document_id is not None and not has_document_id:
            return _business_error(
                "invalid_request",
                "document_id must be a non-blank string when provided",
            )
        if source_uri is not None and not has_source_uri:
            return _business_error(
                "invalid_request",
                "source_uri must be a non-blank string when provided",
            )
        if has_document_id and has_source_uri:
            return _business_error(
                "invalid_request",
                "provide only one of document_id or source_uri",
            )
        if not has_document_id and not has_source_uri:
            return _business_error(
                "invalid_request",
                "provide document_id or source_uri",
            )
        if collection is not None and (
            not isinstance(collection, str) or not collection.strip()
        ):
            return _business_error(
                "invalid_request",
                "collection must be a non-blank string when provided",
            )
        return None


class PostgresMetadataReader:
    """Read public MCP metadata directly from PostgreSQL.

    The reader is intentionally narrow and read-only. It translates relational
    rows into stable public dictionaries for MCP tools and does not return full
    document text, raw chunk payloads, embeddings, BM25 postings, or internal
    trace details.
    """

    def __init__(self, pool: PostgresPool) -> None:
        """Bind the reader to an already opened PostgreSQL pool.

        Args:
            pool: Open pool owned by ``MetadataTool`` for the duration of one
                MCP tool call.
        """

        self._pool = pool

    def list_collections(self) -> list[dict[str, Any]]:
        """Return searchable collection overviews ordered by collection ID."""

        rows = self._read(
            operation="mcp_list_collections",
            query="""
            SELECT
                collection.id,
                COUNT(DISTINCT document.id) AS document_count,
                COUNT(chunk.id) AS chunk_count,
                MAX(
                    GREATEST(
                        document.updated_at,
                        COALESCE(chunk.updated_at, document.updated_at)
                    )
                ) AS updated_at
            FROM rag_collections AS collection
            JOIN rag_documents AS document
              ON document.collection_id = collection.id
             AND document.lifecycle_status = 'success'
            LEFT JOIN rag_chunks AS chunk
              ON chunk.collection_id = document.collection_id
             AND chunk.document_id = document.id
            GROUP BY collection.id
            ORDER BY collection.id ASC
            """,
            params=(),
            many=True,
        )
        return [
            {
                "collection": collection_id,
                "document_count": int(document_count),
                "chunk_count": int(chunk_count),
                "updated_at": _isoformat(updated_at),
            }
            for collection_id, document_count, chunk_count, updated_at in rows
        ]

    def get_document_summary(
        self,
        *,
        document_id: str | None = None,
        source_uri: str | None = None,
        collection: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a public document summary by document ID or source URI.

        Args:
            document_id: Stable document ID. Mutually exclusive with
                ``source_uri``.
            source_uri: Stored source path/URI. May require ``collection`` when
                the same source exists in multiple collections.
            collection: Optional source URI disambiguation filter.

        Returns:
            Summary dictionary with lifecycle status and section outline, or
            ``None`` when no row matches.

        Raises:
            ValueError: If a source URI lookup matches multiple collections and
                no collection filter was supplied.
            DatabaseError: If PostgreSQL rejects the read.
        """

        rows = self._document_rows(
            document_id=document_id,
            source_uri=source_uri,
            collection=collection,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError("source_uri matched multiple documents; provide collection")

        (
            active_document_id,
            collection_id,
            source_path,
            title,
            content,
            summary,
            lifecycle_status,
            metadata,
            updated_at,
            chunk_count,
        ) = rows[0]
        sections = self._section_outline(active_document_id)
        return {
            "document_id": active_document_id,
            "collection": collection_id,
            "source_uri": source_path,
            "title": title or _title_from_source_uri(source_path),
            "summary": _summary_from_column_or_metadata_or_content(
                summary,
                metadata,
                content,
            ),
            "lifecycle_status": lifecycle_status,
            "chunk_count": int(chunk_count),
            "sections": sections,
            "updated_at": _isoformat(updated_at),
        }

    def _document_rows(
        self,
        *,
        document_id: str | None,
        source_uri: str | None,
        collection: str | None,
    ) -> list[tuple[Any, ...]]:
        """Load candidate document rows plus their current chunk counts.

        Args:
            document_id: Stable document ID when the caller selected direct
                lookup. When present, ``source_uri`` and ``collection`` are
                ignored because request validation has already enforced mutual
                exclusivity.
            source_uri: Stored source path/URI used for indirect lookup.
            collection: Optional source lookup filter. Without it, the query
                deliberately returns at most two rows so the caller can detect
                ambiguity without loading an unbounded result set.

        Returns:
            Positional PostgreSQL rows containing the document identity, source
            fields, first-class summary, lifecycle status, metadata, updated
            timestamp, and chunk count required by ``get_document_summary``.
        """

        if document_id is not None:
            return self._read(
                operation="mcp_document_summary_by_id",
                query="""
                SELECT
                    document.id,
                    document.collection_id,
                    document.source_path,
                    document.title,
                    document.content,
                    document.summary,
                    document.lifecycle_status,
                    document.metadata,
                    document.updated_at,
                    COUNT(chunk.id) AS chunk_count
                FROM rag_documents AS document
                LEFT JOIN rag_chunks AS chunk
                  ON chunk.collection_id = document.collection_id
                 AND chunk.document_id = document.id
                WHERE document.id = %s
                GROUP BY document.id
                """,
                params=(document_id,),
                many=True,
            )
        return self._read(
            operation="mcp_document_summary_by_source_uri",
            query="""
            SELECT
                document.id,
                document.collection_id,
                document.source_path,
                document.title,
                document.content,
                document.summary,
                document.lifecycle_status,
                document.metadata,
                document.updated_at,
                COUNT(chunk.id) AS chunk_count
            FROM rag_documents AS document
            LEFT JOIN rag_chunks AS chunk
              ON chunk.collection_id = document.collection_id
             AND chunk.document_id = document.id
            WHERE document.source_path = %s
              AND (%s IS NULL OR document.collection_id = %s)
            GROUP BY document.id
            ORDER BY document.updated_at DESC, document.id ASC
            LIMIT 2
            """,
            params=(source_uri, collection, collection),
            many=True,
        )

    def _section_outline(self, document_id: str) -> list[dict[str, Any]]:
        """Read the public section outline for one document.

        Args:
            document_id: Stable document ID whose chunks provide heading paths.

        Returns:
            Ordered public section objects. Empty heading paths are skipped
            because they do not represent user-visible document structure.

        Notes:
            The outline aggregates by ``heading_path`` instead of returning raw
            chunks, keeping MCP output compact and preventing full chunk text
            from leaking through the metadata tool.
        """

        rows = self._read(
            operation="mcp_document_section_outline",
            query="""
            SELECT
                heading_path,
                COUNT(*) AS chunk_count,
                MIN(chunk_index) AS first_chunk_index
            FROM rag_chunks
            WHERE document_id = %s
              AND jsonb_array_length(heading_path) > 0
            GROUP BY heading_path
            ORDER BY first_chunk_index ASC, heading_path ASC
            """,
            params=(document_id,),
            many=True,
        )
        return [
            {
                "path": list(path),
                "chunk_count": int(chunk_count),
            }
            for path, chunk_count, _first_chunk_index in rows
        ]

    def _read(
        self,
        *,
        operation: str,
        query: str,
        params: tuple[Any, ...],
        many: bool,
    ) -> Any:
        """Execute one PostgreSQL read with the MCP metadata error boundary.

        Args:
            operation: Stable operation label included in ``DatabaseError``
                context for app logs and later trace correlation.
            query: Parameterized SQL statement. Callers must never interpolate
                user input into this string.
            params: Bound parameter tuple passed to psycopg.
            many: Whether the caller expects ``fetchall()`` instead of
                ``fetchone()``.

        Returns:
            One psycopg row, a list of rows, or ``None`` depending on ``many``
            and query results.

        Raises:
            DatabaseError: Wraps psycopg driver failures while preserving the
            operation label and original cause for diagnostics.
        """

        try:
            with self._pool.connection() as connection:
                cursor = connection.execute(query, params)
                return cursor.fetchall() if many else cursor.fetchone()
        except DatabaseError:
            raise
        except psycopg.Error as error:
            raise DatabaseError(
                "PostgreSQL MCP metadata read failed",
                context={"operation": operation},
                cause=error,
            ) from error


def _summary_from_column_or_metadata_or_content(
    summary: Any,
    metadata: Any,
    content: str,
) -> str:
    """Choose the best public document summary for MCP metadata tools.

    Args:
        summary: First-class ``rag_documents.summary`` value. This is the
            preferred source because document summaries are no longer stored in
            arbitrary metadata.
        metadata: Document metadata loaded from PostgreSQL JSONB. Supported
            summary-like keys are retained only as a backward-compatible
            fallback for rows created before the first-class column existed.
        content: Canonical document text used only as a short fallback excerpt.

    Returns:
        A display-safe summary string. The fallback is capped so the MCP
        metadata tool never becomes a full document export path.
    """

    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    if isinstance(metadata, dict):
        for key in ("summary", "description", "abstract"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    normalized = " ".join(content.split())
    return normalized[:240]


def _title_from_source_uri(source_uri: str) -> str:
    """Derive a readable title from a source path when metadata lacks one.

    Args:
        source_uri: Stored source path or URI from ``rag_documents``.

    Returns:
        Filename stem when available, otherwise the original source value.
    """

    path = Path(source_uri)
    return path.stem or source_uri


def _isoformat(value: Any) -> str | None:
    """Convert PostgreSQL timestamp values into JSON-safe ISO strings.

    Args:
        value: Timestamp returned by psycopg, ``None``, or a driver-specific
            value already represented as a string.

    Returns:
        ISO-8601 string, stringified fallback value, or ``None``.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _default_runtime_builder(
    settings: RagSettings,
    pool: PostgresPool,
    no_rerank: bool,
) -> QueryRuntimeLike:
    """Create the production QueryRuntime using the Phase D composition path."""

    from src.scripts.query import _build_runtime

    return _build_runtime(settings, pool, no_rerank)
