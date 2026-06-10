"""Render the Ingestion Trace page for offline pipeline diagnostics.

The Ingestion Trace page explains what happened during document ingestion by
showing historical trace rows, stage timing, document/chunk processing counts,
skip/error details, and quality metrics. It consumes ``TraceReaderService``
DTOs only and does not run ingestion or mutate trace storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.observability.services import (
    TraceDetail,
    TraceHistoryItem,
    TraceReaderService,
    TraceStageWaterfallItem,
    TraceTransformStepItem,
)

INGESTION_TRACE_WIDGET_KEY = "ingestion_trace_id"


@dataclass(frozen=True, slots=True)
class IngestionTracePageModel:
    """Collect Ingestion Trace history and selected detail data for rendering."""

    collection_id: str
    history: tuple[TraceHistoryItem, ...]
    selected_trace: TraceDetail | None


def build_ingestion_trace_page_model(
    *,
    trace_reader: TraceReaderService,
    collection_id: str,
    trace_id: str | None = None,
) -> IngestionTracePageModel:
    """Read trace services and build a render-ready Ingestion Trace model.

    Args:
        trace_reader: Service that reads ingestion trace history and detail.
        collection_id: Knowledge collection to inspect.
        trace_id: Optional trace preselection. ``None`` selects the newest
            available ingestion trace.

    Returns:
        Immutable page model with history rows and selected trace detail.
    """

    history = tuple(trace_reader.list_ingestion_traces(collection_id))
    history_ids = {trace.trace_id for trace in history}
    selected_trace_id = (
        trace_id
        if trace_id is not None and trace_id in history_ids
        else (history[0].trace_id if history else None)
    )
    selected_trace = (
        trace_reader.get_ingestion_trace_detail(selected_trace_id)
        if selected_trace_id is not None
        else None
    )
    return IngestionTracePageModel(
        collection_id=collection_id,
        history=history,
        selected_trace=selected_trace,
    )


def render_ingestion_trace_page(
    model: IngestionTracePageModel,
    *,
    ui: Any | None = None,
) -> str | None:
    """Render Ingestion Trace history/detail and return selected trace ID.

    Args:
        model: Render-ready Ingestion Trace model.
        ui: Optional Streamlit-like module. ``None`` imports ``streamlit`` at
            call time for real Dashboard usage.

    Returns:
        The selected trace ID, or ``None`` when no ingestion trace exists.

    Side Effects:
        Emits Streamlit calls only. It does not run ingestion or write trace
        records.
    """

    streamlit = ui or _streamlit()
    streamlit.title("Ingestion Trace")
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
        streamlit.info("No ingestion trace detail is available.")
        return selected_trace_id

    streamlit.subheader("Stage Timing")
    stage_rows = [_stage_row(stage) for stage in model.selected_trace.waterfall]
    streamlit.dataframe(stage_rows)
    streamlit.bar_chart(
        {
            stage.stage: stage.duration_ms or 0.0
            for stage in model.selected_trace.waterfall
        }
    )

    if model.selected_trace.transform_steps:
        streamlit.subheader("Transform Breakdown")
        streamlit.dataframe(
            [
                _transform_step_row(step)
                for step in model.selected_trace.transform_steps
            ]
        )
        streamlit.bar_chart(
            {
                f"{index}. {step.name}": step.duration_ms or 0.0
                for index, step in enumerate(
                    model.selected_trace.transform_steps,
                    start=1,
                )
            }
        )

    streamlit.subheader("Processing Statistics")
    for metric_name in (
        "chunk_count",
        "embedded_count",
        "skipped_count",
        "document_status",
    ):
        if metric_name in model.selected_trace.summary_metrics:
            streamlit.metric(metric_name, model.selected_trace.summary_metrics[metric_name])
    streamlit.write({"summary_metrics": dict(model.selected_trace.summary_metrics)})

    streamlit.subheader("Quality and Errors")
    streamlit.write({"evaluation_metrics": dict(model.selected_trace.evaluation_metrics)})
    if model.selected_trace.error:
        streamlit.warning({"error": model.selected_trace.error})
    return selected_trace_id


def _history_row(trace: TraceHistoryItem) -> dict[str, object]:
    """Convert an ingestion trace history item into a table row."""

    return {
        "trace_id": trace.trace_id,
        "source": trace.display_input,
        "status": trace.status,
        "duration_ms": trace.duration_ms,
        "stage_count": trace.stage_count,
        "error": trace.error,
    }


def _stage_row(stage: TraceStageWaterfallItem) -> dict[str, object]:
    """Convert one ingestion stage into waterfall table evidence."""

    return {
        "stage": stage.stage,
        "duration_ms": stage.duration_ms,
        "status": stage.status,
        "method": stage.method,
        "provider": stage.provider,
        "details": dict(stage.details),
        "error": stage.error,
    }


def _transform_step_row(step: TraceTransformStepItem) -> dict[str, object]:
    """Convert one concrete Transform execution into table evidence."""

    return {
        "name": step.name,
        "duration_ms": step.duration_ms,
        "status": step.status,
        "input_count": step.input_count,
        "output_count": step.output_count,
        "method": step.method,
        "provider": step.provider,
        "error": step.error,
    }


def _select_trace(
    streamlit: Any,
    history: tuple[TraceHistoryItem, ...],
    *,
    selected_trace_id: str | None,
) -> str | None:
    """Render persistent Ingestion Trace selection and return the selected ID."""

    options = tuple(trace.trace_id for trace in history)
    if not options:
        streamlit.info("No ingestion traces are available.")
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
        key=INGESTION_TRACE_WIDGET_KEY,
    )
    return str(selected) if selected is not None else None


def _streamlit() -> Any:
    """Import Streamlit only when a real render call needs it."""

    import streamlit

    return streamlit
