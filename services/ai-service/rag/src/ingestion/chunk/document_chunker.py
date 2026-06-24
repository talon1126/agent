"""Adapt pure text splits into validated business ``Chunk`` objects.

``DocumentChunker`` is the boundary between low-level text splitting and RAG
business metadata. It calls a ``BaseSplitter`` implementation to obtain
``list[str]`` segments, then adds stable IDs, inherited metadata, source ranges,
source metadata, ordering, and image-reference distribution.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

from src.core.errors import IngestionError
from src.core.types import Chunk, Document
from src.ingestion.chunk.chunk_id import build_chunk_id
from src.ingestion.chunk.splitter_step import (
    attach_section_path,
    distribute_image_refs,
)
from src.libs.splitter import BaseSplitter

_IMAGE_PLACEHOLDER_ONLY = re.compile(r"(?:\s*\[\[image:[^\]]+\]\]\s*)+")


@dataclass(frozen=True, slots=True)
class _LocatedPart:
    """Represent one splitter segment and its canonical document coordinates."""

    text: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class DocumentChunker:
    """Convert one canonical ``Document`` into ordered retrievable chunks.

    Args:
        splitter: Pure text splitter that returns ``list[str]`` and does not
            know about business objects.

    Notes:
        Chunk IDs use ``hash(source_path + section_path + content_hash)`` as
        required by the ingestion design. Source offsets always refer to the
        original ``Document.text`` even if later transform stages rewrite chunk
        text for retrieval quality.
    """

    splitter: BaseSplitter

    def chunk(self, document: Document) -> list[Chunk]:
        """Split a document and adapt each text part into a ``Chunk`` object.

        Args:
            document: Validated source document produced by a loader.

        Returns:
            Ordered validated chunks with stable IDs, inherited metadata,
            source metadata, offsets, chunk indexes, and ``image_refs``.

        Raises:
            IngestionError: If the splitter emits non-string, blank, duplicate
                out-of-order, or non-locatable text segments.
        """

        parts = _merge_image_only_parts(
            document.text,
            _locate_parts(document, self.splitter.split(document.text)),
        )
        chunks: list[Chunk] = []
        for chunk_index, part in enumerate(parts):
            start_offset = part.start_offset
            end_offset = part.end_offset

            metadata = _chunk_metadata_from_document(document)
            metadata["chunk_index"] = chunk_index
            section_path = attach_section_path(
                metadata,
                document=document,
                start_offset=start_offset,
            )
            distribute_image_refs(
                metadata,
                chunk_text=part.text,
            )
            chunks.append(
                Chunk(
                    id=build_chunk_id(
                        source_path=str(
                            document.metadata.get("source_path", document.id)
                        ),
                        section_path=section_path,
                        text=part.text,
                    ),
                    text=part.text,
                    metadata=metadata,
                    chunk_index=chunk_index,
                    start_offset=start_offset,
                    end_offset=end_offset,
                )
            )
        return chunks


def _locate_parts(document: Document, parts: list[str]) -> list[_LocatedPart]:
    """Validate splitter output and locate every segment in canonical text."""

    located: list[_LocatedPart] = []
    search_start = 0
    for chunk_index, part in enumerate(parts):
        if not isinstance(part, str) or not part.strip():
            raise IngestionError(
                "Splitter returned an invalid text segment",
                context={"document_id": document.id, "chunk_index": chunk_index},
            )
        located_range = _locate_part_range(document.text, part, search_start)
        if located_range is None:
            raise IngestionError(
                "Unable to locate splitter segment in source document",
                context={
                    "document_id": document.id,
                    "chunk_index": chunk_index,
                    "search_start": search_start,
                },
            )
        start_offset, end_offset = located_range
        located.append(
            _LocatedPart(
                text=part,
                start_offset=start_offset,
                end_offset=end_offset,
            )
        )
        # Recursive splitters may overlap. Advancing from the previous start
        # preserves ordering while still allowing the next segment to overlap.
        search_start = start_offset + 1
    return located


def _locate_part_range(
    document_text: str,
    part: str,
    search_start: int,
) -> tuple[int, int] | None:
    """Locate exact or context-prefixed splitter output in source text.

    Markdown section splitting may repeat heading context or table headers in
    overflow chunks so each retrievable text segment is understandable alone.
    Those contextual strings are not always contiguous source substrings. This
    helper first preserves the exact-match path used by ordinary splitters, then
    falls back to locating the first and last source-owned content lines inside
    the contextual chunk.
    """

    start_offset = document_text.find(part, search_start)
    if start_offset >= 0:
        return start_offset, start_offset + len(part)

    content_lines = _source_owned_lines(part)
    if not content_lines:
        return None

    first_start = -1
    first_line = ""
    for line in content_lines:
        first_start = document_text.find(line, search_start)
        if first_start >= 0:
            first_line = line
            break
    if first_start < 0:
        return None

    end_offset = first_start + len(first_line)
    cursor = first_start + 1
    for line in content_lines[1:]:
        candidate = document_text.find(line, cursor)
        if candidate >= 0:
            end_offset = candidate + len(line)
            cursor = candidate + 1
    return first_start, end_offset


def _source_owned_lines(part: str) -> list[str]:
    """Return lines likely to exist in the source document body.

    Synthetic heading context and repeated Markdown table headers are useful for
    retrieval but poor anchors for source offset detection. Data rows, prose,
    list items, and image placeholders are better anchors because they originate
    from the source section itself.
    """

    lines: list[str] = []
    previous_table_header_index: int | None = None
    for raw_line in part.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            previous_table_header_index = None
            continue
        if _looks_like_table_separator(line):
            if previous_table_header_index is not None:
                lines.pop(previous_table_header_index)
                previous_table_header_index = None
            continue
        lines.append(line)
        previous_table_header_index = len(lines) - 1 if "|" in line else None
    return lines


def _looks_like_table_separator(line: str) -> bool:
    """Return whether a line is a Markdown table separator row."""

    if "|" not in line:
        return False
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)

def _merge_image_only_parts(
    document_text: str,
    parts: list[_LocatedPart],
) -> list[_LocatedPart]:
    """Attach image-only splitter output to adjacent retrievable source text.

    Trailing image-only segments extend the previous text part. Leading
    image-only segments extend the next text part. Rebuilding from the
    canonical source range preserves exact placeholder order and whitespace.
    """

    merged: list[_LocatedPart] = []
    leading_images: _LocatedPart | None = None
    for part in parts:
        if _IMAGE_PLACEHOLDER_ONLY.fullmatch(part.text):
            if merged:
                previous = merged[-1]
                end_offset = max(previous.end_offset, part.end_offset)
                merged[-1] = _LocatedPart(
                    text=document_text[previous.start_offset:end_offset],
                    start_offset=previous.start_offset,
                    end_offset=end_offset,
                )
            elif leading_images is None:
                leading_images = part
            else:
                leading_images = _LocatedPart(
                    text=document_text[leading_images.start_offset:part.end_offset],
                    start_offset=leading_images.start_offset,
                    end_offset=part.end_offset,
                )
            continue

        if leading_images is not None:
            start_offset = min(leading_images.start_offset, part.start_offset)
            end_offset = max(leading_images.end_offset, part.end_offset)
            part = _LocatedPart(
                text=document_text[start_offset:end_offset],
                start_offset=start_offset,
                end_offset=end_offset,
            )
            leading_images = None
        merged.append(part)
    if leading_images is not None:
        merged.append(leading_images)
    return merged


def _chunk_metadata_from_document(document: Document) -> dict[str, object]:
    """Build the intentionally small metadata payload stored on each chunk.

    Args:
        document: Source document whose metadata may contain loader-only fields
            such as source path, source hash, headings, and document images.

    Returns:
        A new dictionary containing only fields used for retrieval filtering
        and high-level business explanation. Source details remain in
        Source details remain in first-class document tables and selected
        chunk metadata fields.
    """

    metadata: dict[str, object] = {"document_id": document.id}
    for key in ("collection", "source_path", "doc_type", "topic"):
        value = document.metadata.get(key)
        if value is not None:
            metadata[key] = deepcopy(value)
    if "topic" not in metadata and document.metadata.get("title"):
        metadata["topic"] = str(document.metadata["title"])
    return metadata
