"""Execute the semantic retrieval branch of the online query pipeline.

``DenseRoute`` accepts either a raw user question or an existing
``ProcessedQuery``. It delegates raw input to ``QueryProcessor``, creates one
query embedding through the provider-independent ``BaseEmbedding`` contract,
and sends that vector to ``BaseVectorStore.search()``.

The route does not perform RRF fusion, metadata filtering, reranking, response
construction, or provider creation. Those concerns belong to later query-engine
stages and composition roots. Optional trace injection records one dense stage
without making observability a mandatory runtime dependency.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

from src.core.config import RagSettings
from src.core.errors import RetrievalError
from src.core.query_engine.query_processor import ProcessedQuery, QueryProcessor
from src.core.types import RetrievalResult
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.vector_store.base_vector_store import BaseVectorStore


class DenseTraceContext(Protocol):
    """Describe the minimal trace method used by Dense Route."""

    def record_stage(
        self,
        *,
        stage: str,
        method: str,
        provider: str,
        duration_ms: float,
        candidate_count: int,
        status: str,
        details: dict[str, Any],
    ) -> Any:
        """Record one completed Dense Route stage.

        Args:
            stage: Stable query stage name.
            method: Trace-visible retrieval method.
            provider: Concrete vector-store implementation identifier.
            duration_ms: End-to-end route duration in milliseconds.
            candidate_count: Number of validated retrieval candidates.
            status: ``success`` or ``failed``.
            details: Trace-safe route parameters or failure context.

        Returns:
            Trace implementations may return any value; Dense Route ignores it.
        """


class DenseRoute:
    """Embed one processed query and retrieve semantic chunk candidates."""

    def __init__(
        self,
        *,
        settings: RagSettings,
        query_processor: QueryProcessor,
        embedding: BaseEmbedding,
        vector_store: BaseVectorStore,
    ) -> None:
        """Configure Dense Route with existing project abstractions.

        Args:
            settings: Validated settings providing ``dense_top_k``.
            query_processor: Processor used only when callers supply raw text.
            embedding: Provider-independent query embedding client.
            vector_store: Provider-independent semantic vector store.
        """

        self._settings = settings
        self._query_processor = query_processor
        self._embedding = embedding
        self._vector_store = vector_store

    def search(
        self,
        query: str | ProcessedQuery,
        *,
        top_k: int | None = None,
        trace_context: DenseTraceContext | None = None,
    ) -> list[RetrievalResult]:
        """Run Query Embedding followed by vector-store Top-k retrieval.

        Args:
            query: Raw user text or an immutable query already produced by
                ``QueryProcessor``.
            top_k: Optional Dense candidate limit. When omitted,
                ``settings.retrieval.dense_top_k`` is used instead of the final
                result count stored in ``ProcessedQuery.top_k``.
            trace_context: Optional low-intrusion trace recorder.

        Returns:
            Validated retrieval candidates in vector-store ranking order.

        Raises:
            RetrievalError: If ``top_k`` is invalid, query processing fails,
                embedding fails, vector search fails, or a provider returns an
                invalid retrieval result.

        Side Effects:
            Calls the configured embedding and vector-store providers. When a
            trace context exists, records exactly one dense stage for provider
            success or failure after query processing succeeds.
        """

        processed_query = (
            query if isinstance(query, ProcessedQuery) else self._query_processor.process(query)
        )
        candidate_limit = (
            self._settings.retrieval.dense_top_k if top_k is None else top_k
        )
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int):
            raise RetrievalError(
                "Dense top_k must be an integer",
                context={"received_type": type(candidate_limit).__name__},
            )
        if candidate_limit <= 0:
            raise RetrievalError("Dense top_k must be greater than zero")

        started_at = perf_counter()
        provider = type(self._vector_store).__name__
        try:
            query_vector = self._embedding.embed(processed_query.normalized_query)
        except Exception as error:
            self._record_trace(
                trace_context,
                started_at=started_at,
                provider=provider,
                candidate_count=0,
                status="failed",
                details={
                    "top_k": candidate_limit,
                    "operation": "query_embedding",
                    "error_type": type(error).__name__,
                },
            )
            raise RetrievalError(
                "Dense query embedding failed",
                context={
                    "stage": "dense",
                    "operation": "query_embedding",
                },
                cause=error,
            ) from error

        try:
            provider_results = self._vector_store.search(
                query_vector,
                top_k=candidate_limit,
            )
            results = [
                RetrievalResult.model_validate(result)
                for result in provider_results
            ]
        except Exception as error:
            self._record_trace(
                trace_context,
                started_at=started_at,
                provider=provider,
                candidate_count=0,
                status="failed",
                details={
                    "top_k": candidate_limit,
                    "operation": "vector_search",
                    "error_type": type(error).__name__,
                },
            )
            raise RetrievalError(
                "Dense vector search failed",
                context={
                    "stage": "dense",
                    "operation": "vector_search",
                    "top_k": candidate_limit,
                },
                cause=error,
            ) from error

        self._record_trace(
            trace_context,
            started_at=started_at,
            provider=provider,
            candidate_count=len(results),
            status="success",
            details={
                "top_k": candidate_limit,
                "chunk_ids": [result.chunk_id for result in results],
            },
        )
        return results

    @staticmethod
    def _record_trace(
        trace_context: DenseTraceContext | None,
        *,
        started_at: float,
        provider: str,
        candidate_count: int,
        status: str,
        details: dict[str, Any],
    ) -> None:
        """Write one trace stage when observability is available.

        Args:
            trace_context: Optional TraceContext-compatible recorder.
            started_at: ``perf_counter`` value captured before provider work.
            provider: Concrete vector-store class name.
            candidate_count: Number of validated candidates.
            status: Stage completion status.
            details: Trace-safe parameters or failure diagnostics.
        """

        if trace_context is None:
            return
        try:
            trace_context.record_stage(
                stage="dense",
                method="vector_search",
                provider=provider,
                duration_ms=(perf_counter() - started_at) * 1000,
                candidate_count=candidate_count,
                status=status,
                details=details,
            )
        except Exception:
            # Observability is intentionally best-effort at this boundary.
            # Trace sink failures must not replace provider results or the
            # original RetrievalError raised by the Dense Route.
            return
