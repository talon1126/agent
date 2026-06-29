"""Coordinate routed retrieval across more than one knowledge collection.

The controller belongs to the core query layer. It does not decide which
collections are relevant, create storage clients, or build public responses.
It receives an already processed query plus caller-selected collections, runs
the single-collection retrieval boundary for each collection, and merges the
returned candidates with routing-aware metadata for trace and evaluation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from src.core.query_engine.query_processor import ProcessedQuery
from src.core.types import RetrievalResult


class ParallelTraceContext(Protocol):
    """Describe the optional trace sink used by parallel retrieval."""

    def record_stage(self, **payload: object) -> None:
        """Record one structured trace stage."""


class CollectionRetrievalRuntime(Protocol):
    """Describe the per-collection retrieval boundary used by the controller."""

    def execute_collection(
        self,
        *,
        query: ProcessedQuery,
        collection: str,
        top_k: int,
        no_rerank: bool,
        trace_id: str,
    ) -> list[RetrievalResult]:
        """Return final candidates for one collection."""


TraceIdFactory = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class ParallelRetrievalResult:
    """Return merged candidates plus per-collection execution diagnostics."""

    results: list[RetrievalResult]
    collection_results: list[dict[str, Any]]
    query_trace_ids: list[str]
    partial_failure_count: int


class ParallelRetrievalController:
    """Run retrieval across routed collections and merge candidates."""

    def __init__(
        self,
        *,
        runtime: CollectionRetrievalRuntime,
        max_collections: int = 3,
        rrf_k: int = 60,
    ) -> None:
        """Configure the per-collection runtime and merge behavior.

        Args:
            runtime: Adapter that executes the existing single-collection query
                path for one collection at a time.
            max_collections: Upper bound for routed collections accepted by one
                query. Extra collections are dropped in caller-provided order.
            rrf_k: Rank dampening constant used by the fallback merge score.

        Raises:
            ValueError: If collection or merge limits are invalid.
        """

        if max_collections <= 0:
            raise ValueError("max_collections must be greater than zero")
        if rrf_k <= 0:
            raise ValueError("rrf_k must be greater than zero")
        self._runtime = runtime
        self._max_collections = max_collections
        self._rrf_k = rrf_k

    def search(
        self,
        query: ProcessedQuery,
        *,
        collections: Sequence[str] | None = None,
        routing_scores: Mapping[str, float] | None = None,
        routing_reasons: Mapping[str, str] | None = None,
        top_k: int | None = None,
        no_rerank: bool = False,
        trace_id_factory: TraceIdFactory | None = None,
        trace_context: ParallelTraceContext | None = None,
    ) -> ParallelRetrievalResult:
        """Execute routed retrieval and return one merged candidate list.

        Args:
            query: Validated query produced by ``QueryProcessor``.
            collections: Ordered routed collections. ``None`` falls back to
                ``query.collection``.
            routing_scores: Optional intent/router scores keyed by collection.
                Higher scores bias the final merge but do not fully exclude
                strong candidates from lower-confidence collections.
            routing_reasons: Optional human-readable route reasons keyed by
                collection for trace diagnostics.
            top_k: Final merged candidate count. ``None`` uses ``query.top_k``.
            no_rerank: Propagated single-collection runtime flag.
            trace_id_factory: Creates child query trace IDs per collection.
            trace_context: Optional trace sink for the aggregate stage.

        Returns:
            ``ParallelRetrievalResult`` containing merged results, child trace
            IDs, and per-collection status rows.
        """

        started = perf_counter()
        selected, dropped = _normalize_collections(
            collections or (query.collection,),
            max_collections=self._max_collections,
        )
        limit = query.top_k if top_k is None else top_k
        if limit <= 0:
            raise ValueError("top_k must be greater than zero")

        score_map = dict(routing_scores or {})
        reason_map = dict(routing_reasons or {})
        trace_factory = trace_id_factory or (lambda collection: f"{collection}-query")
        merged: list[RetrievalResult] = []
        collection_results: list[dict[str, Any]] = []
        trace_ids: list[str] = []
        partial_failure_count = 0

        for collection in selected:
            trace_id = trace_factory(collection)
            trace_ids.append(trace_id)
            route_score = _coerce_score(score_map.get(collection, 0.0))
            try:
                candidates = self._runtime.execute_collection(
                    query=query,
                    collection=collection,
                    top_k=limit,
                    no_rerank=no_rerank,
                    trace_id=trace_id,
                )
            except Exception as error:
                partial_failure_count += 1
                collection_results.append(
                    {
                        "collection": collection,
                        "trace_id": trace_id,
                        "candidate_count": 0,
                        "status": "failed",
                        "routing_score": route_score,
                        "error": str(error),
                    }
                )
                continue

            collection_results.append(
                {
                    "collection": collection,
                    "trace_id": trace_id,
                    "candidate_count": len(candidates),
                    "status": "success",
                    "routing_score": route_score,
                }
            )
            for rank, candidate in enumerate(candidates, start=1):
                merge_score = route_score + (1.0 / (self._rrf_k + rank))
                metadata = {
                    **candidate.metadata,
                    "collection": collection,
                    "collection_rank": rank,
                    "routing_score": route_score,
                    "routing_reason": reason_map.get(collection),
                    "merge_score": merge_score,
                    "merge_reason": "routing_score_rrf_fallback",
                }
                merged.append(candidate.model_copy(update={"metadata": metadata}))

        merged.sort(
            key=lambda candidate: (
                -float(candidate.metadata.get("merge_score", 0.0)),
                int(candidate.metadata.get("collection_rank", 0)),
                candidate.chunk_id,
            )
        )
        results = merged[:limit]
        self._record_trace(
            trace_context,
            started=started,
            selected=selected,
            dropped=dropped,
            collection_results=collection_results,
            merged_candidate_count=len(merged),
            partial_failure_count=partial_failure_count,
            result_count=len(results),
            trace_ids=trace_ids,
        )
        return ParallelRetrievalResult(
            results=results,
            collection_results=collection_results,
            query_trace_ids=trace_ids,
            partial_failure_count=partial_failure_count,
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
        trace_ids: list[str],
    ) -> None:
        """Write aggregate trace details without coupling retrieval to logging."""

        if trace_context is None:
            return
        try:
            trace_context.record_stage(
                stage="parallel_retrieval",
                method="multi_collection_merge",
                provider="ParallelRetrievalController",
                status="partial_success" if partial_failure_count else "success",
                duration_ms=round((perf_counter() - started) * 1000),
                candidate_count=result_count,
                details={
                    "selected_collections": selected,
                    "dropped_collections": dropped,
                    "collection_results": collection_results,
                    "merged_candidate_count": merged_candidate_count,
                    "partial_failure_count": partial_failure_count,
                    "query_trace_ids": trace_ids,
                },
            )
        except Exception:
            return


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
