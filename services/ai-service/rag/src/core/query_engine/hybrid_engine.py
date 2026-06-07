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

from collections.abc import Mapping
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

_FILTER_KEYS = {
    "collection",
    "doc_type",
    "source_type",
    "document_status",
    "lifecycle_status",
    "permission",
    "permissions",
    "include_deleted",
}


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


class CandidateFilterReport(BaseModel):
    """Describe metadata filtering output and rejection diagnostics."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    results: list[RetrievalResult] = Field(default_factory=list)
    before_count: int = Field(ge=0)
    after_count: int = Field(ge=0)
    rejected_counts: dict[str, int] = Field(default_factory=dict)
    rejected_chunk_ids: dict[str, list[str]] = Field(default_factory=dict)


class CandidateFilter:
    """Apply exact metadata constraints before candidates enter reranking."""

    def apply(
        self,
        candidates: list[RetrievalResult],
        filters: Mapping[str, Any] | None,
    ) -> CandidateFilterReport:
        """Filter fused candidates using rerank-safe metadata fields.

        Args:
            candidates: Fused RRF candidates in current rank order.
            filters: Optional query or CLI parameters. Supported keys are
                ``collection``, ``doc_type``, ``source_type``,
                ``document_status``, ``lifecycle_status``, ``permission``,
                ``permissions``, and ``include_deleted``.

        Returns:
            Filtered candidates plus before/after counts and rejection details.

        Raises:
            RetrievalError: If a filter key is unsupported.
        """

        normalized_filters = dict(filters or {})
        unknown_keys = sorted(set(normalized_filters) - _FILTER_KEYS)
        if unknown_keys:
            raise RetrievalError(
                "Unsupported metadata filter",
                context={"unsupported_filters": unknown_keys},
            )

        rejected_counts: dict[str, int] = {}
        rejected_chunk_ids: dict[str, list[str]] = {}
        results: list[RetrievalResult] = []
        include_deleted = normalized_filters.get("include_deleted", False)
        if not isinstance(include_deleted, bool):
            raise RetrievalError(
                "include_deleted must be a boolean",
                context={"received_type": type(include_deleted).__name__},
            )
        for candidate in candidates:
            reason = self._first_rejection_reason(
                candidate,
                normalized_filters,
                include_deleted=include_deleted,
            )
            if reason is None:
                results.append(candidate)
                continue
            rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
            rejected_chunk_ids.setdefault(reason, []).append(candidate.chunk_id)

        return CandidateFilterReport(
            results=results,
            before_count=len(candidates),
            after_count=len(results),
            rejected_counts=rejected_counts,
            rejected_chunk_ids=rejected_chunk_ids,
        )

    def _first_rejection_reason(
        self,
        candidate: RetrievalResult,
        filters: dict[str, Any],
        *,
        include_deleted: bool,
    ) -> str | None:
        """Return the first metadata field that rejects a candidate.

        Args:
            candidate: Candidate being checked.
            filters: Normalized filter mapping.
            include_deleted: Whether deleted lifecycle candidates are allowed.

        Returns:
            Rejection reason or ``None`` when the candidate matches.
        """

        metadata = candidate.metadata
        if (
            not include_deleted
            and metadata.get("lifecycle_status") == "deleted"
        ):
            return "lifecycle_status"
        for key in (
            "collection",
            "doc_type",
            "source_type",
            "document_status",
            "lifecycle_status",
        ):
            if key in filters and not _matches_filter(metadata.get(key), filters[key]):
                return key
        if "permission" in filters and not _has_permission(
            metadata.get("permissions"),
            filters["permission"],
        ):
            return "permission"
        if "permissions" in filters and not _has_all_permissions(
            metadata.get("permissions"),
            filters["permissions"],
        ):
            return "permission"
        return None


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
    filter_report: CandidateFilterReport | None = None


class HybridSearch:
    """Coordinate Dense, Sparse, and RRF stages with single-route fallback."""

    def __init__(
        self,
        *,
        settings: RagSettings,
        dense_route: DenseRoute,
        sparse_route: SparseRoute,
        candidate_filter: CandidateFilter | None = None,
    ) -> None:
        """Configure HybridSearch with retrieval settings and route instances.

        Args:
            settings: Validated settings providing ``fusion_top_k`` and
                ``rrf_k``.
            dense_route: Semantic route implementation.
            sparse_route: Keyword route implementation.
            candidate_filter: Optional metadata filter strategy. ``None`` uses
                the default exact-match filter.
        """

        self._settings = settings
        self._dense_route = dense_route
        self._sparse_route = sparse_route
        self._candidate_filter = candidate_filter or CandidateFilter()

    def search(
        self,
        query: ProcessedQuery,
        *,
        filters: Mapping[str, Any] | None = None,
        trace_context: HybridTraceContext | None = None,
    ) -> HybridSearchResult:
        """Run both retrieval routes and fuse all available candidates.

        Args:
            query: Query object already produced by ``QueryProcessor``.
            filters: Optional metadata constraints applied after fusion and
                before rerank.
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

        filter_report = self.apply_metadata_filter(
            fused_results,
            filters=filters,
            trace_context=trace_context,
        )
        fallback_used = bool(fallback_reasons)
        self._record_trace(
            trace_context,
            started_at=started_at,
            candidate_count=len(filter_report.results),
            status="degraded" if fallback_used else "success",
            dense_results=dense_results,
            sparse_results=sparse_results,
            fallback_reasons=fallback_reasons,
        )
        return HybridSearchResult(
            dense_results=dense_results,
            sparse_results=sparse_results,
            results=filter_report.results,
            fallback_used=fallback_used,
            fallback_reasons=fallback_reasons,
            filter_report=filter_report,
        )

    def apply_metadata_filter(
        self,
        candidates: list[RetrievalResult],
        *,
        filters: Mapping[str, Any] | None = None,
        trace_context: HybridTraceContext | None = None,
    ) -> CandidateFilterReport:
        """Apply reusable metadata filtering to fused candidates.

        Args:
            candidates: RRF-fused candidates in rank order.
            filters: Optional query or CLI metadata constraints.
            trace_context: Optional trace recorder for the filter stage.

        Returns:
            Filtered candidates and rejection diagnostics.

        Raises:
            RetrievalError: If filter keys are unsupported.
        """

        started_at = perf_counter()
        report = self._candidate_filter.apply(candidates, filters)
        self._record_filter_trace(
            trace_context,
            started_at=started_at,
            filters=dict(filters or {}),
            report=report,
        )
        return report

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

    @staticmethod
    def _record_filter_trace(
        trace_context: HybridTraceContext | None,
        *,
        started_at: float,
        filters: dict[str, Any],
        report: CandidateFilterReport,
    ) -> None:
        """Write one filter trace stage when observability is available.

        Args:
            trace_context: Optional TraceContext-compatible recorder.
            started_at: ``perf_counter`` value captured before filtering.
            filters: Filter parameters supplied by query or CLI callers.
            report: Metadata filtering report.
        """

        if trace_context is None:
            return
        try:
            trace_context.record_stage(
                stage="filter",
                method="metadata",
                provider="CandidateFilter",
                duration_ms=(perf_counter() - started_at) * 1000,
                candidate_count=report.after_count,
                status="success",
                details={
                    "filters": filters,
                    "before_count": report.before_count,
                    "after_count": report.after_count,
                    "rejected_counts": dict(report.rejected_counts),
                    "rejected_chunk_ids": {
                        reason: list(chunk_ids)
                        for reason, chunk_ids in report.rejected_chunk_ids.items()
                    },
                },
            )
        except Exception:
            # Filter trace output is diagnostic only. Trace failures must not
            # replace filtered results or original filtering errors.
            return


