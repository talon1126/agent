"""Persist and reconstruct RAG documents, chunks, traces, and evaluations.

This module is the relational repository boundary between ingestion/business
code and the PostgreSQL schema. ``DocumentRepository`` owns collection-aware
document persistence, while ``ChunkRepository`` owns ordered chunk persistence
and content-hash calculation. ``TraceRepository`` stores the four structured
Query/Ingestion Trace sections, and ``EvaluationRepository`` stores evaluation
runs plus independently queryable metric rows.

The repositories do not open database connections, initialize schema, create
embeddings, calculate evaluation metrics, or implement document lifecycle state
transitions. Callers provide an open ``PostgresPool`` and coordinate
higher-level pipeline operations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src.core.errors import DatabaseError
from src.core.types import Chunk, Document
from src.storage.postgres import PostgresPool


def _freeze_json(value: Any) -> Any:
    """Recursively convert JSON containers into immutable equivalents.

    Args:
        value: JSON-compatible scalar, mapping, list, or tuple.

    Returns:
        Scalars unchanged, mappings as ``MappingProxyType``, and sequences as
        tuples. Nested containers are frozen recursively.
    """

    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Freeze a JSON object while retaining a precise mapping return type."""

    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("Expected a JSON mapping")
    return frozen


def _freeze_stages(
    stages: list[dict[str, Any]] | tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    """Freeze an ordered Trace stage list and every stage object."""

    frozen = _freeze_json(stages)
    if not isinstance(frozen, tuple) or not all(isinstance(stage, Mapping) for stage in frozen):
        raise TypeError("Trace stages must contain JSON mappings")
    return frozen


def _json_compatible(value: Any) -> Any:
    """Convert immutable JSON containers back to serializer-compatible values."""

    if isinstance(value, Mapping):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    return value


def _ensure_collection(connection: Any, collection_id: str) -> None:
    """Create a minimal collection record inside the caller transaction.

    Args:
        connection: Active psycopg connection owned by ``PostgresPool``.
        collection_id: Stable identifier also used as the initial display name.

    Side Effects:
        Inserts one ``rag_collections`` row when the collection is absent.
    """

    connection.execute(
        """
        INSERT INTO rag_collections (id, name)
        VALUES (%s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (collection_id, collection_id),
    )


def _read_rows(
    pool: PostgresPool,
    *,
    operation: str,
    query: str,
    params: tuple[Any, ...],
    many: bool,
) -> Any:
    """Execute one read query with a consistent repository error boundary.

    Args:
        pool: Open connection pool used for the read.
        operation: Trace-safe operation name included in ``DatabaseError``.
        query: Parameterized SQL statement.
        params: Bound SQL parameters; credentials and raw SQL are not logged.
        many: Whether to return ``fetchall()`` instead of ``fetchone()``.

    Returns:
        One positional row, a list of positional rows, or ``None``.

    Raises:
        DatabaseError: If connection management or psycopg query execution
            fails. The original driver exception is retained as ``cause``.
    """

    try:
        with pool.connection() as connection:
            cursor = connection.execute(query, params)
            return cursor.fetchall() if many else cursor.fetchone()
    except DatabaseError:
        raise
    except psycopg.Error as error:
        raise DatabaseError(
            "PostgreSQL repository read failed",
            context={"operation": operation},
            cause=error,
        ) from error


class DocumentRepository:
    """Store canonical source documents within searchable collections.

    A document write automatically creates the collection when it does not yet
    exist. The generated collection uses its stable ID as its initial display
    name, allowing ingestion to persist the first document without a separate
    administrative setup step.
    """

    def __init__(self, pool: PostgresPool) -> None:
        """Bind the repository to an application-managed connection pool.

        Args:
            pool: Open PostgreSQL pool used for every repository transaction.
        """

        self._pool = pool

    def upsert(
        self,
        document: Document,
        *,
        collection_id: str,
        source_path: str,
        source_hash: str,
        title: str | None = None,
    ) -> Document:
        """Insert or replace one canonical document by its stable Python ID.

        Args:
            document: Validated domain document produced by a loader.
            collection_id: Stable collection receiving the document.
            source_path: Original path or URI used for source-level deduplication.
            source_hash: SHA256 digest of the original source bytes.
            title: Optional human-readable title for Dashboard list views.

        Returns:
            The same validated ``Document`` supplied by the caller.

        Raises:
            DatabaseError: If the pool is unavailable or PostgreSQL rejects the
                collection/document transaction.

        Side Effects:
            Creates the collection when absent and inserts or updates one row in
            ``rag_documents``.
        """

        with self._pool.transaction() as connection:
            _ensure_collection(connection, collection_id)
            # Document IDs may include source_hash, so changed source content can
            # legitimately produce a new ID while retaining the same logical
            # collection/source_path identity. Removing the superseded version
            # first also activates schema cascades for stale chunks and images.
            connection.execute(
                """
                DELETE FROM rag_documents
                WHERE collection_id = %s
                  AND source_path = %s
                  AND id <> %s
                """,
                (collection_id, source_path, document.id),
            )
            connection.execute(
                """
                INSERT INTO rag_documents (
                    id,
                    collection_id,
                    source_path,
                    source_hash,
                    title,
                    content,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    collection_id = EXCLUDED.collection_id,
                    source_path = EXCLUDED.source_path,
                    source_hash = EXCLUDED.source_hash,
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    document.id,
                    collection_id,
                    source_path,
                    source_hash,
                    title,
                    document.text,
                    Jsonb(document.metadata),
                ),
            )
        return document

    def get_by_id(self, document_id: str) -> Document | None:
        """Load one document by stable ID.

        Args:
            document_id: Python-generated document identifier.

        Returns:
            Reconstructed ``Document`` or ``None`` when no row exists.

        Raises:
            DatabaseError: If connection acquisition or query execution fails.
        """

        row = _read_rows(
            self._pool,
            operation="document_get",
            query="""
            SELECT id, content, metadata
            FROM rag_documents
            WHERE id = %s
            """,
            params=(document_id,),
            many=False,
        )
        return self._to_document(row)

    def list_by_collection(self, collection_id: str) -> list[Document]:
        """List collection documents in deterministic source-path order.

        Args:
            collection_id: Stable collection identifier used by ingestion and
                retrieval filters.

        Returns:
            Documents ordered by ``source_path`` and stable document ID.

        Raises:
            DatabaseError: If connection acquisition or query execution fails.
        """

        rows = _read_rows(
            self._pool,
            operation="document_list_by_collection",
            query="""
            SELECT id, content, metadata
            FROM rag_documents
            WHERE collection_id = %s
            ORDER BY source_path ASC, id ASC
            """,
            params=(collection_id,),
            many=True,
        )
        return [self._to_document(row) for row in rows if row is not None]

    def list_retrievable_by_collection(self, collection_id: str) -> list[Document]:
        """List documents visible to retrieval for one collection.

        Args:
            collection_id: Collection used by retrieval and Dashboard filters.

        Returns:
            Documents whose lifecycle status is ``success`` only. Pending,
            processing, failed, and deleted records are intentionally hidden
            from retrieval-visible data.

        Raises:
            DatabaseError: If connection acquisition or query execution fails.
        """

        rows = _read_rows(
            self._pool,
            operation="document_list_retrievable_by_collection",
            query="""
            SELECT id, content, metadata
            FROM rag_documents
            WHERE collection_id = %s
              AND lifecycle_status = 'success'
            ORDER BY source_path ASC, id ASC
            """,
            params=(collection_id,),
            many=True,
        )
        return [self._to_document(row) for row in rows if row is not None]

    def has_successful_source_hash(
        self,
        *,
        collection_id: str,
        source_path: str,
        source_hash: str,
    ) -> bool:
        """Check whether one canonical source version is fully indexed.

        Args:
            collection_id: Collection that owns the source.
            source_path: Canonical absolute source path stored during ingestion.
            source_hash: SHA256 digest calculated from original source bytes
                before Loader conversion.

        Returns:
            ``True`` only when the same collection, source path, and hash exist
            with lifecycle status ``success``.

        Raises:
            ValueError: If an identity field is blank or ``source_hash`` is not
                a lowercase/uppercase 64-character hexadecimal digest.
            DatabaseError: If PostgreSQL lookup fails.

        Notes:
            Source path participates in the lookup so two independent files
            containing identical bytes retain separate citations and lifecycle
            records.
        """

        if not collection_id.strip():
            raise ValueError("collection_id must not be blank")
        if not source_path.strip():
            raise ValueError("source_path must not be blank")
        if len(source_hash) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in source_hash
        ):
            raise ValueError("source_hash must be a SHA256 hexadecimal digest")
        normalized_source_hash = source_hash.lower()

        row = _read_rows(
            self._pool,
            operation="document_successful_source_hash_exists",
            query="""
            SELECT EXISTS (
                SELECT 1
                FROM rag_documents
                WHERE collection_id = %s
                  AND source_path = %s
                  AND source_hash = %s
                  AND lifecycle_status = 'success'
            )
            """,
            params=(collection_id, source_path, normalized_source_hash),
            many=False,
        )
        return bool(row[0]) if row is not None else False

    def get_lifecycle_status(self, document_id: str) -> str | None:
        """Read the current lifecycle status for one document.

        Args:
            document_id: Stable Python document identifier.

        Returns:
            Lifecycle status string, or ``None`` when the document is absent.

        Raises:
            DatabaseError: If connection acquisition or query execution fails.
        """

        row = _read_rows(
            self._pool,
            operation="document_lifecycle_get",
            query="""
            SELECT lifecycle_status
            FROM rag_documents
            WHERE id = %s
            """,
            params=(document_id,),
            many=False,
        )
        return row[0] if row is not None else None

    def mark_processing(self, document_id: str) -> str:
        """Mark a document as actively being ingested or re-indexed.

        Args:
            document_id: Stable Python document identifier generated before the
                ingestion pipeline writes chunks or image references.

        Returns:
            The persisted ``processing`` lifecycle status returned by
            PostgreSQL.

        Raises:
            DatabaseError: If the document row does not exist or the database
                update fails.
        """

        return self._mark_lifecycle(document_id, "processing")

    def mark_success(self, document_id: str) -> str:
        """Mark a document as successfully indexed and retrieval-visible.

        Args:
            document_id: Stable Python document identifier whose chunks,
                embeddings, and image indexes have already been written.

        Returns:
            The persisted ``success`` lifecycle status returned by PostgreSQL.

        Raises:
            DatabaseError: If the document row does not exist or the database
                update fails.
        """

        return self._mark_lifecycle(document_id, "success")

    def mark_failed(self, document_id: str) -> str:
        """Mark a document as failed so retrieval filters exclude it.

        Args:
            document_id: Stable Python document identifier for the failed
                ingestion attempt.

        Returns:
            The persisted ``failed`` lifecycle status returned by PostgreSQL.

        Raises:
            DatabaseError: If the document row does not exist or the database
                update fails.

        Notes:
            Failed documents intentionally keep their document, chunk, and image
            index data so a later retry or debugging session can inspect the
            partial ingestion output. Retrieval uses
            ``list_retrievable_by_collection()`` and therefore ignores them.
        """

        return self._mark_lifecycle(document_id, "failed")

    def mark_deleted(self, document_id: str) -> str:
        """Mark a document deleted and remove its retrieval-visible children.

        Args:
            document_id: Stable Python document identifier.

        Returns:
            The persisted ``deleted`` lifecycle status.

        Raises:
            DatabaseError: If the document is absent or cleanup fails.

        Side Effects:
            Deletes child chunk rows and image-index rows in the same
            transaction, then keeps the document metadata row as deleted.
        """

        with self._pool.transaction() as connection:
            row = connection.execute(
                """
                SELECT collection_id
                FROM rag_documents
                WHERE id = %s
                """,
                (document_id,),
            ).fetchone()
            if row is None:
                raise DatabaseError(
                    "Document does not exist",
                    context={
                        "operation": "document_mark_deleted",
                        "document_id": document_id,
                    },
                )
            collection_id = row[0]
            connection.execute(
                """
                DELETE FROM image_index
                WHERE document_id = %s
                  AND collection_id = %s
                """,
                (document_id, collection_id),
            )
            connection.execute(
                """
                DELETE FROM rag_chunks
                WHERE document_id = %s
                  AND collection_id = %s
                """,
                (document_id, collection_id),
            )
            status = self._update_lifecycle_in_transaction(
                connection,
                document_id,
                "deleted",
            )
        return status

    def _mark_lifecycle(self, document_id: str, status: str) -> str:
        """Update one document lifecycle status in a transaction.

        Args:
            document_id: Stable Python document identifier.
            status: One schema-supported lifecycle value. Public methods keep
                callers away from raw status strings; this helper centralizes
                the common transaction wrapper.

        Returns:
            The lifecycle status stored by PostgreSQL.

        Raises:
            DatabaseError: If the document does not exist or the update fails.
        """

        with self._pool.transaction() as connection:
            return self._update_lifecycle_in_transaction(
                connection,
                document_id,
                status,
            )

    @staticmethod
    def _update_lifecycle_in_transaction(
        connection: Any,
        document_id: str,
        status: str,
    ) -> str:
        """Execute the lifecycle update and return the stored status.

        Args:
            connection: Active psycopg connection owned by ``PostgresPool``.
            document_id: Stable Python document identifier.
            status: Target schema-supported lifecycle state.

        Returns:
            The lifecycle status returned by PostgreSQL.

        Raises:
            DatabaseError: If no document row exists for ``document_id``.
        """

        row = connection.execute(
            """
            UPDATE rag_documents
            SET lifecycle_status = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING lifecycle_status
            """,
            (status, document_id),
        ).fetchone()
        if row is None:
            raise DatabaseError(
                "Document does not exist",
                context={
                    "operation": "document_lifecycle_update",
                    "document_id": document_id,
                    "lifecycle_status": status,
                },
            )
        return row[0]

    @staticmethod
    def _to_document(row: tuple[Any, ...] | None) -> Document | None:
        """Convert one positional psycopg row into the shared domain contract.

        Args:
            row: ``(id, content, metadata)`` returned by PostgreSQL.

        Returns:
            Validated ``Document`` or ``None`` for a missing row.
        """

        if row is None:
            return None
        document_id, content, metadata = row
        return Document(id=document_id, text=content, metadata=metadata)


