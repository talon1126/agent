"""Coordinate async retrieval across one or more knowledge collections.

The controller belongs to the core query layer. It receives an already processed
query plus routed collections from ``IntentRouter`` or MCP callers, runs one
per-collection retrieval/rerank sub-chain concurrently, and merges the resulting
candidates before any final Self-RAG or Response Builder work happens.

This module deliberately does not decide which collections are relevant, create
provider clients, run Self-RAG judges, or build public responses. Those remain
runtime/composition concerns so multi-collection retrieval can be reused by CLI,
MCP, and evaluation paths without duplicating final post-processing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from src.core.query_engine.query_processor import ProcessedQuery
from src.core.types import RetrievalResult


class ParallelTraceContext(Protocol):
    """Describe the optional trace sink used by async parallel retrieval."""

    def record_stage(self, *args: Any, **kwargs: Any) -> Any:
        """Record one structured trace stage."""


@dataclass(frozen=True, slots=True)
class AsyncCollectionRetrievalResult:
    """Capture one collection sub-chain output before global merge.

    Args:
        collection: Collection ID searched by this sub-chain.
        results: Final per-collection candidates after optional rerank.
        dense_results: Dense route candidates for trace counts.
        sparse_results: BM25 route candidates for trace counts.
        fused_results: RRF candidates before metadata filter.
        filtered_results: Candidates after metadata filter and before rerank.
        rerank_results: Candidates after rerank, or filtered candidates when
            rerank is disabled or unavailable.
        fallback_used: Whether the sub-chain degraded, for example by using one
            retrieval route or rerank fallback.
        rerank_applied: Whether a reranker was actually invoked.
        rerank_fallback_used: Whether rerank specifically fell back to the
            pre-rerank candidate order.
        duration_ms: End-to-end sub-chain duration in milliseconds.
    """

    collection: str
    results: list[RetrievalResult]
    dense_results: list[RetrievalResult]
    sparse_results: list[RetrievalResult]
    fused_results: list[RetrievalResult]
    filtered_results: list[RetrievalResult]
    rerank_results: list[RetrievalResult]
    stages: list[dict[str, Any]]
    fallback_used: bool
    rerank_applied: bool
    duration_ms: float
    rerank_fallback_used: bool = False


@dataclass(frozen=True, slots=True)
class ParallelRetrievalResult:
    """Return merged candidates plus per-collection execution diagnostics."""

    results: list[RetrievalResult]
    collection_results: list[dict[str, Any]]
    partial_failure_count: int
    dense_results: list[RetrievalResult]
    sparse_results: list[RetrievalResult]
    fused_results: list[RetrievalResult]
    filtered_results: list[RetrievalResult]
    rerank_results: list[RetrievalResult]
    fallback_used: bool
    rerank_fallback_used: bool


CollectionRunner = Callable[
    [ProcessedQuery],
    Awaitable[AsyncCollectionRetrievalResult],
]


class AsyncParallelRetrievalController:
    """Run routed collection sub-chains concurrently and merge candidates."""

    def __init__(
        self,
        *,
        collection_runner: Callable[..., Awaitable[AsyncCollectionRetrievalResult]],
        max_collections: int = 3,
        max_concurrency: int = 3,
        per_collection_timeout_seconds: float = 60,
        rrf_k: int = 60,
    ) -> None:
        """Configure concurrency limits and merge behavior.

        Args:
            collection_runner: Awaitable callable that executes retrieval and
                optional rerank for one collection. It must not run Self-RAG or
                Response Builder.
            max_collections: Maximum routed collections accepted by one query.
            max_concurrency: Maximum collection sub-chains running at once.
            per_collection_timeout_seconds: Timeout applied to each collection
                task independently so one slow collection cannot block the whole
                query indefinitely.
            rrf_k: Rank dampening constant used by the routing-aware fallback
                merge score.

        Raises:
            ValueError: If limits are not positive.
        """

        if max_collections <= 0:
            raise ValueError("max_collections must be greater than zero")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        if per_collection_timeout_seconds <= 0:
            raise ValueError("per_collection_timeout_seconds must be greater than zero")
        if rrf_k <= 0:
            raise ValueError("rrf_k must be greater than zero")
        self._collection_runner = collection_runner
        self._max_collections = max_collections
        self._max_concurrency = max_concurrency
        self._per_collection_timeout_seconds = per_collection_timeout_seconds
        self._rrf_k = rrf_k

    async def search(
        self,
        query: ProcessedQuery,
        *,
        collections: Sequence[str] | None = None,
        routing_scores: Mapping[str, float] | None = None,
        routing_reasons: Mapping[str, str] | None = None,
        top_k: int | None = None,
        no_rerank: bool = False,
        trace_context: ParallelTraceContext | None = None,
    ) -> ParallelRetrievalResult:
        """Execute routed collections concurrently and return merged candidates.

        Args:
            query: Query object produced by ``QueryProcessor``.
            collections: Ordered routed collection IDs. ``None`` falls back to
                ``query.collection``.
            routing_scores: Optional route confidence keyed by collection.
            routing_reasons: Optional route explanation keyed by collection.
            top_k: Final merged result count. ``None`` uses ``query.top_k``.
            no_rerank: Whether per-collection rerank should be skipped.
            trace_context: Optional query trace context. The controller records
                a single aggregate ``fusion`` stage containing collection runs,
                partial failures, and merge snapshots.

        Returns:
            Merged retrieval result plus diagnostic per-collection rows.
        """

        started = perf_counter()
        selected, dropped = _normalize_collections(
            collections or (query.collection,),
            max_collections=self._max_collections,
        )
        limit = query.top_k if top_k is None else top_k
        if limit <= 0:
            raise ValueError("top_k must be greater than zero")

        score_map = {key: _coerce_score(value) for key, value in (routing_scores or {}).items()}
        reason_map = dict(routing_reasons or {})
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _bounded_run(collection: str) -> _CollectionRun:
            async with semaphore:
                return await self._run_collection(
                    query,
                    collection=collection,
                    top_k=limit,
                    no_rerank=no_rerank,
                    routing_score=score_map.get(collection, 0.0),
                    routing_reason=reason_map.get(collection),
                )

        runs = await asyncio.gather(*[_bounded_run(collection) for collection in selected])
        result = self._merge_collection_results(
            runs,
            selected=selected,
            dropped=dropped,
            top_k=limit,
            started=started,
            trace_context=trace_context,
        )
        return result

    async def _run_collection(
        self,
        query: ProcessedQuery,
        *,
        collection: str,
        top_k: int,
        no_rerank: bool,
        routing_score: float,
        routing_reason: str | None,
    ) -> _CollectionRun:
        """Run one collection with timeout isolation and diagnostic wrapping."""

        started = perf_counter()
        try:
            result = await asyncio.wait_for(
                self._collection_runner(
                    query,
                    collection=collection,
                    top_k=top_k,
                    no_rerank=no_rerank,
                    routing_score=routing_score,
                    routing_reason=routing_reason,
                ),
                timeout=self._per_collection_timeout_seconds,
            )
        except TimeoutError as error:
            return _CollectionRun.failure(
                collection=collection,
                routing_score=routing_score,
                routing_reason=routing_reason,
                status="timeout",
                error=error,
                duration_ms=(perf_counter() - started) * 1000,
            )
        except Exception as error:
            return _CollectionRun.failure(
                collection=collection,
                routing_score=routing_score,
                routing_reason=routing_reason,
                status="failed",
                error=error,
                duration_ms=(perf_counter() - started) * 1000,
            )
        return _CollectionRun.success(
            result=result,
            routing_score=routing_score,
            routing_reason=routing_reason,
        )

    def _merge_collection_results(
        self,
        runs: Sequence[_CollectionRun],
        *,
        selected: list[str],
        dropped: list[str],
        top_k: int,
        started: float,
        trace_context: ParallelTraceContext | None,
    ) -> ParallelRetrievalResult:
        """Merge successful collection runs and record aggregate trace details."""

        merged: list[RetrievalResult] = []
        collection_results: list[dict[str, Any]] = []
        dense_results: list[RetrievalResult] = []
        sparse_results: list[RetrievalResult] = []
        fused_results: list[RetrievalResult] = []
        filtered_results: list[RetrievalResult] = []
        rerank_results: list[RetrievalResult] = []
        fallback_used = False
        rerank_fallback_used = False
        partial_failure_count = 0

        collection_order = {collection: index for index, collection in enumerate(selected)}
        for run in runs:
            collection_results.append(run.to_trace_row())
            if not run.successful:
                partial_failure_count += 1
                fallback_used = True
                continue
            result = run.result
            if result is None:
                continue
            dense_results.extend(result.dense_results)
            sparse_results.extend(result.sparse_results)
            fused_results.extend(result.fused_results)
            filtered_results.extend(result.filtered_results)
            rerank_results.extend(result.rerank_results)
            fallback_used = fallback_used or result.fallback_used
            rerank_fallback_used = rerank_fallback_used or result.rerank_fallback_used
            for rank, candidate in enumerate(result.results, start=1):
                merge_score = run.routing_score + (1.0 / (self._rrf_k + rank))
                metadata = {
                    **candidate.metadata,
                    "collection": run.collection,
                    "collection_rank": rank,
                    "routing_score": run.routing_score,
                    "routing_reason": run.routing_reason,
                    "merge_score": merge_score,
                    "merge_reason": "routing_score_rrf_fallback",
                    "_collection_order": collection_order.get(run.collection, len(selected)),
                }
                merged.append(candidate.model_copy(update={"metadata": metadata}, deep=True))

        merged.sort(
            key=lambda candidate: (
                -float(candidate.metadata.get("merge_score", 0.0)),
                int(candidate.metadata.get("collection_rank", 0)),
                int(candidate.metadata.get("_collection_order", len(selected))),
                candidate.chunk_id,
            )
        )
        merged = [_without_internal_merge_keys(candidate) for candidate in merged]
        results = merged[:top_k]
        self._record_trace(
            trace_context,
            started=started,
            selected=selected,
            dropped=dropped,
            collection_results=collection_results,
            merged_candidate_count=len(merged),
            partial_failure_count=partial_failure_count,
            result_count=len(results),
        )
        return ParallelRetrievalResult(
            results=results,
            collection_results=collection_results,
            partial_failure_count=partial_failure_count,
            dense_results=dense_results,
            sparse_results=sparse_results,
            fused_results=merged,
            filtered_results=merged,
            rerank_results=rerank_results if rerank_results else merged,
            fallback_used=fallback_used,
            rerank_fallback_used=rerank_fallback_used,
        )

    @staticmethod
    def _record_trace(
        trace_context: ParallelTraceContext | None,
        *,
        started: float,
        selected: list[str],
        dropped: list[str],
        collection_results: list[dict[str, Any]],
        merged_candidate_count: int,
        partial_failure_count: int,
        result_count: int,
    ) -> None:
        """Write async multi-collection diagnostics using normal query stages."""

        if trace_context is None:
            return
        stage_rows = _aggregate_stage_rows(collection_results)
        for stage_name in ("dense", "sparse", "fusion", "filter", "rerank"):
            rows = stage_rows.get(stage_name, [])
            if not rows and stage_name not in {"fusion", "filter"}:
                continue
            details = {
                "selected_collections": selected,
                "dropped_collections": dropped,
                "collection_runs": rows,
                "partial_failure_count": partial_failure_count,
            }
            if stage_name == "fusion":
                details["merged_candidate_count"] = merged_candidate_count
            try:
                trace_context.record_stage(
                    stage=stage_name,
                    method=(
                        "async_multi_collection_merge"
                        if stage_name == "fusion"
                        else f"async_multi_collection_{stage_name}"
                    ),
                    provider="AsyncParallelRetrievalController",
                    status="degraded" if partial_failure_count else "success",
                    duration_ms=_stage_duration(rows),
                    candidate_count=(
                        result_count if stage_name == "fusion" else _stage_candidate_count(rows)
                    ),
                    details=details,
                )
            except Exception:
                return

@dataclass(frozen=True, slots=True)
class _CollectionRun:
    """Internal normalized success/failure row for one collection task."""

    collection: str
    routing_score: float
    routing_reason: str | None
    status: str
    duration_ms: float
    result: AsyncCollectionRetrievalResult | None = None
    error_type: str | None = None
    error_message: str | None = None

    @property
    def successful(self) -> bool:
        """Return whether this run produced a usable collection result."""

        return self.status == "success" and self.result is not None

    @classmethod
    def success(
        cls,
        *,
        result: AsyncCollectionRetrievalResult,
        routing_score: float,
        routing_reason: str | None,
    ) -> _CollectionRun:
        """Build a successful normalized run row."""

        return cls(
            collection=result.collection,
            routing_score=routing_score,
            routing_reason=routing_reason,
            status="success",
            duration_ms=result.duration_ms,
            result=result,
        )

    @classmethod
    def failure(
        cls,
        *,
        collection: str,
        routing_score: float,
        routing_reason: str | None,
        status: str,
        error: BaseException,
        duration_ms: float,
    ) -> _CollectionRun:
        """Build a failed or timed-out normalized run row."""

        return cls(
            collection=collection,
            routing_score=routing_score,
            routing_reason=routing_reason,
            status=status,
            duration_ms=duration_ms,
            error_type=type(error).__name__,
            error_message=str(error) or type(error).__name__,
        )

    def to_trace_row(self) -> dict[str, Any]:
        """Return the compact per-collection row stored in trace details."""

        row: dict[str, Any] = {
            "collection": self.collection,
            "candidate_count": len(self.result.results) if self.result else 0,
            "status": self.status,
            "routing_score": self.routing_score,
            "duration_ms": self.duration_ms,
        }
        if self.routing_reason is not None:
            row["routing_reason"] = self.routing_reason
        if self.result is not None:
            row["fallback_used"] = self.result.fallback_used
            row["rerank_applied"] = self.result.rerank_applied
            row["stages"] = [dict(stage) for stage in self.result.stages]
        if self.error_type is not None:
            row["error_type"] = self.error_type
            row["error"] = self.error_message
        return row



def _without_internal_merge_keys(candidate: RetrievalResult) -> RetrievalResult:
    """Remove private merge tie-break fields before returning candidates."""

    if "_collection_order" not in candidate.metadata:
        return candidate
    metadata = dict(candidate.metadata)
    metadata.pop("_collection_order", None)
    return candidate.model_copy(update={"metadata": metadata}, deep=True)

def _normalize_collections(
    collections: Sequence[str],
    *,
    max_collections: int,
) -> tuple[list[str], list[str]]:
    """Return ordered unique non-blank collections and dropped overflow."""

    selected: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for value in collections:
        if not isinstance(value, str) or not value.strip():
            continue
        collection = value.strip()
        if collection in seen:
            continue
        seen.add(collection)
        if len(selected) < max_collections:
            selected.append(collection)
        else:
            dropped.append(collection)
    if not selected:
        raise ValueError("at least one collection is required")
    return selected, dropped


def _coerce_score(value: object) -> float:
    """Convert routing scores to floats and clamp invalid values to zero."""

    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _aggregate_stage_rows(
    collection_results: Sequence[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group per-collection trace rows by normal query stage name."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in collection_results:
        collection = str(row.get("collection") or "")
        stage_count = 0
        for stage in row.get("stages") or []:
            if not isinstance(stage, dict):
                continue
            stage_name = str(stage.get("stage") or "")
            if stage_name not in {"dense", "sparse", "fusion", "filter", "rerank"}:
                continue
            grouped.setdefault(stage_name, []).append(
                _collection_stage_row(collection, row, stage)
            )
            stage_count += 1
        if stage_count == 0 and _collection_row_failed(row):
            for stage_name in ("dense", "sparse", "fusion", "filter", "rerank"):
                grouped.setdefault(stage_name, []).append(
                    _collection_failure_stage_row(collection, row)
                )
    return grouped


def _collection_row_failed(collection_row: Mapping[str, Any]) -> bool:
    """Return whether a collection run failed before producing stage rows."""

    status = str(collection_row.get("status") or "success")
    return status != "success" or collection_row.get("error_type") is not None


def _collection_failure_stage_row(
    collection: str,
    collection_row: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a compact failure row for aggregate multi-collection stages."""

    result: dict[str, Any] = {
        "collection": collection,
        "status": str(collection_row.get("status") or "failed"),
        "duration_ms": float(collection_row.get("duration_ms") or 0),
        "candidate_count": 0,
        "method": "async_collection_failure",
        "provider": "AsyncParallelRetrievalController",
    }
    if collection_row.get("routing_score") is not None:
        result["routing_score"] = collection_row["routing_score"]
    if collection_row.get("routing_reason") is not None:
        result["routing_reason"] = collection_row["routing_reason"]
    if collection_row.get("error_type") is not None:
        result["error_type"] = collection_row["error_type"]
    if collection_row.get("error") is not None:
        result["error"] = collection_row["error"]
    return result


