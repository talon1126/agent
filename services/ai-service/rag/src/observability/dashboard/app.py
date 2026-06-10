"""Compose the local Streamlit Dashboard application shell.

This module is the importable app target passed to ``streamlit run`` by
``src.scripts.run_dashboard``. It intentionally keeps startup lightweight:
loading the app verifies that all six Dashboard page modules are importable,
but it does not open PostgreSQL, run ingestion jobs, delete documents, or call
external model providers during module import.

The heavier model-building functions stay inside each page module. Later tasks
can wire sidebar navigation and service-backed page rendering here while this
entry continues to provide a stable launch target for local operators and tests.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib import import_module
from typing import Any

from src.core.config import load_settings
from src.observability.pages.data_browser import (
    build_data_browser_page_model,
    render_data_browser_page,
)
from src.observability.pages.evaluation import (
    build_evaluation_page_model,
    render_evaluation_page,
)
from src.observability.pages.ingestion_manage import (
    build_ingestion_manage_page_model,
    render_ingestion_manage_page,
)
from src.observability.pages.ingestion_trace import (
    build_ingestion_trace_page_model,
    render_ingestion_trace_page,
)
from src.observability.pages.overview import (
    build_overview_page_model,
    render_overview_page,
)
from src.observability.pages.query_trace import (
    build_query_trace_page_model,
    render_query_trace_page,
)
from src.observability.services import (
    ConfigReaderService,
    DataBrowserService,
    EvaluationService,
    IngestionOperationService,
    TraceReaderService,
)
from src.storage.postgres import PostgresPool, init_schema

DASHBOARD_PAGE_MODULES: tuple[str, ...] = (
    "overview",
    "ingestion_manage",
    "data_browser",
    "query_trace",
    "ingestion_trace",
    "evaluation",
)

DASHBOARD_PAGE_LABELS: dict[str, str] = {
    "overview": "System Overview",
    "ingestion_manage": "Ingestion Management",
    "data_browser": "Data Browser",
    "query_trace": "Query Trace",
    "ingestion_trace": "Ingestion Trace",
    "evaluation": "Evaluation",
}

PageRenderer = Callable[[str, Any], None]


def load_dashboard_pages(
    page_modules: Sequence[str] = DASHBOARD_PAGE_MODULES,
) -> tuple[str, ...]:
    """Import every configured Dashboard page module and return its names.

    Args:
        page_modules: Ordered page module names under
            ``src.observability.pages``. The default mirrors the six-page
            Dashboard contract documented in ``DEV_SPEC.md``.

    Returns:
        Ordered names of the successfully imported page modules.

    Raises:
        ModuleNotFoundError: If a page listed in the Dashboard contract is
            missing from ``src.observability.pages``.
        ImportError: If a page module exists but fails during import.
    """

    loaded_pages: list[str] = []
    for page_name in page_modules:
        import_module(f"src.observability.pages.{page_name}")
        loaded_pages.append(page_name)
    return tuple(loaded_pages)


def main(
    ui: Any | None = None,
    *,
    page_renderer: PageRenderer | None = None,
) -> tuple[str, ...]:
    """Render the lightweight Dashboard shell and validate page imports.

    Args:
        ui: Optional Streamlit-compatible object. Tests pass a fake recorder so
            the app can be exercised without importing Streamlit's runtime or
            opening a browser. ``None`` imports the real ``streamlit`` module.
        page_renderer: Optional renderer override used by tests to verify
            navigation dispatch without opening PostgreSQL. ``None`` renders
            the selected page through service-backed Dashboard page builders.

    Returns:
        Ordered names of the page modules that were imported successfully.

    Side Effects:
        Writes the Dashboard shell and selected page through the provided
        Streamlit-like UI object. Production rendering opens PostgreSQL for the
        selected page only and closes the pool before the run finishes.
    """

    active_ui = ui if ui is not None else _load_streamlit()
    if hasattr(active_ui, "set_page_config"):
        active_ui.set_page_config(
            page_title="AImodel RAG Dashboard",
            layout="wide",
        )

    loaded_pages = load_dashboard_pages()
    active_ui.title("AImodel RAG Dashboard")
    active_ui.caption(
        "Local observability workspace for ingestion, query traces, "
        "indexed data, and evaluation."
    )
    selected_page = select_dashboard_page(active_ui, loaded_pages)
    active_renderer = page_renderer or render_dashboard_page
    active_renderer(selected_page, active_ui)
    return loaded_pages


def select_dashboard_page(
    ui: Any,
    page_modules: Sequence[str] = DASHBOARD_PAGE_MODULES,
) -> str:
    """Render the sidebar page selector and return the selected page key.

    Args:
        ui: Streamlit-compatible module. Real Streamlit exposes ``sidebar``;
            tests may pass a fake object whose ``sidebar`` points back to
            itself.
        page_modules: Ordered page keys that should be reachable in the
            browser.

    Returns:
        Selected page key. The first configured page is used when the widget
        returns ``None`` so the Dashboard always has a deterministic default.
    """

    sidebar = getattr(ui, "sidebar", ui)
    selected = sidebar.radio(
        "Page",
        options=list(page_modules),
        format_func=lambda page_name: DASHBOARD_PAGE_LABELS.get(
            str(page_name),
            str(page_name),
        ),
    )
    return str(selected or page_modules[0])


def render_dashboard_page(page_name: str, ui: Any) -> None:
    """Build services and render the selected Dashboard page.

    Args:
        page_name: Stable page key selected by ``select_dashboard_page``.
        ui: Streamlit-compatible module used by the page render function.

    Raises:
        ValueError: If an unknown page key is selected. The sidebar only emits
            configured keys, so this protects direct programmatic calls.

    Side Effects:
        Loads validated settings without requiring model API environment
        variables, opens PostgreSQL, initializes schema idempotently, renders
        one page, and closes the pool.
    """

    settings = load_settings(validate_environment=False)
    collection_id = settings.project.default_collection
    pool = PostgresPool.from_settings(settings.database)
    pool.open()
    try:
        init_schema(pool)
        config_reader = ConfigReaderService(settings_loader=lambda: settings)
        data_browser = DataBrowserService(pool)
        trace_reader = TraceReaderService(pool)
        evaluation_service = EvaluationService(pool)
        ingestion_operation_service = IngestionOperationService()

        if page_name == "overview":
            render_overview_page(
                build_overview_page_model(
                    config_reader=config_reader,
                    data_browser=data_browser,
                    trace_reader=trace_reader,
                    collection_id=collection_id,
                ),
                ui=ui,
            )
            return
        if page_name == "ingestion_manage":
            render_ingestion_manage_page(
                build_ingestion_manage_page_model(
                    config_reader=config_reader,
                    data_browser=data_browser,
                    collection_id=collection_id,
                ),
                ui=ui,
                ingestion_service=ingestion_operation_service,
            )
            return
        if page_name == "data_browser":
            render_data_browser_page(
                build_data_browser_page_model(
                    data_browser=data_browser,
                    collection_id=collection_id,
                ),
                ui=ui,
            )
            return
        if page_name == "query_trace":
            render_query_trace_page(
                build_query_trace_page_model(
                    trace_reader=trace_reader,
                    collection_id=collection_id,
                ),
                ui=ui,
            )
            return
        if page_name == "ingestion_trace":
            render_ingestion_trace_page(
                build_ingestion_trace_page_model(
                    trace_reader=trace_reader,
                    collection_id=collection_id,
                ),
                ui=ui,
            )
            return
        if page_name == "evaluation":
            render_evaluation_page(
                build_evaluation_page_model(
                    evaluation_service=evaluation_service,
                    collection_id=collection_id,
                ),
                ui=ui,
            )
            return
        raise ValueError(f"Unknown Dashboard page: {page_name}")
    finally:
        pool.close()


def _load_streamlit() -> Any:
    """Import Streamlit lazily so tests can import this module cheaply.

    Returns:
        The real ``streamlit`` module used when the app is executed by
        ``streamlit run``.
    """

    import streamlit as st

    return st


if __name__ == "__main__":
    main()
