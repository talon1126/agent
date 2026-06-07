"""Orchestrate Dense Route, Sparse Route, and RRF fusion for retrieval.

``HybridSearch`` is the first online query component that coordinates multiple
retrieval routes. It accepts an already validated ``ProcessedQuery``, executes
Dense and Sparse routes independently, fuses all available candidates with RRF,
and degrades to one route when the other fails.

This module deliberately does not perform query preprocessing, metadata
filtering, reranking, response construction, provider creation, or persistence.
Those concerns belong to neighboring query-engine stages and composition roots.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.core.config import RagSettings
from src.core.errors import RetrievalError
from src.core.query_engine.dense_route import DenseRoute
from src.core.query_engine.fusion import reciprocal_rank_fusion
from src.core.query_engine.query_processor import ProcessedQuery
from src.core.query_engine.sparse_route import SparseRoute
from src.core.types import RetrievalResult


class HybridTraceContext(Protocol):
    """Describe the minimal trace method used by HybridSearch."""

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
        """Record one completed HybridSearch orchestration stage.

        Args:
            stage: Stable query stage name.
            method: Trace-visible orchestration method.
            provider: Concrete orchestration implementation identifier.
            duration_ms: End-to-end hybrid orchestration duration.
            candidate_count: Number of fused retrieval candidates.
            status: ``success``, ``degraded``, or ``failed``.
            details: Trace-safe route counts and fallback details.

        Returns:
            Trace implementations may return any value; HybridSearch ignores it.
        """


class HybridSearchResult(BaseModel):
    """Carry route outputs and fused candidates from HybridSearch.

    The route result lists are intentionally preserved for later trace,
    debugging, evaluation, and Dashboard comparison. The ``results`` list is
    the fused candidate stream that downstream filtering and reranking consume.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    dense_results: list[RetrievalResult] = Field(default_factory=list)
    sparse_results: list[RetrievalResult] = Field(default_factory=list)
    results: list[RetrievalResult] = Field(default_factory=list)
    fallback_used: bool = False
    fallback_reasons: dict[str, str] = Field(default_factory=dict)


class HybridSearch:
    """Coordinate Dense, Sparse, and RRF stages with single-route fallback."""

    def __init__(
        self,
        *,
        settings: RagSettings,
        dense_route: DenseRoute,
        sparse_route: SparseRoute,
    ) -> None:
        """Configure HybridSearch with retrieval settings and route instances.

        Args:
            settings: Validated settings providing ``fusion_top_k`` and
                ``rrf_k``.
            dense_route: Semantic route implementation.
            sparse_route: Keyword route implementation.
        """

        self._settings = settings
        self._dense_route = dense_route
        self._sparse_route = sparse_route

    def search(
        self,
        query: ProcessedQuery,
        *,
        trace_context: HybridTraceContext | None = None,
    ) -> HybridSearchResult:
        """Run both retrieval routes and fuse all available candidates.

        Args:
            query: Query object already produced by ``QueryProcessor``.
            trace_context: Optional low-intrusion trace recorder.

        Returns:
            Route outputs, fused results, and fallback diagnostics.

        Raises:
            RetrievalError: If both Dense and Sparse routes fail, or if RRF
                fusion fails after at least one route returned candidates.

        Side Effects:
            Calls the configured route instances. When a trace context exists,
            records exactly one hybrid stage after route/fusion work.
        """

        started_at = perf_counter()
        dense_results: list[RetrievalResult] = []
        sparse_results: list[RetrievalResult] = []
        fallback_reasons: dict[str, str] = {}

        try:
            dense_results = self._dense_route.search(query, trace_context=trace_context)
        except RetrievalError as error:
            fallback_reasons["dense"] = str(error)

        try:
            sparse_results = self._sparse_route.search(query, trace_context=trace_context)
        except RetrievalError as error:
            fallback_reasons["sparse"] = str(error)

        if "dense" in fallback_reasons and "sparse" in fallback_reasons:
            self._record_trace(
                trace_context,
                started_at=started_at,
                candidate_count=0,
                status="failed",
                dense_results=dense_results,
                sparse_results=sparse_results,
                fallback_reasons=fallback_reasons,
            )
            raise RetrievalError(
                "Hybrid search failed",
                context={
                    "stage": "hybrid",
                    "failed_routes": sorted(fallback_reasons),
                },
            )

        try:
            fused_results = reciprocal_rank_fusion(
                dense_results,
                sparse_results,
                top_k=self._settings.retrieval.fusion_top_k,
                rrf_k=self._settings.retrieval.rrf_k,
            )
        except Exception as error:
            self._record_trace(
                trace_context,
                started_at=started_at,
                candidate_count=0,
                status="failed",
                dense_results=dense_results,
                sparse_results=sparse_results,
                fallback_reasons=fallback_reasons,
            )
            raise RetrievalError(
                "Hybrid fusion failed",
                context={"stage": "hybrid", "operation": "fusion"},
                cause=error,
            ) from error

        fallback_used = bool(fallback_reasons)
        self._record_trace(
            trace_context,
            started_at=started_at,
            candidate_count=len(fused_results),
            status="degraded" if fallback_used else "success",
            dense_results=dense_results,
            sparse_results=sparse_results,
            fallback_reasons=fallback_reasons,
        )
        return HybridSearchResult(
            dense_results=dense_results,
            sparse_results=sparse_results,
            results=fused_results,
            fallback_used=fallback_used,
            fallback_reasons=fallback_reasons,
        )

    @staticmethod
    def _record_trace(
        trace_context: HybridTraceContext | None,
        *,
        started_at: float,
        candidate_count: int,
        status: str,
        dense_results: list[RetrievalResult],
        sparse_results: list[RetrievalResult],
        fallback_reasons: dict[str, str],
    ) -> None:
        """Write one trace stage when observability is available.

        Args:
            trace_context: Optional TraceContext-compatible recorder.
            started_at: ``perf_counter`` value captured before route work.
            candidate_count: Number of fused candidates.
            status: Hybrid orchestration completion status.
            dense_results: Dense route candidates that survived route errors.
            sparse_results: Sparse route candidates that survived route errors.
            fallback_reasons: Mapping of failed route name to readable reason.
        """

        if trace_context is None:
            return
        try:
            trace_context.record_stage(
                stage="hybrid",
                method="rrf",
                provider="HybridSearch",
                duration_ms=(perf_counter() - started_at) * 1000,
                candidate_count=candidate_count,
                status=status,
                details={
                    "dense_candidate_count": len(dense_results),
                    "sparse_candidate_count": len(sparse_results),
                    "failed_routes": sorted(fallback_reasons),
                    "fallback_reasons": dict(fallback_reasons),
                },
            )
        except Exception:
            # Hybrid trace output is diagnostic only. Trace failures must not
            # replace route results, fallback behavior, or original errors.
            return
