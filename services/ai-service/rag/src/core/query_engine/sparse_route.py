"""Execute the BM25 retrieval branch of the online query pipeline.

``SparseRoute`` accepts a raw user question or an existing ``ProcessedQuery``.
It converts raw input through ``QueryProcessor``, sends the immutable keyword
snapshot to a BM25-compatible indexer, then hydrates returned ``chunk_id``
values through ``BaseVectorStore.get_by_ids()``.

The route intentionally does not perform dense search, RRF fusion, metadata
filtering, reranking, response construction, or BM25 persistence. Its only
business responsibility is turning sparse keyword candidates into validated
``RetrievalResult`` objects that later retrieval stages can fuse and rerank.
"""

from __future__ import annotations

from copy import deepcopy
from time import perf_counter
from typing import Any, Protocol

from src.core.config import RagSettings
from src.core.errors import RetrievalError
from src.core.query_engine.query_processor import ProcessedQuery, QueryProcessor
from src.core.types import Chunk, RetrievalResult
from src.libs.vector_store.base_vector_store import BaseVectorStore


class BM25CandidateLike(Protocol):
    """Describe the BM25 candidate fields consumed by Sparse Route."""

    chunk_id: str
    score: float


class BM25IndexerLike(Protocol):
    """Describe the minimal sparse indexer query contract."""

    def query(
        self,
        keywords: list[str] | str,
        *,
        top_k: int,
        collection: str | None = None,
    ) -> list[BM25CandidateLike]:
        """Return ranked sparse candidates for normalized query keywords.

        Args:
            keywords: Ordered unique query keywords from ``ProcessedQuery``.
            top_k: Maximum number of sparse candidates to return.
            collection: Target knowledge collection. In-memory indexes may
                ignore it; persistent indexes must use it to isolate corpus
                statistics and postings.

        Returns:
            BM25 candidates containing chunk IDs and native sparse scores.
        """


class SparseTraceContext(Protocol):
    """Describe the minimal trace method used by Sparse Route."""

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
        """Record one completed Sparse Route stage.

        Args:
            stage: Stable query stage name.
            method: Trace-visible retrieval method.
            provider: Concrete sparse indexer implementation identifier.
            duration_ms: End-to-end route duration in milliseconds.
            candidate_count: Number of hydrated retrieval candidates.
            status: ``success``, ``skipped``, or ``failed``.
            details: Trace-safe route parameters or failure context.

        Returns:
            Trace implementations may return any value; Sparse Route ignores it.
        """


