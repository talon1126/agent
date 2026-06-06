"""Load Markdown files into canonical RAG ``Document`` objects.

The Markdown loader is the first concrete source adapter used by local tests and
future ingestion flows. It reads UTF-8 Markdown text, extracts a lightweight
title from the first heading, and records source metadata needed by
``DocumentChunker`` and repositories. It does not split content or parse images;
more advanced Markdown image handling belongs to later ingestion tasks.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from src.core.errors import IngestionError
from src.core.types import Document
from src.libs.loader.base_loader import BaseLoader


def _stable_document_id(path: Path, content: str) -> str:
    """Build a stable document ID from source identity and content.

    Args:
        path: Resolved source file path.
        content: Canonical text loaded from the file.

    Returns:
        A SHA256-based document identifier stable across repeated loads of the
        same source content.
    """

    digest = sha256(f"{path.as_posix()}|{content}".encode()).hexdigest()
    return f"doc-{digest}"


def _first_markdown_heading(content: str) -> str | None:
    """Extract the first ATX Markdown heading without parsing the whole file.

    Args:
        content: Markdown document text.

    Returns:
        Heading text without leading ``#`` markers, or ``None`` when the file
        has no non-empty heading line.
    """

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return None


class MarkdownLoader(BaseLoader):
    """Convert one Markdown file into the shared ``Document`` contract."""

    def load(self, source: str | Path) -> Document:
        """Read a Markdown source file as UTF-8 and return a ``Document``.

        Args:
            source: Filesystem path ending in ``.md`` or another Markdown
                extension selected by ``LoaderFactory``.

        Returns:
            A validated ``Document`` with ``source_path``, ``source_type``, and
            optional ``title`` metadata.

        Raises:
            IngestionError: If the file cannot be read or the loaded content is
                invalid for ``Document`` construction.
        """

        path = Path(source).expanduser().resolve()
        try:
            content = path.read_text(encoding="utf-8")
            metadata = {
                "source_path": str(path),
                "source_type": "markdown",
            }
            title = _first_markdown_heading(content)
            if title:
                metadata["title"] = title
            return Document(
                id=_stable_document_id(path, content),
                text=content,
                metadata=metadata,
            )
        except Exception as error:
            raise IngestionError(
                "Unable to load Markdown document",
                context={"operation": "markdown_load", "source": str(source)},
                cause=error,
            ) from error
