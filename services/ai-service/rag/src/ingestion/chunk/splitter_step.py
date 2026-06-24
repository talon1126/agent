"""Provide business helpers and the pipeline step for document chunking.

The low-level splitter interface intentionally returns only ``list[str]``.
This module contains the ingestion-specific operations that attach source
identity, section metadata, and image references before validated ``Chunk``
objects continue to transform and indexing stages.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.core.types import Chunk, Document

if TYPE_CHECKING:
    from src.ingestion.chunk.document_chunker import DocumentChunker


def attach_section_path(
    metadata: dict[str, Any],
    *,
    document: Document,
    start_offset: int,
) -> list[str]:
    """Attach the active logical heading path to chunk metadata.

    Args:
        metadata: Deep-copied document metadata owned by the target chunk.
        document: Source document containing ordered heading metadata.
        start_offset: Start-inclusive chunk position in ``document.text``.

    Returns:
        The active section path. An empty list means the chunk appears before
        the first heading and no document-wide fallback path exists.

    Side Effects:
        Writes ``metadata["section_path"]`` when a path is available and
        removes a stale empty value otherwise.
    """

    section_path = _active_heading_path(
        document.metadata.get("headings"),
        start_offset=start_offset,
    )
    if not section_path:
        section_path = _normalize_path(
            document.metadata.get(
                "section_path",
                document.metadata.get("heading_path"),
            )
        )

    if section_path:
        metadata["section_path"] = section_path
    else:
        metadata.pop("section_path", None)
    return section_path



_IMAGE_PLACEHOLDER = re.compile(r"\[\[image:(?P<image_id>[^\]]+)\]\]")


def distribute_image_refs(
    metadata: dict[str, Any],
    *,
    chunk_text: str,
) -> list[str]:
    """Attach image IDs found in the chunk's image placeholders.

    Args:
        metadata: Chunk-owned metadata being assembled by ``DocumentChunker``.
        chunk_text: Chunk source text that may contain ``[[image:...]]``
            placeholders.

    Returns:
        Ordered unique image IDs assigned to the chunk.

    Side Effects:
        Writes ``metadata["image_refs"]`` only when at least one placeholder
        appears in ``chunk_text``. The full document-level ``images`` list is
        never copied into chunk metadata.
    """

    image_refs: list[str] = []
    seen: set[str] = set()
    for match in _IMAGE_PLACEHOLDER.finditer(chunk_text):
        image_id = match.group("image_id").strip()
        if image_id and image_id not in seen:
            image_refs.append(image_id)
            seen.add(image_id)

    if image_refs:
        metadata["image_refs"] = image_refs
    else:
        metadata.pop("image_refs", None)
    return image_refs


@dataclass(frozen=True, slots=True)
class SplitterStep:
    """Expose DocumentChunker through a composable ingestion pipeline stage.

    Args:
        chunker: Business adapter configured with one pure text splitter.
    """

    chunker: DocumentChunker

    def run(self, document: Document) -> list[Chunk]:
        """Convert one canonical document into ordered business chunks.

        Args:
            document: Loader output that passed document-level validation.

        Returns:
            Ordered chunks produced by the configured ``DocumentChunker``.
        """

        return self.chunker.chunk(document)


def _active_heading_path(
    headings: Any,
    *,
    start_offset: int,
) -> list[str]:
    """Select the active H2-plus section path for a chunk offset.

    Loader heading metadata keeps the full Markdown path so document-level H1
    titles remain available to dashboards and source browsers. Chunk metadata
    uses ``section_path`` only for retrievable section structure, so the H1
    document title is dropped and content before the first H2 has no section.
    """

    if not isinstance(headings, list):
        return []

    active_path: list[str] = []
    active_offset = -1
    for heading in headings:
        if not isinstance(heading, Mapping):
            continue
        heading_offset = heading.get("text_offset")
        if not isinstance(heading_offset, int) or heading_offset < 0:
            continue
        path = _section_path_from_heading(heading)
        if heading_offset <= start_offset and heading_offset >= active_offset and path:
            active_path = path
            active_offset = heading_offset
    return active_path


def _section_path_from_heading(heading: Mapping[str, Any]) -> list[str]:
    """Return a normalized section path that starts at Markdown H2.

    Args:
        heading: One loader-produced heading metadata object. The object may
            contain a full ``path`` that starts at H1 and a numeric ``level``.

    Returns:
        Ordered section titles excluding the document-level H1 title. Invalid
        or H1-only headings return an empty list so chunks before H2 do not get
        misleading section metadata.
    """

    path = _normalize_path(heading.get("path"))
    level = heading.get("level")
    if not path:
        return []
    if not isinstance(level, int):
        return path[1:] if len(path) > 1 else []
    if level <= 1:
        return []
    component_count = min(level - 1, len(path))
    return path[-component_count:]


def _normalize_path(value: Any) -> list[str]:
    """Normalize metadata section values to an ordered list of strings."""

    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence):
        return [str(component) for component in value if str(component)]
    return [str(value)]
