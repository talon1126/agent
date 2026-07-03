"""Define the minimal loader interface used by ingestion orchestration.

Loaders are source adapters: they convert files or future URI-like sources into
validated ``Document`` objects after upstream deduplication has decided that a
source should be processed. They do not split text, create chunks, generate
embeddings, write PostgreSQL rows, or manage document lifecycle state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.core.types import Document


class BaseLoader(ABC):
    """Provide the smallest common contract for source-to-Document loaders.

    Concrete loaders hide source-format details such as Markdown parsing, PDF
    conversion, encoding, and image extraction. Ingestion code receives only the
    normalized ``Document`` contract and therefore does not depend on a specific
    parser or file type.
    """

    @abstractmethod
    def load(self, source: str | Path) -> Document:
        """Load one source into a validated domain document.

        Args:
            source: Filesystem path or future URI-like source identifier.

        Returns:
            A validated ``Document`` containing canonical text and extensible
            metadata.

        Raises:
            IngestionError: Implementations raise this when a source cannot be
                read, parsed, converted, or normalized.
        """
