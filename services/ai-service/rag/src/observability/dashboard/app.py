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

from collections.abc import Sequence
from importlib import import_module
from typing import Any

DASHBOARD_PAGE_MODULES: tuple[str, ...] = (
    "overview",
    "ingestion_manage",
    "data_browser",
    "query_trace",
    "ingestion_trace",
    "evaluation",
)


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


def main(ui: Any | None = None) -> tuple[str, ...]:
    """Render the lightweight Dashboard shell and validate page imports.

    Args:
        ui: Optional Streamlit-compatible object. Tests pass a fake recorder so
            the app can be exercised without importing Streamlit's runtime or
            opening a browser. ``None`` imports the real ``streamlit`` module.

    Returns:
        Ordered names of the page modules that were imported successfully.

    Side Effects:
        Writes a minimal title, caption, and page list to the provided
        Streamlit-like UI object. It performs no database or provider I/O.
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
    active_ui.write({"loaded_pages": list(loaded_pages)})
    return loaded_pages


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
