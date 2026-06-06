"""Define the provider-independent transform contract for chunk enhancement.

Transform implementations operate after initial document chunking and before
embedding/indexing. They may enrich metadata, rewrite text, merge semantically
related chunks, denoise parser artifacts, or attach image-caption metadata. The
contract remains provider-independent so orchestration code can compose
transforms without depending on LLM, Vision, or rule-based implementation
details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.core.types import Chunk


class BaseTransform(ABC):
    """Provide the minimal unified interface for all chunk transforms."""

    @abstractmethod
    def transform(
        self,
        chunks: list[Chunk],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Transform ordered chunks and return a new ordered chunk list.

        Args:
            chunks: Ordered business chunks produced by ``DocumentChunker`` or a
                previous transform.
            context: Optional trace-safe runtime metadata such as collection,
                trace ID, document ID, or configured transform step name.

        Returns:
            Ordered transformed chunks. Implementations should avoid mutating
            input chunk objects in place so trace and retry code can compare
            before/after states.

        Raises:
            IngestionError: Implementations raise this when transform execution
                fails and cannot be safely skipped by orchestration.
        """
