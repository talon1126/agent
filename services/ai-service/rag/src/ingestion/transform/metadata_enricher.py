"""Enrich chunk metadata with trace-safe document and ingestion context.

``MetadataEnricher`` adds contextual fields needed by retrieval filters,
Dashboard inspection, and later answer citations. It never changes chunk text,
source offsets, IDs, or source-owned metadata values.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.core.types import Chunk
from src.libs.transform.base_transform import BaseTransform


class MetadataEnricher(BaseTransform):
    """Copy selected runtime context into missing chunk metadata fields."""

    def __init__(
        self,
        *,
        context_fields: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        """Configure context keys eligible for metadata enrichment.

        Args:
            context_fields: Ordered trace-safe context keys to copy. The default
                covers filterable business fields. Source path and title stay
                in ``Chunk.source_ref`` or document storage, not chunk metadata.
        """

        self._context_fields = tuple(
            context_fields
            or (
                "topic",
                "collection",
                "doc_type",
                "document_id",
            )
        )

    def transform(
        self,
        chunks: list[Chunk],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Return chunk copies enriched with missing context values.

        Args:
            chunks: Ordered chunks produced by ``DocumentChunker`` or a prior
                transform.
            context: Runtime metadata for the current ingestion request.

        Returns:
            Deep-copied chunks whose missing configured fields are populated
            from context. Existing chunk metadata always wins because it came
            from the canonical source document.
        """

        source = context or {}
        transformed: list[Chunk] = []
        for chunk in chunks:
            metadata = deepcopy(chunk.metadata)
            for field in self._context_fields:
                value = source.get(field)
                if value is not None:
                    metadata.setdefault(field, deepcopy(value))
            transformed.append(chunk.model_copy(update={"metadata": metadata}, deep=True))
        return transformed
