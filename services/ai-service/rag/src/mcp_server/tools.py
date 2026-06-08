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
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from src.core.config import DatabaseSettings, RagSettings, load_settings
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


PoolFactory = Callable[[DatabaseSettings], PostgresPool]
SchemaInitializer = Callable[[PostgresPool], None]
RuntimeBuilder = Callable[[RagSettings, PostgresPool, bool], QueryRuntimeLike]
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


def _default_runtime_builder(
    settings: RagSettings,
    pool: PostgresPool,
    no_rerank: bool,
) -> QueryRuntimeLike:
    """Create the production QueryRuntime using the Phase D composition path."""

    from src.scripts.query import _build_runtime

    return _build_runtime(settings, pool, no_rerank)