def _collection_stage_row(
    collection: str,
    collection_row: Mapping[str, Any],
    stage: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one compact collection-level trace row for a query stage."""

    details = stage.get("details") if isinstance(stage.get("details"), dict) else {}
    result: dict[str, Any] = {
        "collection": collection,
        "status": str(stage.get("status") or collection_row.get("status") or "success"),
        "duration_ms": float(stage.get("duration_ms") or 0),
        "candidate_count": int(stage.get("candidate_count") or 0),
    }
    for key in ("method", "provider"):
        if stage.get(key) is not None:
            result[key] = stage[key]
    if collection_row.get("routing_score") is not None:
        result["routing_score"] = collection_row["routing_score"]
    if collection_row.get("routing_reason") is not None:
        result["routing_reason"] = collection_row["routing_reason"]
    for key in (
        "chunk_ids",
        "missing_chunk_ids",
        "fused_candidates",
        "before_candidates",
        "after_candidates",
        "rejected_candidates",
        "rejected_counts",
        "fallback_reasons",
        "failed_routes",
        "fallback_reason",
    ):
        if key in details:
            result[key] = details[key]
    return result


def _stage_duration(rows: Sequence[Mapping[str, Any]]) -> float:
    """Return parallel stage wall time as the slowest collection duration."""

    if not rows:
        return 0.0
    return round(max(float(row.get("duration_ms") or 0) for row in rows), 3)


def _stage_candidate_count(rows: Sequence[Mapping[str, Any]]) -> int:
    """Return total candidates observed across collection rows."""

    return sum(int(row.get("candidate_count") or 0) for row in rows)
