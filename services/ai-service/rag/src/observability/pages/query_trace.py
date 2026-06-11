"""Render the Query Trace page for retrieval diagnostics.

The Query Trace page explains why a query returned its final context by showing
history rows, stage waterfall data, Dense/Sparse/Fusion/Rerank candidate
counts, final top-k summaries, and rerank deltas. Rendering consumes
``TraceReaderService`` DTOs only and does not execute retrieval or mutate trace
storage.
"""

from __future__ import annotations

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
