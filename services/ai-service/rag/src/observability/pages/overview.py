"""Render the System Overview page for the local Streamlit Dashboard.

The overview page is a presentation boundary over Dashboard services. It shows
validated component configuration, collection asset counts, and the latest
Query/Ingestion health signals without instantiating providers or running
pipeline work. The module accepts an injectable Streamlit-like object so tests
can verify startup behavior without launching a browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.observability.services import (
    CollectionStats,
    ConfigOverview,
    ConfigReaderService,
    DataBrowserService,
    TraceHistoryItem,
    TraceReaderService,
)


@dataclass(frozen=True, slots=True)
class DashboardHealthSnapshot:
    """Represent the latest trace-derived health signal for one pipeline."""

    trace_id: str | None
    status: str
    duration_ms: float | None
    started_at: datetime | None
    error: object | None = None


@dataclass(frozen=True, slots=True)
class OverviewPageModel:
    """Collect every read-only value needed by the overview page.

    The model keeps page rendering deterministic and side-effect free. Service
    calls happen before rendering in ``build_overview_page_model()``, while the
    renderer only turns this immutable model into Streamlit calls.
    """

    config: ConfigOverview
    collection_stats: CollectionStats
    latest_query: DashboardHealthSnapshot
    latest_ingestion: DashboardHealthSnapshot


def build_overview_page_model(
    *,
    config_reader: ConfigReaderService,
    data_browser: DataBrowserService,
    trace_reader: TraceReaderService,
    collection_id: str | None = None,
) -> OverviewPageModel:
    """Read services and build a render-ready System Overview model.

    Args:
        config_reader: Service that projects validated settings.
        data_browser: Service that reads collection asset statistics.
        trace_reader: Service that reads Query and Ingestion trace history.
        collection_id: Optional collection override. ``None`` uses the default
            collection from settings.

    Returns:
        Immutable page model containing component config, asset counts, and the
        latest health row for both online query and offline ingestion.
    """

    config = config_reader.read_overview()
    selected_collection = collection_id or config.default_collection
    stats = data_browser.collection_stats(selected_collection)
    query_history = trace_reader.list_query_traces(selected_collection)
    ingestion_history = trace_reader.list_ingestion_traces(selected_collection)
    return OverviewPageModel(
        config=config,
        collection_stats=stats,
        latest_query=_latest_health(query_history),
        latest_ingestion=_latest_health(ingestion_history),
    )


def render_overview_page(
    model: OverviewPageModel,
    *,
    ui: Any | None = None,
) -> None:
    """Render the System Overview page through Streamlit-compatible calls.

    Args:
        model: Render-ready overview data.
        ui: Optional Streamlit-like module. ``None`` imports ``streamlit`` at
            call time, keeping module import safe in non-Dashboard contexts.

    Side Effects:
        Emits Streamlit calls through ``ui``. It does not mutate the database,
        trigger ingestion, or instantiate provider SDK clients.
    """

    streamlit = ui or _streamlit()
    streamlit.title("System Overview")
    streamlit.caption(
        f"{model.config.project_name} / {model.config.environment} / "
        f"{model.collection_stats.collection_id}"
    )

    streamlit.subheader("Components")
    streamlit.dataframe([_component_row(component) for component in model.config.components])
    transform_component = _find_component(model.config.components, "transform")
    if transform_component is not None:
        with streamlit.expander("sub_transform"):
            streamlit.dataframe(_transform_step_rows(transform_component))

    streamlit.subheader("Data Assets")
    streamlit.metric("Documents", model.collection_stats.document_count)
    streamlit.metric("Chunks", model.collection_stats.chunk_count)
    streamlit.metric("Images", model.collection_stats.image_count)
    streamlit.metric("Dense Indexed", model.collection_stats.dense_indexed_chunk_count)
    streamlit.metric("BM25 Indexed", model.collection_stats.bm25_indexed_chunk_count)

    streamlit.subheader("System Health")
    _render_health_metric(streamlit, "Latest Query", model.latest_query)
    _render_health_metric(streamlit, "Latest Ingestion", model.latest_ingestion)


def _latest_health(history: list[TraceHistoryItem]) -> DashboardHealthSnapshot:
    """Convert the newest trace history row into a health snapshot."""

    if not history:
        return DashboardHealthSnapshot(
            trace_id=None,
            status="empty",
            duration_ms=None,
            started_at=None,
            error=None,
        )
    latest = history[0]
    return DashboardHealthSnapshot(
        trace_id=latest.trace_id,
        status=latest.status,
        duration_ms=latest.duration_ms,
        started_at=latest.started_at,
        error=latest.error,
    )


def _component_row(component: Any) -> dict[str, object]:
    """Convert one component DTO into the main Overview component table.

    Args:
        component: ``ComponentConfig``-compatible DTO from ``ConfigReaderService``.

    Returns:
        A compact table row. Detailed transform step rows are rendered in the
        adjacent expander instead of being embedded inside the main row.
    """

    return {
        "component": component.component,
        "provider": component.provider,
        "model": component.model,
        "enabled": component.enabled,
    }


def _find_component(components: tuple[Any, ...], component_name: str) -> Any | None:
    """Return one component DTO by stable component name.

    Args:
        components: Overview component DTOs.
        component_name: Stable component identifier to find.

    Returns:
        The matching component, or ``None`` when absent.
    """

    for component in components:
        if component.component == component_name:
            return component
    return None


def _transform_step_rows(component: Any) -> list[dict[str, object]]:
    """Build rows for the transform component expander.

    Args:
        component: Transform ``ComponentConfig`` whose ``details.steps`` value
            contains settings-order sub-transform dictionaries.

    Returns:
        Rows showing each sub-transform, provider, resolved model, model source,
        prompt path, and enabled state.
    """

    raw_steps = component.details.get("steps", [])
    if not isinstance(raw_steps, list | tuple):
        return []
    rows: list[dict[str, object]] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        rows.append(
            {
                "sub_transform": step.get("name"),
                "provider": step.get("provider"),
                "model": step.get("model"),
                "model_source": step.get("model_source"),
                "prompt_path": step.get("prompt_path"),
                "enabled": step.get("enabled"),
            }
        )
    return rows


def _render_health_metric(
    streamlit: Any,
    label: str,
    snapshot: DashboardHealthSnapshot,
) -> None:
    """Render one trace-derived health metric group."""

    streamlit.metric(f"{label} Status", snapshot.status)
    streamlit.metric(
        f"{label} Duration",
        "n/a" if snapshot.duration_ms is None else f"{snapshot.duration_ms:.1f} ms",
    )
    if snapshot.error:
        streamlit.warning({"trace_id": snapshot.trace_id, "error": snapshot.error})
    elif snapshot.trace_id:
        streamlit.write({"trace_id": snapshot.trace_id, "started_at": snapshot.started_at})
    else:
        streamlit.info(f"{label} trace is not available yet.")


def _streamlit() -> Any:
    """Import Streamlit only when a real render call needs it."""

    import streamlit

    return streamlit