class SparseRoute:
    """Retrieve keyword candidates with BM25 and hydrate chunk payloads."""

    def __init__(
        self,
        *,
        settings: RagSettings,
        query_processor: QueryProcessor,
        bm25_indexer: BM25IndexerLike,
        vector_store: BaseVectorStore,
    ) -> None:
        """Configure Sparse Route with existing project abstractions.

        Args:
            settings: Validated settings providing ``sparse_top_k``.
            query_processor: Processor used only when callers supply raw text.
            bm25_indexer: BM25-compatible keyword index queried by this route.
            vector_store: Store used only for ordered chunk hydration by ID.
        """

        self._settings = settings
        self._query_processor = query_processor
        self._bm25_indexer = bm25_indexer
        self._vector_store = vector_store

    def search(
        self,
        query: str | ProcessedQuery,
        *,
        top_k: int | None = None,
        trace_context: SparseTraceContext | None = None,
    ) -> list[RetrievalResult]:
        """Run BM25 keyword recall followed by chunk hydration.

        Args:
            query: Raw user text or an immutable query already produced by
                ``QueryProcessor``.
            top_k: Optional Sparse candidate limit. When omitted,
                ``settings.retrieval.sparse_top_k`` is used.
            trace_context: Optional low-intrusion trace recorder.

        Returns:
            Validated retrieval candidates in BM25 ranking order. BM25 IDs that
            no longer exist in chunk storage are skipped and recorded in trace
            details.

        Raises:
            RetrievalError: If ``top_k`` is invalid, BM25 query fails, chunk
                hydration fails, or hydrated chunks cannot be converted to
                ``RetrievalResult``.

        Side Effects:
            Calls the configured sparse indexer and vector-store lookup. When a
            trace context exists, records exactly one sparse stage after query
            processing succeeds.
        """

        processed_query = (
            query if isinstance(query, ProcessedQuery) else self._query_processor.process(query)
        )
        candidate_limit = (
            self._settings.retrieval.sparse_top_k if top_k is None else top_k
        )
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int):
            raise RetrievalError(
                "Sparse top_k must be an integer",
                context={"received_type": type(candidate_limit).__name__},
            )
        if candidate_limit <= 0:
            raise RetrievalError("Sparse top_k must be greater than zero")

        keywords = list(processed_query.keywords)
        started_at = perf_counter()
        provider = type(self._bm25_indexer).__name__
        if not keywords:
            self._record_trace(
                trace_context,
                started_at=started_at,
                provider=provider,
                candidate_count=0,
                status="skipped",
                details={
                    "top_k": candidate_limit,
                    "keyword_count": 0,
                    "reason": "empty_keywords",
                    "missing_chunk_ids": [],
                    "chunk_ids": [],
                },
            )
            return []

        try:
            candidates = self._bm25_indexer.query(
                keywords,
                top_k=candidate_limit,
                collection=processed_query.collection,
            )
            chunk_ids = [candidate.chunk_id for candidate in candidates]
            score_by_chunk_id = {
                candidate.chunk_id: candidate.score
                for candidate in candidates
            }
        except Exception as error:
            self._record_trace(
                trace_context,
                started_at=started_at,
                provider=provider,
                candidate_count=0,
                status="failed",
                details={
                    "top_k": candidate_limit,
                    "keyword_count": len(keywords),
                    "operation": "bm25_query",
                    "error_type": type(error).__name__,
                },
            )
            raise RetrievalError(
                "Sparse BM25 query failed",
                context={
                    "stage": "sparse",
                    "operation": "bm25_query",
                    "top_k": candidate_limit,
                },
                cause=error,
            ) from error

        if not chunk_ids:
            self._record_trace(
                trace_context,
                started_at=started_at,
                provider=provider,
                candidate_count=0,
                status="success",
                details={
                    "top_k": candidate_limit,
                    "keyword_count": len(keywords),
                    "bm25_candidate_count": 0,
                    "missing_chunk_ids": [],
                    "chunk_ids": [],
                },
            )
            return []

        try:
            chunks = self._vector_store.get_by_ids(chunk_ids)
            results = self._to_retrieval_results(
                chunks,
                score_by_chunk_id=score_by_chunk_id,
            )
        except Exception as error:
            self._record_trace(
                trace_context,
                started_at=started_at,
                provider=provider,
                candidate_count=0,
                status="failed",
                details={
                    "top_k": candidate_limit,
                    "keyword_count": len(keywords),
                    "bm25_candidate_count": len(chunk_ids),
                    "operation": "chunk_hydration",
                    "error_type": type(error).__name__,
                    "chunk_ids": chunk_ids,
                },
            )
            raise RetrievalError(
                "Sparse chunk hydration failed",
                context={
                    "stage": "sparse",
                    "operation": "chunk_hydration",
                    "candidate_count": len(chunk_ids),
                },
                cause=error,
            ) from error

        hydrated_ids = {result.chunk_id for result in results}
        missing_chunk_ids = [
            chunk_id for chunk_id in chunk_ids if chunk_id not in hydrated_ids
        ]
        self._record_trace(
            trace_context,
            started_at=started_at,
            provider=provider,
            candidate_count=len(results),
            status="success",
            details={
                "top_k": candidate_limit,
                "keyword_count": len(keywords),
                "bm25_candidate_count": len(chunk_ids),
                "missing_chunk_ids": missing_chunk_ids,
                "chunk_ids": [result.chunk_id for result in results],
            },
        )
        return results

    @staticmethod
    def _to_retrieval_results(
        chunks: list[Chunk],
        *,
        score_by_chunk_id: dict[str, float],
    ) -> list[RetrievalResult]:
        """Convert hydrated chunks into Sparse retrieval results.

        Args:
            chunks: Chunks returned by ``BaseVectorStore.get_by_ids()`` in the
                requested BM25 candidate order.
            score_by_chunk_id: BM25 native score lookup keyed by chunk ID.

        Returns:
            Retrieval results with BM25 scores and copied metadata containing
            the citation fields needed by response construction.
        """

        results: list[RetrievalResult] = []
        for chunk in chunks:
            if chunk.id not in score_by_chunk_id:
                continue
            metadata = deepcopy(chunk.metadata)
            results.append(
                RetrievalResult(
                    chunk_id=chunk.id,
                    text=chunk.text,
                    score=score_by_chunk_id[chunk.id],
                    metadata=metadata,
                )
            )
        return results

    @staticmethod
    def _record_trace(
        trace_context: SparseTraceContext | None,
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
            started_at: ``perf_counter`` value captured before route work.
            provider: Concrete BM25 indexer class name.
            candidate_count: Number of hydrated candidates.
            status: Stage completion status.
            details: Trace-safe parameters or failure diagnostics.
        """

        if trace_context is None:
            return
        try:
            trace_context.record_stage(
                stage="sparse",
                method="bm25",
                provider=provider,
                duration_ms=(perf_counter() - started_at) * 1000,
                candidate_count=candidate_count,
                status=status,
                details=details,
            )
        except Exception:
            # Observability remains best-effort so tracing outages do not
            # replace successful retrieval results or original route failures.
            return
