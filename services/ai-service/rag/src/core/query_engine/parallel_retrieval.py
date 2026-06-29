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
        """Write aggregate collection-run diagnostics into query trace."""

        if trace_context is None:
            return
        status = "degraded" if partial_failure_count else "success"
        try:
            trace_context.record_stage(
                stage="fusion",
                method="async_multi_collection_merge",
                provider="AsyncParallelRetrievalController",
                status=status,
                duration_ms=round((perf_counter() - started) * 1000, 3),
                candidate_count=result_count,
                details={
                    "selected_collections": selected,
                    "dropped_collections": dropped,
                    "collection_results": collection_results,
                    "merged_candidate_count": merged_candidate_count,
                    "partial_failure_count": partial_failure_count,
                },
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
            error_message=str(error),
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
