"""Define the pure text splitter interface for ingestion chunking.

Splitter implementations only transform raw text into ordered text segments.
They must not create ``Document`` or ``Chunk`` objects, attach metadata, assign
image references, or generate chunk IDs. Those business responsibilities belong
to ``DocumentChunker``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseSplitter(ABC):
    """Provide the minimal ``str -> list[str]`` splitter contract."""

    @abstractmethod
    def split(self, text: str) -> list[str]:
        """Split raw text into ordered text segments.

        Args:
            text: Non-empty source text produced by a loader.

        Returns:
            Ordered text segments. Implementations should omit blank segments.

        Raises:
            IngestionError: Implementations raise this when splitting fails or
                produces invalid output.
        """