def _matches_filter(metadata_value: Any, expected: Any) -> bool:
    """Return whether a metadata value satisfies an exact-match filter.

    Args:
        metadata_value: Value stored in retrieval result metadata.
        expected: Filter value, either one allowed value or a collection of
            allowed values.

    Returns:
        ``True`` when the metadata value matches the filter.
    """

    if isinstance(expected, (list, tuple, set, frozenset)):
        return metadata_value in expected
    return metadata_value == expected


def _has_permission(metadata_permissions: Any, required_permission: Any) -> bool:
    """Return whether candidate permissions contain one required permission."""

    if isinstance(metadata_permissions, str):
        available = {metadata_permissions}
    elif isinstance(metadata_permissions, (list, tuple, set, frozenset)):
        available = {str(permission) for permission in metadata_permissions}
    else:
        available = set()
    return str(required_permission) in available


def _has_all_permissions(metadata_permissions: Any, required_permissions: Any) -> bool:
    """Return whether candidate permissions contain all required permissions."""

    if isinstance(required_permissions, str):
        return _has_permission(metadata_permissions, required_permissions)
    if not isinstance(required_permissions, (list, tuple, set, frozenset)):
        return False
    return all(
        _has_permission(metadata_permissions, permission)
        for permission in required_permissions
    )
