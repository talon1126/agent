"""Provide a deterministic transform implementation for unit tests.

``FakeTransform`` proves transform orchestration, factory wiring, and metadata
copy behavior without using LLMs, Vision models, or rule-based denoising logic.
It returns new ``Chunk`` objects so tests can detect accidental in-place
mutation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.core.types import Chunk
from src.libs.transform.base_transform import BaseTransform


class FakeTransform(BaseTransform):
    """Copy chunks and merge configured metadata into each copy."""

    def __init__(self, *, metadata_updates: dict[str, Any] | None = None) -> None:
        """Configure deterministic metadata enrichment.

        Args:
            metadata_updates: Metadata values merged into every returned chunk.
                ``None`` leaves metadata unchanged while still returning copies.
        """

        self._metadata_updates = dict(metadata_updates or {})

    def transform(
        self,
        chunks: list[Chunk],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Return chunk copies with configured metadata updates applied.

        Args:
            chunks: Ordered input chunks.
            context: Optional runtime context. The fake does not read it, but it
                preserves the same signature as real transforms.

        Returns:
            New ``Chunk`` objects with copied metadata and deterministic updates.
        """

        transformed: list[Chunk] = []
        for chunk in chunks:
            metadata = deepcopy(chunk.metadata)
            metadata.update(self._metadata_updates)
            transformed.append(chunk.model_copy(update={"metadata": metadata}, deep=True))
        return transformed
