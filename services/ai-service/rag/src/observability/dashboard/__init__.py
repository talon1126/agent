"""Provide the local Streamlit Dashboard application package.

The dashboard package contains the Streamlit app composition entry used by the
operator startup script. Page-specific rendering code remains in
``src.observability.pages`` so page tests can exercise UI behavior without
starting Streamlit or opening PostgreSQL connections.
"""

from src.observability.dashboard.app import DASHBOARD_PAGE_MODULES, main

__all__ = ["DASHBOARD_PAGE_MODULES", "main"]
