"""Adapt pure text splits into validated business ``Chunk`` objects.

``DocumentChunker`` is the boundary between low-level text splitting and RAG
business metadata. It calls a ``BaseSplitter`` implementation to obtain
``list[str]`` segments, then adds stable IDs, inherited metadata, source ranges,
source references, ordering, and image-reference distribution.
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
    build_source_ref,
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
            source references, offsets, chunk indexes, and ``image_refs``.

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
            source_ref = build_source_ref(
                document,
                start_offset=start_offset,
                end_offset=end_offset,
                section_path=section_path,
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
                    source_ref=source_ref,
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
        start_offset = document.text.find(part, search_start)
        if start_offset < 0:
            raise IngestionError(
                "Unable to locate splitter segment in source document",
                context={
                    "document_id": document.id,
                    "chunk_index": chunk_index,
                    "search_start": search_start,
                },
            )
        end_offset = start_offset + len(part)
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
        ``Chunk.source_ref`` and document/image tables.
    """

    metadata: dict[str, object] = {"document_id": document.id}
    for key in ("collection", "doc_type", "topic"):
        value = document.metadata.get(key)
        if value is not None:
            metadata[key] = deepcopy(value)
    if "topic" not in metadata and document.metadata.get("title"):
        metadata["topic"] = str(document.metadata["title"])
    return metadata
