"""Render the Ingestion Trace page for offline pipeline diagnostics.

The Ingestion Trace page explains what happened during document ingestion by
showing historical trace rows, stage timing, document/chunk processing counts,
skip/error details, and quality metrics. It consumes ``TraceReaderService``
DTOs only and does not run ingestion or mutate trace storage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from html import escape
from typing import Any

from src.observability.services import (
    TraceDetail,
    TraceHistoryItem,
    TraceReaderService,
    TraceStageWaterfallItem,
    TraceTransformSnapshotItem,
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
        _render_transform_diff(streamlit, model.selected_trace.transform_steps)

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
        "started_at": trace.started_at,
        "finished_at": trace.finished_at,
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


def _render_transform_diff(
    streamlit: Any,
    steps: tuple[TraceTransformStepItem, ...],
) -> None:
    """Render per-transform before/after chunk previews when available.

    Args:
        streamlit: Streamlit-like renderer.
        steps: Transform execution DTOs from the selected ingestion trace.

    Side Effects:
        Emits a colored legend and a table of bounded before/after previews.
        The function intentionally skips output for historical traces that do
        not contain snapshot data.
    """

    snapshots = [
        snapshot
        for step in steps
        for snapshot in step.snapshots
    ]
    if not snapshots:
        return

    streamlit.subheader("Transform Result Diff")
    streamlit.markdown(
        _transform_diff_legend(snapshots),
        unsafe_allow_html=True,
    )
    streamlit.markdown(
        _transform_diff_cards(snapshots),
        unsafe_allow_html=True,
    )
    streamlit.dataframe([_transform_snapshot_row(snapshot) for snapshot in snapshots])


def _transform_snapshot_row(
    snapshot: TraceTransformSnapshotItem,
) -> dict[str, object]:
    """Convert one Transform snapshot DTO into a table row.

    Args:
        snapshot: Bounded before/after evidence for one chunk and one concrete
            Transform step.

    Returns:
        A row containing step identity, color, chunk identity, change type, and
        preview text for Dashboard comparison.
    """

    return {
        "step": snapshot.step_name,
        "step_color": snapshot.step_color,
        "chunk_id": snapshot.chunk_id,
        "chunk_index": snapshot.chunk_index,
        "change_type": snapshot.change_type,
        "before_preview": snapshot.before_preview,
        "after_preview": snapshot.after_preview,
        "truncated": snapshot.before_truncated or snapshot.after_truncated,
    }


def _transform_diff_legend(
    snapshots: list[TraceTransformSnapshotItem],
) -> str:
    """Build a small HTML legend mapping Transform names to colors.

    Args:
        snapshots: Snapshot DTOs visible in the current diff table.

    Returns:
        HTML safe for ``streamlit.markdown(..., unsafe_allow_html=True)``.
        Step names and colors originate from internal trace DTOs, not user
        input.
    """

    unique_steps: dict[str, str] = {}
    for snapshot in snapshots:
        unique_steps.setdefault(snapshot.step_name, snapshot.step_color)
    badges = [
        (
            "<span style='display:inline-block;margin-right:8px;"
            "margin-bottom:6px;padding:2px 8px;border-radius:4px;"
            f"background:{escape(color)};color:white;font-size:12px'>"
            f"{escape(name)}</span>"
        )
        for name, color in unique_steps.items()
    ]
    return "".join(badges)


def _transform_diff_cards(
    snapshots: list[TraceTransformSnapshotItem],
) -> str:
    """Build colored before/after cards for Transform snapshot inspection.

    Args:
        snapshots: Snapshot DTOs visible in the current diff table.

    Returns:
        HTML cards. Each card uses the transform-specific color as a left
        border and header accent so rewrite, semantic merge, denoise, and
        future transforms are visually separable even before reading text.
    """

    return "\n".join(_transform_diff_card(snapshot) for snapshot in snapshots)


def _transform_diff_card(snapshot: TraceTransformSnapshotItem) -> str:
    """Build one colored before/after Transform diff card.

    Args:
        snapshot: Bounded preview for one chunk at one transform step.

    Returns:
        Escaped HTML showing chunk identity, change type, before text, and
        after text. The after panel is tinted with the transform color to make
        the produced content stand out from the original input.
    """

    color = escape(snapshot.step_color)
    before_html, after_html = _highlight_preview_diff(
        before=snapshot.before_preview,
        after=snapshot.after_preview,
    )
    before_suffix = " ..." if snapshot.before_truncated else ""
    after_suffix = " ..." if snapshot.after_truncated else ""
    return (
        "<div class='transform-diff-card' "
        "style='margin:10px 0 14px 0;padding:12px 14px;"
        f"border-left:6px solid {color};"
        "border-radius:6px;background:#ffffff;"
        "box-shadow:0 1px 3px rgba(15,23,42,0.12)'>"
        "<div style='display:flex;gap:8px;align-items:center;"
        "margin-bottom:8px;flex-wrap:wrap'>"
        f"<strong style='color:{color}'>{escape(snapshot.step_name)}</strong>"
        "<span style='font-size:12px;color:#475569'>"
        f"chunk_index={snapshot.chunk_index} · {escape(snapshot.change_type)}"
        "</span>"
        "<span style='font-size:12px;color:#64748b'>"
        f"{escape(snapshot.chunk_id)}"
        "</span>"
        "</div>"
        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px'>"
        "<div style='padding:10px;border:1px solid #e2e8f0;"
        "border-radius:6px;background:#f8fafc'>"
        "<div style='font-size:12px;font-weight:600;color:#475569;"
        "margin-bottom:6px'>Before</div>"
        "<pre style='white-space:pre-wrap;margin:0;font-size:12px;"
        "line-height:1.45;color:#0f172a;font-family:Consolas,monospace'>"
        f"{before_html}{before_suffix}</pre>"
        "</div>"
        "<div style='padding:10px;border:1px solid #e2e8f0;"
        f"border-top:3px solid {color};"
        "border-radius:6px;background:#fff'>"
        "<div style='font-size:12px;font-weight:600;"
        f"color:{color};margin-bottom:6px'>After</div>"
        "<pre style='white-space:pre-wrap;margin:0;font-size:12px;"
        "line-height:1.45;color:#0f172a;font-family:Consolas,monospace'>"
        f"{after_html}{after_suffix}</pre>"
        "</div>"
        "</div>"
        "</div>"
    )


def _highlight_preview_diff(*, before: str, after: str) -> tuple[str, str]:
    """Highlight changed preview tokens for side-by-side diff cards.

    Args:
        before: Original preview text captured before a Transform step ran.
        after: Result preview text captured after the Transform step finished.

    Returns:
        Two escaped HTML fragments. Deleted or replaced tokens in ``before``
        receive a red ``transform-diff-removed`` span, while inserted or
        replaced tokens in ``after`` receive a green ``transform-diff-added``
        span. Equal tokens remain unwrapped so unchanged context stays quiet.
    """

    before_tokens = _split_diff_tokens(before)
    after_tokens = _split_diff_tokens(after)
    matcher = SequenceMatcher(a=before_tokens, b=after_tokens, autojunk=False)
    before_parts: list[str] = []
    after_parts: list[str] = []

    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        before_segment = "".join(before_tokens[before_start:before_end])
        after_segment = "".join(after_tokens[after_start:after_end])
        if tag == "equal":
            before_parts.append(escape(before_segment))
            after_parts.append(escape(after_segment))
        elif tag == "delete":
            before_parts.append(_diff_span(before_segment, kind="removed"))
        elif tag == "insert":
            after_parts.append(_diff_span(after_segment, kind="added"))
        elif tag == "replace":
            before_parts.append(_diff_span(before_segment, kind="removed"))
            after_parts.append(_diff_span(after_segment, kind="added"))

    return "".join(before_parts), "".join(after_parts)


def _split_diff_tokens(text: str) -> list[str]:
    """Split preview text into multilingual tokens for readable diffs.

    Args:
        text: Preview text to split.

    Returns:
        Ordered tokens that preserve whitespace and punctuation. Each CJK
        character is a token because Chinese prose commonly has no spaces;
        Latin letters, digits, and underscores remain grouped as words. This
        gives ``SequenceMatcher`` enough granularity to preserve unchanged
        Chinese phrases instead of marking an entire paragraph as replaced.
    """

    if not text:
        return []
    return re.findall(
        r"\s+|[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9_]+|.",
        text,
        flags=re.DOTALL,
    )


def _diff_span(text: str, *, kind: str) -> str:
    """Wrap changed text in a colored inline diff span.

    Args:
        text: Changed token segment.
        kind: Either ``removed`` or ``added``.

    Returns:
        Escaped HTML span with a stable class for tests and an inline color
        style for Streamlit's permissive markdown renderer.
    """

    styles = {
        "removed": "background:#FEE2E2;color:#991B1B;",
        "added": "background:#DCFCE7;color:#166534;",
    }
    return (
        f"<span class='transform-diff-{kind}' "
        f"style='{styles[kind]}padding:1px 2px;border-radius:3px'>"
        f"{escape(text)}</span>"
    )


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
