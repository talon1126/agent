"""Load PDF files through MarkItDown into canonical RAG ``Document`` objects.

PDF parsing is intentionally isolated behind this loader so ingestion pipelines
do not depend on MarkItDown APIs. B8 provides the adapter boundary and a minimal
conversion path; later tasks add richer page metadata, image extraction, and
placeholder injection.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from src.core.errors import IngestionError
from src.core.types import Document
from src.libs.loader.base_loader import BaseLoader


class PdfLoader(BaseLoader):
    """Convert one PDF source into Markdown-like document text."""

    def load(self, source: str | Path) -> Document:
        """Convert a PDF source through MarkItDown and return a ``Document``.

        Args:
            source: Filesystem path selected as a PDF by ``LoaderFactory``.

        Returns:
            A validated ``Document`` whose text is the Markdown/plain-text
            representation returned by MarkItDown.

        Raises:
            IngestionError: If MarkItDown is unavailable, conversion fails, or
                the converted content is invalid for ``Document`` construction.
        """

        path = Path(source).expanduser().resolve()
        try:
            from markitdown import MarkItDown

            converted: Any = MarkItDown().convert(str(path))
            content = getattr(converted, "text_content", None) or str(converted)
            digest = sha256(f"{path.as_posix()}|{content}".encode()).hexdigest()
            return Document(
                id=f"doc-{digest}",
                text=content,
                metadata={
                    "source_path": str(path),
                    "source_type": "pdf",
                },
            )
        except Exception as error:
            raise IngestionError(
                "Unable to load PDF document",
                context={"operation": "pdf_load", "source": str(source)},
                cause=error,
            ) from error
