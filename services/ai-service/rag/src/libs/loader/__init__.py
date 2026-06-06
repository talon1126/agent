"""Define the loader component namespace for source-to-Document adapters.

Loader implementations will convert supported source formats into validated
``Document`` objects after ingestion-level deduplication decides a source should
be processed. This package intentionally contains only the namespace boundary in
B7; B8 adds the base interface, factory, and concrete Markdown/PDF loaders.
"""

from src.libs.loader.base_loader import BaseLoader
from src.libs.loader.fake_loader import FakeLoader
from src.libs.loader.loader_factory import LoaderFactory
from src.libs.loader.markdown_loader import MarkdownLoader
from src.libs.loader.pdf_loader import PdfLoader

__all__ = (
    "BaseLoader",
    "FakeLoader",
    "LoaderFactory",
    "MarkdownLoader",
    "PdfLoader",
)
