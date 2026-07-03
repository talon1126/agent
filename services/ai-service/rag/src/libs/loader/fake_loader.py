"""Provide a deterministic loader implementation for unit tests.

``FakeLoader`` lets factory and ingestion tests exercise loader orchestration
without touching the filesystem, PDF conversion libraries, or external parser
state. It is intentionally simple and should not be selected by production
settings.
"""

from __future__ import annotations

from pathlib import Path

from src.core.types import Document
from src.libs.loader.base_loader import BaseLoader


class FakeLoader(BaseLoader):
    """Return a preconstructed ``Document`` regardless of the supplied source."""

    def __init__(self, document: Document | None = None) -> None:
        """Configure the fake loader's stable return value.

        Args:
            document: Optional validated document fixture. When omitted, the
                loader returns a small default document suitable for smoke tests.
        """

        self._document = document or Document(
            id="fake-document",
            text="Fake loader document.",
            metadata={"source_path": "memory://fake-document.md", "source_type": "fake"},
        )

    def load(self, source: str | Path) -> Document:
        """Return the configured document without reading ``source``.

        Args:
            source: Ignored path or URI. It remains part of the signature so the
                fake satisfies the same contract as real loaders.

        Returns:
            The configured immutable test fixture document.
        """

        return self._document
