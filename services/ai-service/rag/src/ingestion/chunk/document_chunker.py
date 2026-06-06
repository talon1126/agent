"""Adapt pure text splits into validated business ``Chunk`` objects.

``DocumentChunker`` is the boundary between low-level text splitting and RAG
business metadata. It calls a ``BaseSplitter`` implementation to obtain
``list[str]`` segments, then adds stable IDs, inherited metadata, source ranges,
source references, ordering, and image-reference distribution.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from src.core.errors import IngestionError
from src.core.types import Chunk, Document
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
            metadata["image_refs"] = self._image_refs_for_range(metadata, start_offset, end_offset)

            chunks.append(
                Chunk(
                    id=self._chunk_id(document, part),
                    text=part,
                    metadata=metadata,
                    chunk_index=chunk_index,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    source_ref=self._source_ref(document, start_offset, end_offset),
                )
            )
        return chunks

    @staticmethod
    def _chunk_id(document: Document, text: str) -> str:
        """Generate a stable chunk ID from source, section path, and content.

        Args:
            document: Source document whose metadata contains source details.
            text: Chunk text returned by the splitter.

        Returns:
            SHA256 hex digest over source path, section path, and content hash.
        """

        source_path = str(document.metadata.get("source_path", document.id))
        section_path = DocumentChunker._section_path(document.metadata)
        content_hash = sha256(text.encode()).hexdigest()
        return sha256(f"{source_path}|{section_path}|{content_hash}".encode()).hexdigest()

    @staticmethod
    def _section_path(metadata: dict[str, Any]) -> str:
        """Serialize the heading or section path used in stable chunk IDs.

        Args:
            metadata: Document metadata inherited from the loader.

        Returns:
            A slash-joined section path, or an empty string when no section path
            exists yet.
        """

        value = metadata.get("section_path", metadata.get("heading_path", []))
        if isinstance(value, list | tuple):
            return "/".join(str(part) for part in value)
        return str(value)

    @staticmethod
    def _source_ref(document: Document, start_offset: int, end_offset: int) -> dict[str, Any]:
        """Build a citation-ready source reference for one chunk.

        Args:
            document: Source document being chunked.
            start_offset: Start-inclusive source offset.
            end_offset: End-exclusive source offset.

        Returns:
            A mapping used by storage, trace output, and later citation builders.
        """

        return {
            "document_id": document.id,
            "source_path": document.metadata.get("source_path", document.id),
            "start_offset": start_offset,
            "end_offset": end_offset,
        }

    @staticmethod
    def _image_refs_for_range(
        metadata: dict[str, Any],
        start_offset: int,
        end_offset: int,
    ) -> list[str]:
        """Return image IDs whose placeholders overlap a chunk source range.

        Args:
            metadata: Inherited document metadata, possibly containing the
                normalized ``images`` list.
            start_offset: Chunk start-inclusive source offset.
            end_offset: Chunk end-exclusive source offset.

        Returns:
            Ordered image IDs whose placeholder ranges overlap the chunk range.
        """

        image_refs: list[str] = []
        for image in metadata.get("images", []):
            image_start = image["text_offset"]
            image_end = image_start + image["text_length"]
            if image_start < end_offset and image_end > start_offset:
                image_refs.append(image["id"])
        return image_refs
