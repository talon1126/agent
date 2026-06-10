"""Render the Evaluation page for RAG quality monitoring.

The Evaluation page shows historical runs, selected run details, metric values,
and trend data grouped by metric name. It also collects an operator's request
to start a new evaluation run, but does not execute evaluation inside the
renderer. Real run orchestration stays in ``EvaluationService`` or later
Dashboard application code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.observability.services import (
    EvaluationMetricTrendPoint,
    EvaluationRunDetail,
    EvaluationRunSummary,
    EvaluationService,
)


@dataclass(frozen=True, slots=True)
class EvaluationPageModel:
    """Collect evaluation history, selected run detail, and trends."""

    collection_id: str
    runs: tuple[EvaluationRunSummary, ...]
    selected_run: EvaluationRunDetail | None
    metric_trends: dict[str, tuple[EvaluationMetricTrendPoint, ...]]


@dataclass(frozen=True, slots=True)
class EvaluationPageSelection:
    """Represent evaluation page selection and run-request intent."""

    run_id: str | None
    request_run: bool


def build_evaluation_page_model(
    *,
    evaluation_service: EvaluationService,
    collection_id: str,
    run_id: str | None = None,
) -> EvaluationPageModel:
    """Read evaluation services and build a render-ready page model.

    Args:
        evaluation_service: Service that reads evaluation runs and metric
            trends.
        collection_id: Knowledge collection whose quality history is shown.
        run_id: Optional run preselection. ``None`` selects the newest
            available evaluation run.

    Returns:
        Immutable page model with run history, selected run detail, and metric
        trend points grouped by metric name.
    """

    runs = tuple(evaluation_service.list_runs(collection_id))
    selected_run_id = run_id or (runs[0].run_id if runs else None)
    selected_run = (
        evaluation_service.get_run_detail(selected_run_id)
        if selected_run_id is not None
        else None
    )
    trends = {
        metric_name: tuple(points)
        for metric_name, points in evaluation_service.metric_trends(collection_id).items()
    }
    return EvaluationPageModel(
        collection_id=collection_id,
        runs=runs,
        selected_run=selected_run,
        metric_trends=trends,
    )


def render_evaluation_page(
    model: EvaluationPageModel,
    *,
    ui: Any | None = None,
) -> EvaluationPageSelection:
    """Render evaluation history/detail/trends and return operator intent.

    Args:
        model: Render-ready evaluation page model.
        ui: Optional Streamlit-like module. ``None`` imports ``streamlit`` at
            call time for real Dashboard usage.

    Returns:
        Selection DTO containing the selected run ID and whether the operator
        requested a new evaluation run.

    Side Effects:
        Emits Streamlit calls only. It does not execute evaluator backends or
        write evaluation rows.
    """

    streamlit = ui or _streamlit()
    streamlit.title("Evaluation")
    streamlit.caption(f"Collection: {model.collection_id}")

    streamlit.subheader("Evaluation Run")
    request_run = bool(streamlit.button("Run evaluation"))
    if request_run:
        streamlit.info(
            {
                "collection": model.collection_id,
                "status": "pending evaluation orchestration",
            }
        )

    streamlit.subheader("History")
    streamlit.dataframe([_run_row(run) for run in model.runs])
    selected_run_id = _select_run(streamlit, model.runs)

    streamlit.subheader("Metric Details")
    if model.selected_run is None:
        streamlit.info("No evaluation run detail is available.")
    else:
        for metric_name, metric_value in model.selected_run.metrics.items():
            streamlit.metric(metric_name, metric_value)
        streamlit.dataframe(
            [
                {
                    "metric_name": metric_name,
                    "metric_value": metric_value,
                    "details": model.selected_run.metric_details.get(metric_name, {}),
                }
                for metric_name, metric_value in model.selected_run.metrics.items()
            ]
        )
        streamlit.write(
            {
                "summary": dict(model.selected_run.summary),
                "settings_snapshot": dict(model.selected_run.settings_snapshot),
            }
        )

    streamlit.subheader("Trends")
    streamlit.dataframe(_trend_rows(model.metric_trends))
    streamlit.bar_chart(
        {
            metric_name: points[-1].metric_value
            for metric_name, points in model.metric_trends.items()
            if points
        }
    )
    return EvaluationPageSelection(
        run_id=selected_run_id,
        request_run=request_run,
    )


def _run_row(run: EvaluationRunSummary) -> dict[str, object]:
    """Convert an evaluation run summary into a table row."""

    return {
        "run_id": run.run_id,
        "evaluator": run.evaluator,
        "dataset_name": run.dataset_name,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "metric_count": run.metric_count,
        "metrics": dict(run.metrics),
    }


def _trend_rows(
    trends: dict[str, tuple[EvaluationMetricTrendPoint, ...]],
) -> list[dict[str, object]]:
    """Flatten metric trends into table rows."""

    rows: list[dict[str, object]] = []
    for metric_name, points in trends.items():
        for point in points:
            rows.append(
                {
                    "metric_name": metric_name,
                    "run_id": point.run_id,
                    "metric_value": point.metric_value,
                    "evaluator": point.evaluator,
                    "dataset_name": point.dataset_name,
                    "status": point.status,
                    "created_at": point.created_at,
                }
            )
    return rows


def _select_run(
    streamlit: Any,
    runs: tuple[EvaluationRunSummary, ...],
) -> str | None:
    """Render run selection and return the selected run ID."""

    options = tuple(run.run_id for run in runs)
    if not options:
        streamlit.info("No evaluation runs are available.")
        return None
    selected = streamlit.selectbox("Run", options=options)
    return str(selected) if selected is not None else None


def _streamlit() -> Any:
    """Import Streamlit only when a real render call needs it."""

    import streamlit

    return streamlit
