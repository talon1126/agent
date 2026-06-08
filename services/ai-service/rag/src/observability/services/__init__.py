"""Expose Dashboard-facing read services for observability pages.

The services package is intentionally read-only at F6. Streamlit pages can use
these classes to inspect settings, documents, chunks, images, and index status
without importing storage repositories directly or opening their own SQL
queries.
"""

from src.observability.services.config_reader import (
    ComponentConfig,
    ConfigOverview,
    ConfigReaderService,
)
from src.observability.services.data_browser_service import (
    ChunkBrowserRow,
    CollectionStats,
    DataBrowserService,
    DocumentBrowserRow,
    ImageBrowserRow,
)

__all__ = [
    "ChunkBrowserRow",
    "CollectionStats",
    "ComponentConfig",
    "ConfigOverview",
    "ConfigReaderService",
    "DataBrowserService",
    "DocumentBrowserRow",
    "ImageBrowserRow",
]
