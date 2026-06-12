"""Render the Query Trace page for retrieval diagnostics.

The Query Trace page explains why a query returned its final context by showing
history rows, stage waterfall data, Dense/Sparse/Fusion/Rerank candidate
counts, final top-k summaries, and rerank deltas. Rendering consumes
``TraceReaderService`` DTOs only and does not execute retrieval or mutate trace
storage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.observability.services import (
    TraceDetail,
    TraceHistoryItem,
    TraceReaderService,
    TraceStageWaterfallItem,
)

QUERY_TRACE_WIDGET_KEY = "query_trace_id"


@dataclass(frozen=True, slots=True)
class QueryTracePageModel:
    """Collect Query Trace history and selected detail data for rendering."""

    collection_id: str
    history: tuple[TraceHistoryItem, ...]
    selected_trace: TraceDetail | None


def build_query_trace_page_model(
    *,
    trace_reader: TraceReaderService,
    collection_id: str,
    trace_id: str | None = None,
) -> QueryTracePageModel:
    """Read trace services and build a render-ready Query Trace page model.

    Args:
        trace_reader: Service that reads trace history and detail projections.
        collection_id: Knowledge collection to inspect.
        trace_id: Optional trace preselection. ``None`` selects the newest
            available query trace.

    Returns:
        Immutable page model with history rows and selected detail payload.
    """

    history = tuple(trace_reader.list_query_traces(collection_id))
    history_ids = {trace.trace_id for trace in history}
    selected_trace_id = (
        trace_id
        if trace_id is not None and trace_id in history_ids
        else (history[0].trace_id if history else None)
    )
    selected_trace = (
        trace_reader.get_query_trace_detail(selected_trace_id)
        if selected_trace_id is not None
        else None
    )
    return QueryTracePageModel(
        collection_id=collection_id,
        history=history,
        selected_trace=selected_trace,
    )


def render_query_trace_page(
    model: QueryTracePageModel,
    *,
    ui: Any | None = None,
) -> str | None:
    """Render Query Trace history/detail and return the selected trace ID.

    Args:
        model: Render-ready Query Trace model.
        ui: Optional Streamlit-like module. ``None`` imports ``streamlit`` at
            call time for real Dashboard usage.

    Returns:
        The selected trace ID, or ``None`` when no query trace exists.

    Side Effects:
        Emits Streamlit calls only. It does not run retrieval, rerank, or trace
        persistence.
    """

    streamlit = ui or _streamlit()
    streamlit.title("Query Trace")
    streamlit.caption(f"Collection: {model.collection_id}")

    streamlit.subheader("History")
    streamlit.dataframe([_history_row(trace) for trace in model.history])
    selected_trace_id = _select_trace(
        streamlit,
        model.history,
        selected_trace_id=(
            model.selected_trace.trace_id
            if model.selected_trace is not None
            else None
        ),
    )

    if model.selected_trace is None:
        streamlit.info("No query trace detail is available.")
        return selected_trace_id

    streamlit.subheader("Stage Waterfall")
    streamlit.dataframe([_stage_row(stage) for stage in model.selected_trace.waterfall])
    streamlit.bar_chart(model.selected_trace.candidate_counts)

    streamlit.subheader("Retrieval Comparison")
    for stage, count in model.selected_trace.candidate_counts.items():
        streamlit.metric(stage, count)
    streamlit.write(
        {
            "contexts": model.selected_trace.query_result.get("contexts", []),
            "top_score": model.selected_trace.summary_metrics.get("top_score"),
            "candidate_counts": dict(model.selected_trace.candidate_counts),
        }
    )

    streamlit.subheader("Final Context")
    streamlit.text_area(
        "query_result.content",
        value=_query_result_content(model.selected_trace),
        height=240,
        disabled=True,
    )

    streamlit.subheader("Chunk Frequency Summary")
    streamlit.dataframe(_chunk_frequency_rows(model.selected_trace))

    streamlit.subheader("Chunk Flow Matrix")
    streamlit.dataframe(_chunk_flow_rows(model.selected_trace))

    streamlit.subheader("Rerank Delta")
    streamlit.dataframe(
        [
            {"chunk_id": chunk_id, "rank_delta": delta}
            for chunk_id, delta in model.selected_trace.rerank_delta.items()
        ]
    )
    streamlit.write({"evaluation_metrics": dict(model.selected_trace.evaluation_metrics)})
    return selected_trace_id


def _history_row(trace: TraceHistoryItem) -> dict[str, object]:
    """Convert a query trace history item into a table row."""

    return {
        "trace_id": trace.trace_id,
        "query": trace.display_input,
        "status": trace.status,
        "started_at": trace.started_at,
        "finished_at": trace.finished_at,
        "duration_ms": trace.duration_ms,
        "stage_count": trace.stage_count,
        "fallback_used": trace.fallback_used,
    }


def _stage_row(stage: TraceStageWaterfallItem) -> dict[str, object]:
    """Convert a trace stage DTO into waterfall table evidence."""

    return {
        "stage": stage.stage,
        "duration_ms": stage.duration_ms,
        "status": stage.status,
        "candidate_count": stage.candidate_count,
        "method": stage.method,
        "provider": stage.provider,
        "details": dict(stage.details),
    }


def _chunk_frequency_rows(trace: TraceDetail) -> list[dict[str, object]]:
    """Aggregate candidate appearances across Query Trace retrieval stages.

    Args:
        trace: Selected Query Trace detail from the Dashboard service layer.

    Returns:
        Table rows sorted for diagnosis: chunks seen in more stages appear
        first, final contexts are preferred over rejected-only chunks, and score
        ties are deterministic. Rows contain only IDs, ranks, scores, and
        filter reasons so the Dashboard does not duplicate full chunk text.
    """

    appearances: dict[str, list[str]] = {}
    best_scores: dict[str, float] = {}
    final_ranks: dict[str, int] = {}
    filtered_reasons = _filtered_reasons(trace)

    def record(
        chunk_id: str,
        stage_label: str,
        *,
        score: float | None = None,
        rank: int | None = None,
        is_final: bool = False,
    ) -> None:
        """Record one stage-level observation for one chunk."""

        appearances.setdefault(chunk_id, []).append(stage_label)
        if score is not None:
            best_scores[chunk_id] = max(score, best_scores.get(chunk_id, score))
        if is_final and rank is not None:
            final_ranks[chunk_id] = rank

    for chunk_id in _stage_chunk_ids(trace, "dense"):
        record(chunk_id, "dense")
    for chunk_id in _stage_chunk_ids(trace, "sparse"):
        record(chunk_id, "sparse")
    for candidate in _stage_candidates(trace, "fusion", "fused_candidates"):
        record(candidate.chunk_id, "fusion", score=candidate.score)
    for candidate in _stage_candidates(trace, "filter", "before_candidates"):
        record(candidate.chunk_id, "filter_before", score=candidate.score)
    for candidate in _stage_candidates(trace, "filter", "after_candidates"):
        record(candidate.chunk_id, "filter_after", score=candidate.score)
    for candidate in _stage_candidates(trace, "rerank", "before_candidates"):
        record(candidate.chunk_id, "rerank_before", score=candidate.score)
    for candidate in _stage_candidates(trace, "rerank", "after_candidates"):
        record(candidate.chunk_id, "rerank_after", score=candidate.score)
    for context in _query_contexts(trace):
        record(
            context.chunk_id,
            "final",
            score=context.score,
            rank=context.rank,
            is_final=True,
        )

    rows = [
        {
            "chunk_id": chunk_id,
            "appeared_count": len(stage_labels),
            "stages": ", ".join(stage_labels),
            "final_rank": final_ranks.get(chunk_id),
            "best_score": best_scores.get(chunk_id),
            "filtered_reason": ", ".join(filtered_reasons.get(chunk_id, ())),
        }
        for chunk_id, stage_labels in appearances.items()
    ]
    return sorted(
        rows,
        key=lambda row: (
            -int(row["appeared_count"]),
            row["final_rank"] is None,
            -_sortable_score(row["best_score"]),
            str(row["chunk_id"]),
        ),
    )


def _chunk_flow_rows(trace: TraceDetail) -> list[dict[str, object]]:
    """Build one row per chunk showing its movement through query stages.

    Args:
        trace: Selected Query Trace detail from the Dashboard service layer.

    Returns:
        Matrix rows with hit markers and ranks for Dense, Sparse, Fusion,
        Filter, Rerank, and final result stages. Missing fields from old traces
        are treated as empty values instead of raising errors.
    """

    dense_ids = set(_stage_chunk_ids(trace, "dense"))
    sparse_ids = set(_stage_chunk_ids(trace, "sparse"))
    fusion_ranks = _rank_by_chunk(_stage_candidates(trace, "fusion", "fused_candidates"))
    kept_ids = {
        candidate.chunk_id
        for candidate in _stage_candidates(trace, "filter", "after_candidates")
    }
    rejected_reasons = _filtered_reasons(trace)
    rerank_ranks = _rank_by_chunk(_stage_candidates(trace, "rerank", "after_candidates"))
    final_ranks = _rank_by_chunk(_query_contexts(trace))
    chunk_ids = (
        set(dense_ids)
        | set(sparse_ids)
        | set(fusion_ranks)
        | set(kept_ids)
        | set(rejected_reasons)
        | set(rerank_ranks)
        | set(final_ranks)
    )

    rows = [
        {
            "chunk_id": chunk_id,
            "dense": "hit" if chunk_id in dense_ids else "",
            "sparse": "hit" if chunk_id in sparse_ids else "",
            "fusion_rank": fusion_ranks.get(chunk_id),
            "filter": _filter_status(chunk_id, kept_ids, rejected_reasons),
            "rerank_rank": rerank_ranks.get(chunk_id),
            "final_rank": final_ranks.get(chunk_id),
        }
        for chunk_id in chunk_ids
    ]
    return sorted(
        rows,
        key=lambda row: (
            row["final_rank"] is None,
            _rank_or_large(row["final_rank"]),
            _rank_or_large(row["fusion_rank"]),
            str(row["chunk_id"]),
        ),
    )


@dataclass(frozen=True, slots=True)
class _CandidateView:
    """Represent a trace candidate snapshot without full chunk text."""

    chunk_id: str
    rank: int | None = None
    score: float | None = None


def _stage_details(trace: TraceDetail, stage_name: str) -> Mapping[str, Any]:
    """Return details for the first matching trace stage, or an empty mapping."""

    for stage in trace.waterfall:
        if stage.stage == stage_name:
            return stage.details if isinstance(stage.details, Mapping) else {}
    return {}


def _stage_chunk_ids(trace: TraceDetail, stage_name: str) -> list[str]:
    """Extract chunk IDs from a Dense/Sparse stage details payload."""

    raw_chunk_ids = _stage_details(trace, stage_name).get("chunk_ids", ())
    if not isinstance(raw_chunk_ids, list | tuple):
        return []
    return [chunk_id for chunk_id in raw_chunk_ids if isinstance(chunk_id, str)]


def _stage_candidates(
    trace: TraceDetail,
    stage_name: str,
    field_name: str,
) -> list[_CandidateView]:
    """Extract ordered candidate snapshots from one trace stage field."""

    raw_candidates = _stage_details(trace, stage_name).get(field_name, ())
    return _candidate_views(raw_candidates)


def _query_contexts(trace: TraceDetail) -> list[_CandidateView]:
    """Extract final context snapshots from ``query_result.contexts``."""

    contexts = trace.query_result.get("contexts", ())
    return _candidate_views(contexts)


def _query_result_content(trace: TraceDetail) -> str:
    """Return the Agent-ready final context recorded in Query Trace.

    Args:
        trace: Selected Query Trace detail.

    Returns:
        ``query_result.content`` when present, otherwise an empty string. Old
        traces without this field should remain renderable.
    """

    content = trace.query_result.get("content")
    return content if isinstance(content, str) else ""


def _candidate_views(raw_candidates: Any) -> list[_CandidateView]:
    """Normalize raw trace dictionaries into compact candidate view objects."""

    if not isinstance(raw_candidates, list | tuple):
        return []

    views: list[_CandidateView] = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, Mapping):
            continue
        chunk_id = raw_candidate.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            continue
        views.append(
            _CandidateView(
                chunk_id=chunk_id,
                rank=_optional_int(raw_candidate.get("rank")),
                score=_optional_float(raw_candidate.get("score")),
            )
        )
    return views


def _filtered_reasons(trace: TraceDetail) -> dict[str, tuple[str, ...]]:
    """Map rejected chunk IDs to deterministic filter rejection reasons."""

    reasons: dict[str, list[str]] = {}
    raw_rejections = _stage_details(trace, "filter").get("rejected_candidates", ())
    if not isinstance(raw_rejections, list | tuple):
        return {}
    for raw_rejection in raw_rejections:
        if not isinstance(raw_rejection, Mapping):
            continue
        chunk_id = raw_rejection.get("chunk_id")
        reason = raw_rejection.get("reason")
        if isinstance(chunk_id, str) and isinstance(reason, str):
            reasons.setdefault(chunk_id, []).append(reason)
    return {
        chunk_id: tuple(sorted(set(chunk_reasons)))
        for chunk_id, chunk_reasons in reasons.items()
    }


def _rank_by_chunk(candidates: list[_CandidateView]) -> dict[str, int]:
    """Build a chunk-id to rank mapping from normalized candidate snapshots."""

    return {
        candidate.chunk_id: candidate.rank
        for candidate in candidates
        if candidate.rank is not None
    }


def _filter_status(
    chunk_id: str,
    kept_ids: set[str],
    rejected_reasons: Mapping[str, tuple[str, ...]],
) -> str:
    """Return a compact filter status for the Chunk Flow Matrix."""

    if chunk_id in kept_ids:
        return "kept"
    reasons = rejected_reasons.get(chunk_id)
    if reasons:
        return f"rejected:{','.join(reasons)}"
    return ""


def _optional_int(value: Any) -> int | None:
    """Convert a trace numeric rank to ``int`` when possible."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _optional_float(value: Any) -> float | None:
    """Convert a trace numeric score to ``float`` when possible."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _sortable_score(value: object) -> float:
    """Return a numeric score for sorting rows with optional best scores."""

    return float(value) if isinstance(value, int | float) else float("-inf")


def _rank_or_large(value: object) -> int:
    """Return an integer rank or a large sentinel for ``None`` values."""

    return int(value) if isinstance(value, int) else 1_000_000


def _select_trace(
    streamlit: Any,
    history: tuple[TraceHistoryItem, ...],
    *,
    selected_trace_id: str | None,
) -> str | None:
    """Render persistent Query Trace selection and return the selected ID."""

    options = tuple(trace.trace_id for trace in history)
    if not options:
        streamlit.info("No query traces are available.")
        return None
    selected_index = (
        options.index(selected_trace_id)
        if selected_trace_id in options
        else 0
    )
    selected = streamlit.selectbox(
        "Trace",
        options=options,
        index=selected_index,
        key=QUERY_TRACE_WIDGET_KEY,
    )
    return str(selected) if selected is not None else None


def _streamlit() -> Any:
    """Import Streamlit only when a real render call needs it."""

    import streamlit

    return streamlit
