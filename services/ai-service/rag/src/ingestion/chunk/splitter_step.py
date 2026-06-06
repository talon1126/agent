"""Provide business helpers and the pipeline step for document chunking.

The low-level splitter interface intentionally returns only ``list[str]``.
This module contains the ingestion-specific operations that attach source
identity, section hierarchy, and image references before validated ``Chunk``
objects continue to transform and indexing stages.
"""

from __future__ import annotations

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


def build_source_ref(
    document: Document,
    *,
    start_offset: int,
    end_offset: int,
    section_path: Sequence[str],
) -> dict[str, Any]:
    """Build the citation and trace reference stored beside one chunk.

    Args:
        document: Canonical source document being adapted.
        start_offset: Start-inclusive source text position.
        end_offset: End-exclusive source text position.
        section_path: Active logical heading path selected for this chunk.

    Returns:
        A JSON-compatible mapping containing stable document identity, source
        path, source range, and optional collection and section information.
    """

    source_ref: dict[str, Any] = {
        "document_id": document.id,
        "source_path": document.metadata.get("source_path", document.id),
    }
    if section_path:
        source_ref["section_path"] = list(section_path)
    if collection := document.metadata.get("collection"):
        source_ref["collection"] = collection
    source_ref["start_offset"] = start_offset
    source_ref["end_offset"] = end_offset
    return source_ref


def distribute_image_refs(
    metadata: dict[str, Any],
    *,
    start_offset: int,
    end_offset: int,
) -> list[str]:
    """Attach image IDs whose placeholder ranges intersect a chunk.

    Args:
        metadata: Deep-copied document metadata containing normalized
            ``images`` entries when the loader extracted images.
        start_offset: Start-inclusive chunk position in source text.
        end_offset: End-exclusive chunk position in source text.

    Returns:
        Ordered unique image IDs assigned to the chunk.

    Side Effects:
        Writes ``metadata["image_refs"]`` only when at least one placeholder
        intersects the chunk. Removing empty references prevents downstream
        caption and multimodal stages from treating text-only chunks as image
        work.
    """

    image_refs: list[str] = []
    seen: set[str] = set()
    for image in metadata.get("images", []):
        image_start = int(image["text_offset"])
        image_end = image_start + int(image["text_length"])
        image_id = str(image["id"])
        if image_start < end_offset and image_end > start_offset and image_id not in seen:
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
    """Select the last valid heading whose source offset precedes the chunk."""

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
        path = _normalize_path(heading.get("path"))
        if heading_offset <= start_offset and heading_offset >= active_offset and path:
            active_path = path
            active_offset = heading_offset
    return active_path


def _normalize_path(value: Any) -> list[str]:
    """Normalize metadata section values to an ordered list of strings."""

    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence):
        return [str(component) for component in value if str(component)]
    return [str(value)]
