"""Remove deterministic parser noise while preserving meaningful content.

The implementation targets common PDF-to-Markdown artifacts: repeated short
headers and footers, table-of-contents leaders, page-number watermarks,
separator-only lines, excessive whitespace, and physical line wrapping.
Image placeholders are always preserved for later caption and response stages.
"""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any

from src.core.errors import IngestionError
from src.core.types import Chunk
from src.ingestion.chunk.chunk_id import build_chunk_id
from src.libs.transform.base_transform import BaseTransform

_PAGE_MARKER = re.compile(
    r"^(?:page\s+\d+\s*(?:[/\\|-]\s*\d+)?|\d+\s*[/\\]\s*\d+)$",
    flags=re.IGNORECASE,
)
_TOC_ENTRY = re.compile(r"^.+?\.{3,}\s*\d+\s*$", flags=re.IGNORECASE)
_SYMBOL_SEPARATOR = re.compile(r"^[^\w\u3400-\u9fff\[\]]{4,}$")
_IMAGE_PLACEHOLDER = re.compile(r"^\[\[image:[^\]]+\]\]$")
_SENTENCE_END = re.compile(r"[.!?。！？:：;；]$")


class DenoiseTransform(BaseTransform):
    """Apply repeatable rule-based cleanup to parsed chunk text."""

    def transform(
        self,
        chunks: list[Chunk],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Clean every chunk and record deterministic removal statistics.

        Args:
            chunks: Ordered chunks that may contain parser artifacts.
            context: Optional runtime context reserved for trace orchestration.

        Returns:
            New chunks with cleaned text, regenerated IDs when content changes,
            and idempotency metadata.

        Raises:
            IngestionError: If cleanup removes all searchable content.
        """

        del context
        return [self._clean_chunk(chunk) for chunk in chunks]

    def _clean_chunk(self, chunk: Chunk) -> Chunk:
        """Clean one chunk unless it already carries matching output metadata.

        Args:
            chunk: Parsed source chunk that may contain deterministic noise.

        Returns:
            A cleaned chunk with regenerated ID and removal statistics, or a
            deep copy when the recorded output hash matches current text.

        Raises:
            IngestionError: If cleanup removes all searchable content.
        """

        cleaned_text, _removed_line_count = _clean_text(chunk.text)
        if not cleaned_text.strip():
            raise IngestionError(
                "Denoise transform removed all chunk content",
                context={"operation": "denoise", "chunk_id": chunk.id},
            )

        metadata = deepcopy(chunk.metadata)
        source_path = str(
            metadata.get("source_path")
            or metadata.get("document_id")
            or chunk.id
        )
        return chunk.model_copy(
            update={
                "id": build_chunk_id(
                    source_path=source_path,
                    section_path=metadata.get("section_path"),
                    text=cleaned_text,
                ),
                "text": cleaned_text,
                "metadata": metadata,
            },
            deep=True,
        )


def _clean_text(text: str) -> tuple[str, int]:
    """Normalize one parsed text block and return removal statistics.

    Args:
        text: Parsed chunk text that may contain physical line wrapping,
            repeated boundary labels, page markers, or separators.

    Returns:
        A tuple containing cleaned text and the number of non-blank noise lines
        removed. Whitespace compaction is not counted as a removed line.
    """

    normalized_lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    content_lines = [line for line in normalized_lines if line]
    counts = Counter(content_lines)
    leading_boundary = content_lines[0] if content_lines else None
    trailing_boundary = content_lines[-1] if content_lines else None
    repeated_noise = {
        line
        for line, count in counts.items()
        if count >= 2
        and len(line) <= 100
        and not _IMAGE_PLACEHOLDER.fullmatch(line)
        and line == leading_boundary
        and line == trailing_boundary
    }

    filtered: list[str] = []
    removed = 0
    for line in normalized_lines:
        if not line:
            if filtered and filtered[-1]:
                filtered.append("")
            continue
        if (
            line in repeated_noise
            or _PAGE_MARKER.fullmatch(line)
            or _TOC_ENTRY.fullmatch(line)
            or _SYMBOL_SEPARATOR.fullmatch(line)
        ):
            removed += 1
            continue
        filtered.append(line)

    paragraphs: list[str] = []
    current: list[str] = []
    for line in filtered:
        if not line:
            if current:
                paragraphs.append(_join_wrapped_lines(current))
                current = []
            continue
        if _IMAGE_PLACEHOLDER.fullmatch(line):
            if current:
                paragraphs.append(_join_wrapped_lines(current))
                current = []
            paragraphs.append(line)
            continue
        current.append(line)
    if current:
        paragraphs.append(_join_wrapped_lines(current))

    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph), removed


def _join_wrapped_lines(lines: list[str]) -> str:
    """Join physical parser lines into one readable paragraph.

    Args:
        lines: Ordered non-blank lines belonging to one source paragraph.

    Returns:
        A readable paragraph. A sentence-ending line retains a newline before
        the next sentence; an incomplete physical line is joined with a space.
    """

    if len(lines) == 1:
        return lines[0]
    output = lines[0]
    for line in lines[1:]:
        separator = "\n" if _SENTENCE_END.search(output) else " "
        output = f"{output}{separator}{line}"
    return output