class ChunkRepository:
    """Store retrievable chunks while preserving document-relative ordering.

    A chunk's logical identity inside a document is ``chunk_index``. When
    transformed content changes and produces a new stable chunk ID, upsert
    replaces the row at that logical position so stale IDs cannot remain
    retrievable.
    """

    def __init__(self, pool: PostgresPool) -> None:
        """Bind the repository to an application-managed connection pool.

        Args:
            pool: Open PostgreSQL pool used for batch writes and reads.
        """

        self._pool = pool

    def upsert_many(
        self,
        chunks: list[Chunk],
        *,
        collection_id: str,
        document_id: str,
    ) -> list[Chunk]:
        """Persist an ordered chunk batch in one transaction.

        Args:
            chunks: Validated chunks belonging to one source document.
            collection_id: Collection containing the parent document.
            document_id: Existing parent document ID.

        Returns:
            A shallow list copy preserving the caller's input order.

        Raises:
            DatabaseError: If the document is absent, collection ownership does
                not match, or any batch statement fails. The transaction rolls
                back the complete batch.

        Side Effects:
            Inserts or replaces ``rag_chunks`` rows and computes each
            ``content_hash`` from UTF-8 chunk text.
        """

        persisted = list(chunks)
        if not persisted:
            return persisted
        chunk_ids = [chunk.id for chunk in persisted]
        chunk_indexes = [chunk.chunk_index for chunk in persisted]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("Chunk batch contains duplicate chunk IDs")
        if len(set(chunk_indexes)) != len(chunk_indexes):
            raise ValueError("Chunk batch contains duplicate chunk indexes")

        with self._pool.transaction() as connection:
            # Stable chunk IDs do not include chunk_index. Re-splitting can move
            # existing IDs to new positions, including swapping two positions.
            # Removing every incoming ID/index collision before insertion avoids
            # order-dependent primary-key failures while preserving one atomic
            # document-level batch update.
            connection.execute(
                """
                DELETE FROM rag_chunks
                WHERE document_id = %s
                  AND (
                      id = ANY(%s)
                      OR chunk_index = ANY(%s)
                  )
                """,
                (document_id, chunk_ids, chunk_indexes),
            )
            for chunk in persisted:
                heading_path = chunk.metadata.get("heading_path", [])
                connection.execute(
                    """
                    INSERT INTO rag_chunks (
                        id,
                        collection_id,
                        document_id,
                        chunk_index,
                        content,
                        content_hash,
                        start_offset,
                        end_offset,
                        source_ref,
                        heading_path,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        chunk.id,
                        collection_id,
                        document_id,
                        chunk.chunk_index,
                        chunk.text,
                        sha256(chunk.text.encode("utf-8")).hexdigest(),
                        chunk.start_offset,
                        chunk.end_offset,
                        Jsonb(chunk.source_ref) if chunk.source_ref is not None else None,
                        Jsonb(heading_path),
                        Jsonb(chunk.metadata),
                    ),
                )
        return persisted

    def get_by_id(self, chunk_id: str) -> Chunk | None:
        """Load one chunk by its current stable ID.

        Args:
            chunk_id: Python-generated chunk identifier.

        Returns:
            Reconstructed ``Chunk`` or ``None`` when the ID is absent or was
            replaced by a newer chunk at the same logical position.

        Raises:
            DatabaseError: If connection acquisition or query execution fails.
        """

        row = _read_rows(
            self._pool,
            operation="chunk_get",
            query="""
            SELECT
                id,
                content,
                metadata,
                chunk_index,
                start_offset,
                end_offset,
                source_ref
            FROM rag_chunks
            WHERE id = %s
            """,
            params=(chunk_id,),
            many=False,
        )
        return self._to_chunk(row)

    def list_by_document(self, document_id: str) -> list[Chunk]:
        """List all current chunks for a document in source order.

        Args:
            document_id: Stable parent document identifier.

        Returns:
            Chunks ordered by ``chunk_index`` and then ID for deterministic
            behavior even if corrupted legacy data bypasses the unique index.

        Raises:
            DatabaseError: If connection acquisition or query execution fails.
        """

        rows = _read_rows(
            self._pool,
            operation="chunk_list_by_document",
            query="""
            SELECT
                id,
                content,
                metadata,
                chunk_index,
                start_offset,
                end_offset,
                source_ref
            FROM rag_chunks
            WHERE document_id = %s
            ORDER BY chunk_index ASC, id ASC
            """,
            params=(document_id,),
            many=True,
        )
        return [self._to_chunk(row) for row in rows if row is not None]

    @staticmethod
    def _to_chunk(row: tuple[Any, ...] | None) -> Chunk | None:
        """Convert one positional psycopg row into the shared chunk contract.

        Args:
            row: Selected chunk columns in domain-constructor order.

        Returns:
            Validated ``Chunk`` or ``None`` for a missing row.
        """

        if row is None:
            return None
        (
            chunk_id,
            content,
            metadata,
            chunk_index,
            start_offset,
            end_offset,
            source_ref,
        ) = row
        return Chunk(
            id=chunk_id,
            text=content,
            metadata=metadata,
            chunk_index=chunk_index,
            start_offset=start_offset,
            end_offset=end_offset,
            source_ref=source_ref,
        )


@dataclass(frozen=True, slots=True)
class QueryTraceRecord:
    """Represent one query trace stored for observability and evaluation.

    Attributes mirror ``rag_query_traces``. JSON sections are recursively
    frozen after construction so Dashboard services cannot accidentally alter
    historical trace evidence in memory.

    Attributes:
        trace_id: Stable identifier shared by logs and PostgreSQL.
        collection_id: Knowledge collection used by the query.
        raw_query: Original user question before query processing.
        request_source: Calling surface such as AImodel or MCP.
        started_at: Time at which Query Pipeline processing began.
        finished_at: Completion time, or ``None`` while running.
        status: Schema-supported lifecycle state.
        basic_info: Request identity and static context.
        stages: Ordered Query Pipeline stage observations.
        summary_metrics: End-to-end latency, result count, and error summary.
        evaluation_metrics: Query/document relevance and related quality data.
        error: Structured failure information when the query fails.
        created_at: PostgreSQL insertion timestamp.
    """

    trace_id: str
    collection_id: str
    raw_query: str
    request_source: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    basic_info: Mapping[str, Any] = field(default_factory=dict)
    stages: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    summary_metrics: Mapping[str, Any] = field(default_factory=dict)
    evaluation_metrics: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """Deep-freeze Trace JSON so repository records cannot be mutated."""

        object.__setattr__(self, "basic_info", _freeze_mapping(self.basic_info))
        object.__setattr__(self, "stages", _freeze_stages(self.stages))
        object.__setattr__(
            self,
            "summary_metrics",
            _freeze_mapping(self.summary_metrics),
        )
        object.__setattr__(
            self,
            "evaluation_metrics",
            _freeze_mapping(self.evaluation_metrics),
        )
        if self.error is not None:
            object.__setattr__(self, "error", _freeze_mapping(self.error))


@dataclass(frozen=True, slots=True)
class IngestionTraceRecord:
    """Represent one ingestion trace and its four structured trace sections.

    Attributes:
        trace_id: Stable identifier shared by logs and PostgreSQL.
        collection_id: Collection receiving the source document.
        source_uri: Original file path or source URI.
        source_hash: SHA256 digest used for ingestion deduplication.
        started_at: Time at which ingestion processing began.
        finished_at: Completion time, or ``None`` while running.
        status: Running, success, skipped, or failed state.
        basic_info: Source and request-level metadata.
        stages: Ordered load/split/transform/embed/upsert observations.
        summary_metrics: End-to-end duration and processing counts.
        evaluation_metrics: Chunk or ingestion quality measurements.
        error: Structured failure information when ingestion fails.
        created_at: PostgreSQL insertion timestamp.
    """

    trace_id: str
    collection_id: str
    source_uri: str
    source_hash: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    basic_info: Mapping[str, Any] = field(default_factory=dict)
    stages: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    summary_metrics: Mapping[str, Any] = field(default_factory=dict)
    evaluation_metrics: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """Deep-freeze ingestion JSON sections after construction."""

        object.__setattr__(self, "basic_info", _freeze_mapping(self.basic_info))
        object.__setattr__(self, "stages", _freeze_stages(self.stages))
        object.__setattr__(
            self,
            "summary_metrics",
            _freeze_mapping(self.summary_metrics),
        )
        object.__setattr__(
            self,
            "evaluation_metrics",
            _freeze_mapping(self.evaluation_metrics),
        )
        if self.error is not None:
            object.__setattr__(self, "error", _freeze_mapping(self.error))


@dataclass(frozen=True, slots=True)
class EvaluationRunRecord:
    """Represent one evaluator execution and its reproducibility snapshot.

    Attributes:
        id: Stable Python-generated evaluation run ID.
        collection_id: Collection evaluated by the run.
        evaluator: Configured evaluator implementation name.
        dataset_name: Golden dataset or benchmark identifier.
        status: Pending/running/success/failed lifecycle state.
        started_at: Evaluator execution start time.
        finished_at: Completion time when the run has ended.
        settings_snapshot: Retrieval, rerank, model, and dataset configuration.
        summary: Aggregate run-level observations.
        error: Structured failure information for unsuccessful runs.
        created_at: Durable run creation timestamp.
    """

    id: str
    collection_id: str
    evaluator: str
    dataset_name: str
    status: str = "pending"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    settings_snapshot: Mapping[str, Any] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """Deep-freeze settings, summary, and optional error evidence."""

        object.__setattr__(
            self,
            "settings_snapshot",
            _freeze_mapping(self.settings_snapshot),
        )
        object.__setattr__(self, "summary", _freeze_mapping(self.summary))
        if self.error is not None:
            object.__setattr__(self, "error", _freeze_mapping(self.error))


@dataclass(frozen=True, slots=True)
class EvaluationResultRecord:
    """Represent one named metric produced by an evaluation run.

    Attributes:
        id: Stable Python-generated metric result ID.
        run_id: Parent evaluation run ID.
        metric_name: Queryable metric key such as ``mrr``.
        metric_value: Finite numeric score constrained by PostgreSQL.
        details: Thresholds, sample counts, and per-metric evidence.
        created_at: Original metric creation timestamp.
    """

    id: str
    run_id: str
    metric_name: str
    metric_value: float
    details: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """Deep-freeze evaluator-specific metric evidence."""

        object.__setattr__(self, "details", _freeze_mapping(self.details))


class TraceRepository:
    """Persist Query and Ingestion traces for Dashboard history views."""

    def __init__(self, pool: PostgresPool) -> None:
        """Bind trace persistence to an application-managed PostgreSQL pool.

        Args:
            pool: Open pool used for trace transactions and reads.
        """

        self._pool = pool

    def upsert_query_trace(self, trace: QueryTraceRecord) -> QueryTraceRecord:
        """Insert or update one Query Trace by its stable trace ID.

        Args:
            trace: Complete Query Trace snapshot produced by TraceContext.

        Returns:
            Persisted immutable record including database ``created_at``.

        Side Effects:
            Creates the collection when absent and writes one query-trace row.
        """

        with self._pool.transaction() as connection:
            _ensure_collection(connection, trace.collection_id)
            row = connection.execute(
                """
                INSERT INTO rag_query_traces (
                    trace_id,
                    collection_id,
                    raw_query,
                    request_source,
                    started_at,
                    finished_at,
                    status,
                    basic_info,
                    stages,
                    summary_metrics,
                    evaluation_metrics,
                    error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (trace_id) DO UPDATE SET
                    collection_id = EXCLUDED.collection_id,
                    raw_query = EXCLUDED.raw_query,
                    request_source = EXCLUDED.request_source,
                    started_at = EXCLUDED.started_at,
                    finished_at = EXCLUDED.finished_at,
                    status = EXCLUDED.status,
                    basic_info = EXCLUDED.basic_info,
                    stages = EXCLUDED.stages,
                    summary_metrics = EXCLUDED.summary_metrics,
                    evaluation_metrics = EXCLUDED.evaluation_metrics,
                    error = EXCLUDED.error
                RETURNING
                    trace_id,
                    collection_id,
                    raw_query,
                    request_source,
                    started_at,
                    finished_at,
                    status,
                    basic_info,
                    stages,
                    summary_metrics,
                    evaluation_metrics,
                    error,
                    created_at
                """,
                (
                    trace.trace_id,
                    trace.collection_id,
                    trace.raw_query,
                    trace.request_source,
                    trace.started_at,
                    trace.finished_at,
                    trace.status,
                    Jsonb(_json_compatible(trace.basic_info)),
                    Jsonb(_json_compatible(trace.stages)),
                    Jsonb(_json_compatible(trace.summary_metrics)),
                    Jsonb(_json_compatible(trace.evaluation_metrics)),
                    Jsonb(_json_compatible(trace.error)) if trace.error is not None else None,
                ),
            ).fetchone()
        return self._require_query_trace(row)

    def upsert_ingestion_trace(
        self,
        trace: IngestionTraceRecord,
    ) -> IngestionTraceRecord:
        """Insert or update one Ingestion Trace by its stable trace ID.

        Args:
            trace: Complete ingestion snapshot produced by TraceContext.

        Returns:
            Persisted immutable record including database ``created_at``.

        Side Effects:
            Creates the collection when absent and writes one ingestion row.
        """

        with self._pool.transaction() as connection:
            _ensure_collection(connection, trace.collection_id)
            row = connection.execute(
                """
                INSERT INTO rag_ingestion_traces (
                    trace_id,
                    collection_id,
                    source_uri,
                    source_hash,
                    started_at,
                    finished_at,
                    status,
                    basic_info,
                    stages,
                    summary_metrics,
                    evaluation_metrics,
                    error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (trace_id) DO UPDATE SET
                    collection_id = EXCLUDED.collection_id,
                    source_uri = EXCLUDED.source_uri,
                    source_hash = EXCLUDED.source_hash,
                    started_at = EXCLUDED.started_at,
                    finished_at = EXCLUDED.finished_at,
                    status = EXCLUDED.status,
                    basic_info = EXCLUDED.basic_info,
                    stages = EXCLUDED.stages,
                    summary_metrics = EXCLUDED.summary_metrics,
                    evaluation_metrics = EXCLUDED.evaluation_metrics,
                    error = EXCLUDED.error
                RETURNING
                    trace_id,
                    collection_id,
                    source_uri,
                    source_hash,
                    started_at,
                    finished_at,
                    status,
                    basic_info,
                    stages,
                    summary_metrics,
                    evaluation_metrics,
                    error,
                    created_at
                """,
                (
                    trace.trace_id,
                    trace.collection_id,
                    trace.source_uri,
                    trace.source_hash,
                    trace.started_at,
                    trace.finished_at,
                    trace.status,
                    Jsonb(_json_compatible(trace.basic_info)),
                    Jsonb(_json_compatible(trace.stages)),
                    Jsonb(_json_compatible(trace.summary_metrics)),
                    Jsonb(_json_compatible(trace.evaluation_metrics)),
                    Jsonb(_json_compatible(trace.error)) if trace.error is not None else None,
                ),
            ).fetchone()
        return self._require_ingestion_trace(row)

    def get_query_trace(self, trace_id: str) -> QueryTraceRecord | None:
        """Load one Query Trace by stable trace ID.

        Args:
            trace_id: Identifier emitted by Query Pipeline TraceContext.

        Returns:
            Immutable Query Trace record, or ``None`` when absent.

        Raises:
            DatabaseError: If PostgreSQL connection or query execution fails.
        """

        row = _read_rows(
            self._pool,
            operation="query_trace_get",
            query=f"""
            SELECT {self._query_trace_columns()}
            FROM rag_query_traces
            WHERE trace_id = %s
            """,
            params=(trace_id,),
            many=False,
        )
        return self._to_query_trace(row)

    def get_ingestion_trace(self, trace_id: str) -> IngestionTraceRecord | None:
        """Load one Ingestion Trace by stable trace ID.

        Args:
            trace_id: Identifier emitted by Ingestion Pipeline TraceContext.

        Returns:
            Immutable Ingestion Trace record, or ``None`` when absent.

        Raises:
            DatabaseError: If PostgreSQL connection or query execution fails.
        """

        row = _read_rows(
            self._pool,
            operation="ingestion_trace_get",
            query=f"""
            SELECT {self._ingestion_trace_columns()}
            FROM rag_ingestion_traces
            WHERE trace_id = %s
            """,
            params=(trace_id,),
            many=False,
        )
        return self._to_ingestion_trace(row)

    def list_query_traces(self, collection_id: str) -> list[QueryTraceRecord]:
        """List Query Traces newest-first for one collection.

        Args:
            collection_id: Collection selected by the Dashboard history filter.

        Returns:
            Query Traces ordered by descending start time and stable trace ID.

        Raises:
            DatabaseError: If PostgreSQL connection or query execution fails.
        """

        rows = _read_rows(
            self._pool,
            operation="query_trace_list",
            query=f"""
            SELECT {self._query_trace_columns()}
            FROM rag_query_traces
            WHERE collection_id = %s
            ORDER BY started_at DESC, trace_id ASC
            """,
            params=(collection_id,),
            many=True,
        )
        return [self._require_query_trace(row) for row in rows]

    def list_ingestion_traces(
        self,
        collection_id: str,
    ) -> list[IngestionTraceRecord]:
        """List Ingestion Traces newest-first for one collection.

        Args:
            collection_id: Collection selected by the Dashboard history filter.

        Returns:
            Ingestion Traces ordered by descending start time and trace ID.

        Raises:
            DatabaseError: If PostgreSQL connection or query execution fails.
        """

        rows = _read_rows(
            self._pool,
            operation="ingestion_trace_list",
            query=f"""
            SELECT {self._ingestion_trace_columns()}
            FROM rag_ingestion_traces
            WHERE collection_id = %s
            ORDER BY started_at DESC, trace_id ASC
            """,
            params=(collection_id,),
            many=True,
        )
        return [self._require_ingestion_trace(row) for row in rows]

    @staticmethod
    def _query_trace_columns() -> str:
        """Return the canonical Query Trace projection in dataclass order."""

        return """
            trace_id,
            collection_id,
            raw_query,
            request_source,
            started_at,
            finished_at,
            status,
            basic_info,
            stages,
            summary_metrics,
            evaluation_metrics,
            error,
            created_at
        """

    @staticmethod
    def _ingestion_trace_columns() -> str:
        """Return the canonical Ingestion Trace projection in dataclass order."""

        return """
            trace_id,
            collection_id,
            source_uri,
            source_hash,
            started_at,
            finished_at,
            status,
            basic_info,
            stages,
            summary_metrics,
            evaluation_metrics,
            error,
            created_at
        """

    @staticmethod
    def _to_query_trace(row: tuple[Any, ...] | None) -> QueryTraceRecord | None:
        """Convert one PostgreSQL row to a Query Trace record."""

        return QueryTraceRecord(*row) if row is not None else None

    @staticmethod
    def _require_query_trace(row: tuple[Any, ...] | None) -> QueryTraceRecord:
        """Convert a mandatory upsert/list row to a Query Trace record."""

        if row is None:
            raise RuntimeError("Query Trace operation returned no row")
        return QueryTraceRecord(*row)

    @staticmethod
    def _to_ingestion_trace(
        row: tuple[Any, ...] | None,
    ) -> IngestionTraceRecord | None:
        """Convert one PostgreSQL row to an Ingestion Trace record."""

        return IngestionTraceRecord(*row) if row is not None else None

    @staticmethod
    def _require_ingestion_trace(
        row: tuple[Any, ...] | None,
    ) -> IngestionTraceRecord:
        """Convert a mandatory upsert/list row to an Ingestion Trace record."""

        if row is None:
            raise RuntimeError("Ingestion Trace operation returned no row")
        return IngestionTraceRecord(*row)


class EvaluationRepository:
    """Persist evaluation runs and their independently queryable metrics."""

    def __init__(self, pool: PostgresPool) -> None:
        """Bind evaluation persistence to an application-managed pool.

        Args:
            pool: Open PostgreSQL pool used for all evaluation operations.
        """

        self._pool = pool

    def upsert_run(self, run: EvaluationRunRecord) -> EvaluationRunRecord:
        """Insert or update one evaluation run by stable Python ID.

        Args:
            run: Evaluator execution state and reproducibility metadata.

        Returns:
            Persisted run including its database creation timestamp.

        Side Effects:
            Creates the collection when absent and writes one evaluation run.
        """

        with self._pool.transaction() as connection:
            _ensure_collection(connection, run.collection_id)
            row = connection.execute(
                """
                INSERT INTO rag_evaluation_runs (
                    id,
                    collection_id,
                    evaluator,
                    dataset_name,
                    status,
                    started_at,
                    finished_at,
                    settings_snapshot,
                    summary,
                    error,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    collection_id = EXCLUDED.collection_id,
                    evaluator = EXCLUDED.evaluator,
                    dataset_name = EXCLUDED.dataset_name,
                    status = EXCLUDED.status,
                    started_at = EXCLUDED.started_at,
                    finished_at = EXCLUDED.finished_at,
                    settings_snapshot = EXCLUDED.settings_snapshot,
                    summary = EXCLUDED.summary,
                    error = EXCLUDED.error
                RETURNING
                    id,
                    collection_id,
                    evaluator,
                    dataset_name,
                    status,
                    started_at,
                    finished_at,
                    settings_snapshot,
                    summary,
                    error,
                    created_at
                """,
                (
                    run.id,
                    run.collection_id,
                    run.evaluator,
                    run.dataset_name,
                    run.status,
                    run.started_at,
                    run.finished_at,
                    Jsonb(_json_compatible(run.settings_snapshot)),
                    Jsonb(_json_compatible(run.summary)),
                    Jsonb(_json_compatible(run.error)) if run.error is not None else None,
                    run.created_at or run.started_at or datetime.now(UTC),
                ),
            ).fetchone()
        return self._require_run(row)

    def upsert_results(
        self,
        run_id: str,
        results: list[EvaluationResultRecord],
    ) -> list[EvaluationResultRecord]:
        """Upsert a metric batch while preserving caller order.

        Args:
            run_id: Existing evaluation run receiving every result.
            results: Metric records to insert or update.

        Returns:
            Persisted metric records in input order.

        Raises:
            ValueError: If any record references a different run, or duplicate
                metric names/IDs appear in the same batch.
        """

        persisted = list(results)
        if not persisted:
            return persisted
        if any(result.run_id != run_id for result in persisted):
            raise ValueError("Evaluation result run_id does not match batch run_id")
        result_ids = [result.id for result in persisted]
        metric_names = [result.metric_name for result in persisted]
        if len(set(result_ids)) != len(result_ids):
            raise ValueError("Evaluation result batch contains duplicate IDs")
        if len(set(metric_names)) != len(metric_names):
            raise ValueError("Evaluation result batch contains duplicate metric names")

        stored: list[EvaluationResultRecord] = []
        with self._pool.transaction() as connection:
            for result in persisted:
                row = connection.execute(
                    """
                    INSERT INTO rag_evaluation_results (
                        id,
                        run_id,
                        metric_name,
                        metric_value,
                        details
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, metric_name) DO UPDATE SET
                        id = EXCLUDED.id,
                        metric_value = EXCLUDED.metric_value,
                        details = EXCLUDED.details
                    RETURNING
                        id,
                        run_id,
                        metric_name,
                        metric_value,
                        details,
                        created_at
                    """,
                    (
                        result.id,
                        run_id,
                        result.metric_name,
                        result.metric_value,
                        Jsonb(_json_compatible(result.details)),
                    ),
                ).fetchone()
                stored.append(self._require_result(row))
        return stored

    def get_run(self, run_id: str) -> EvaluationRunRecord | None:
        """Load one evaluation run by stable ID.

        Args:
            run_id: Python-generated evaluation run identifier.

        Returns:
            Immutable evaluation run, or ``None`` when absent.

        Raises:
            DatabaseError: If PostgreSQL connection or query execution fails.
        """

        row = _read_rows(
            self._pool,
            operation="evaluation_run_get",
            query=f"""
            SELECT {self._run_columns()}
            FROM rag_evaluation_runs
            WHERE id = %s
            """,
            params=(run_id,),
            many=False,
        )
        return self._to_run(row)

    def list_runs(self, collection_id: str) -> list[EvaluationRunRecord]:
        """List evaluation runs newest-first for one collection.

        Args:
            collection_id: Collection selected by the evaluation Dashboard.

        Returns:
            Runs ordered by descending creation time and stable run ID.

        Raises:
            DatabaseError: If PostgreSQL connection or query execution fails.
        """

        rows = _read_rows(
            self._pool,
            operation="evaluation_run_list",
            query=f"""
            SELECT {self._run_columns()}
            FROM rag_evaluation_runs
            WHERE collection_id = %s
            ORDER BY created_at DESC, id ASC
            """,
            params=(collection_id,),
            many=True,
        )
        return [self._require_run(row) for row in rows]

    def list_results(self, run_id: str) -> list[EvaluationResultRecord]:
        """List one run's metrics in stable metric-name order.

        Args:
            run_id: Parent evaluation run identifier.

        Returns:
            Metric records ordered by metric name and stable result ID.

        Raises:
            DatabaseError: If PostgreSQL connection or query execution fails.
        """

        rows = _read_rows(
            self._pool,
            operation="evaluation_result_list",
            query="""
            SELECT
                id,
                run_id,
                metric_name,
                metric_value,
                details,
                created_at
            FROM rag_evaluation_results
            WHERE run_id = %s
            ORDER BY metric_name ASC, id ASC
            """,
            params=(run_id,),
            many=True,
        )
        return [self._require_result(row) for row in rows]

    @staticmethod
    def _run_columns() -> str:
        """Return the canonical evaluation-run projection in dataclass order."""

        return """
            id,
            collection_id,
            evaluator,
            dataset_name,
            status,
            started_at,
            finished_at,
            settings_snapshot,
            summary,
            error,
            created_at
        """

    @staticmethod
    def _to_run(row: tuple[Any, ...] | None) -> EvaluationRunRecord | None:
        """Convert one PostgreSQL row to an evaluation-run record."""

        return EvaluationRunRecord(*row) if row is not None else None

    @staticmethod
    def _require_run(row: tuple[Any, ...] | None) -> EvaluationRunRecord:
        """Convert a mandatory upsert/list row to an evaluation-run record."""

        if row is None:
            raise RuntimeError("Evaluation run operation returned no row")
        return EvaluationRunRecord(*row)

    @staticmethod
    def _require_result(row: tuple[Any, ...] | None) -> EvaluationResultRecord:
        """Convert a mandatory upsert/list row to an evaluation metric record."""

        if row is None:
            raise RuntimeError("Evaluation result operation returned no row")
        return EvaluationResultRecord(*row)
