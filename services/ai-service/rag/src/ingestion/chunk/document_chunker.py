"""Adapt pure text splits into validated business ``Chunk`` objects.

``DocumentChunker`` is the boundary between low-level text splitting and RAG
business metadata. It calls a ``BaseSplitter`` implementation to obtain
``list[str]`` segments, then adds stable IDs, inherited metadata, source ranges,
source references, ordering, and image-reference distribution.
"""

from __future__ import annotations

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

        parts = self.splitter.split(document.text)
        chunks: list[Chunk] = []
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
            # Recursive splitters can produce overlapping segments. Advancing
            # from the previous start, rather than the previous end, preserves
            # ordering while still allowing the next segment to begin inside the
            # previous source range.
            search_start = start_offset + 1

            metadata = deepcopy(document.metadata)
            metadata["chunk_index"] = chunk_index
            section_path = attach_section_path(
                metadata,
                document=document,
                start_offset=start_offset,
            )
            distribute_image_refs(
                metadata,
                start_offset=start_offset,
                end_offset=end_offset,
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
                        text=part,
                    ),
                    text=part,
                    metadata=metadata,
                    chunk_index=chunk_index,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    source_ref=source_ref,
                )
            )
        return chunks
