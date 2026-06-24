"""Split Markdown sources around semantic sections while staying text-only.

The splitter keeps the ``BaseSplitter`` contract: it accepts one Markdown string
and returns ordered plain strings. It does not create ``Document`` or ``Chunk``
objects and does not attach business metadata. The goal is to produce better
Markdown-sized retrieval units before ``DocumentChunker`` adds source ranges,
section metadata, image references, and stable chunk IDs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.core.errors import IngestionError
from src.libs.splitter.base_splitter import BaseSplitter
from src.libs.splitter.recursive_character_splitter import RecursiveCharacterSplitter

_ATX_HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


@dataclass(frozen=True, slots=True)
class MarkdownSection:
    """Represent one heading-bounded Markdown section.

    Attributes:
        text: Source text belonging to this section.
        path: Active heading path from document title to the section heading.
        start_offset: Start-inclusive offset in the original Markdown text.
        end_offset: End-exclusive offset in the original Markdown text.
        level: Heading level used as the section boundary.
    """

    text: str
    path: tuple[str, ...]
    start_offset: int
    end_offset: int
    level: int


class MarkdownSectionSplitter(BaseSplitter):
    """Split Markdown by heading sections, then by tables and block boundaries.

    Args:
        chunk_size: Maximum target length for returned text segments.
        chunk_overlap: Overlap used only for recursive fallback inside very long
            non-table blocks.
        min_section_chars: Sections shorter than this are merged with an
            adjacent sibling where possible.
        section_level: Preferred section boundary level. The default ``3``
            means ``###`` headings become the primary retrieval unit.
        fallback_options: Extra options forwarded to the recursive fallback.

    Notes:
        Returned chunks omit Markdown heading lines. Section context is carried
        later by ``DocumentChunker`` through metadata and ``source_ref`` rather
        than duplicated in chunk text.
    """

    def __init__(
        self,
        *,
        chunk_size: int = 900,
        chunk_overlap: int = 150,
        min_section_chars: int = 120,
        section_level: int = 3,
        **fallback_options: Any,
    ) -> None:
        """Create a Markdown-aware text splitter from configuration values.

        Raises:
            IngestionError: If sizing options cannot produce valid chunks.
        """

        self.chunk_size = int(chunk_size)
        self.chunk_overlap = int(chunk_overlap)
        self.min_section_chars = int(min_section_chars)
        self.section_level = int(section_level)
        if self.chunk_size <= 0:
            raise IngestionError(
                "Markdown section chunk_size must be positive",
                context={"operation": "markdown_section_splitter_init"},
            )
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise IngestionError(
                "Markdown section chunk_overlap must be smaller than chunk_size",
                context={
                    "operation": "markdown_section_splitter_init",
                    "chunk_size": self.chunk_size,
                    "chunk_overlap": self.chunk_overlap,
                },
            )
        if self.min_section_chars < 0:
            raise IngestionError(
                "Markdown section min_section_chars must not be negative",
                context={"operation": "markdown_section_splitter_init"},
            )
        self._fallback = RecursiveCharacterSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            **fallback_options,
        )

    def split(self, text: str) -> list[str]:
        """Return ordered Markdown chunks with heading and table awareness.

        Args:
            text: Canonical Markdown source text produced by a loader.

        Returns:
            Ordered non-blank Markdown fragments. Fragments are capped by
            ``chunk_size`` unless a single table row or fallback segment cannot
            be split further.
        """

        sections = self.merge_short_sections(self.build_sections(text))
        if not sections:
            return self._fallback.split(text)

        chunks: list[str] = []
        for section in sections:
            section_text = _strip_markdown_headings(section.text)
            if not section_text:
                continue
            if len(section_text) <= self.chunk_size:
                chunks.append(section_text)
                continue
            chunks.extend(self.split_long_section(section))
        return [chunk for chunk in chunks if chunk.strip()]

    def build_sections(self, text: str) -> list[MarkdownSection]:
        """Build heading-bounded sections from Markdown text.

        The preferred boundary is ``section_level``. If a document does not
        contain that level, the method falls back to the deepest available
        heading level up to ``section_level`` so simple Markdown documents still
        receive structure-aware chunks.
        """

        headings = _collect_headings(text)
        if not headings:
            return []

        available_levels = {heading["level"] for heading in headings}
        boundary_level = self.section_level
        if boundary_level not in available_levels:
            shallower = [level for level in available_levels if level < boundary_level]
            if not shallower:
                return []
            boundary_level = max(shallower)

        boundary_indexes = [
            index
            for index, heading in enumerate(headings)
            if heading["level"] == boundary_level
        ]
        if not boundary_indexes:
            return []

        sections: list[MarkdownSection] = []
        first_start = headings[boundary_indexes[0]]["start"]
        preamble = text[:first_start].strip()
        if preamble and _has_non_heading_content(preamble):
            sections.append(
                MarkdownSection(
                    text=preamble,
                    path=tuple(_active_path_before(headings, first_start)),
                    start_offset=0,
                    end_offset=first_start,
                    level=0,
                )
            )

        for position, heading_index in enumerate(boundary_indexes):
            heading = headings[heading_index]
            next_start = len(text)
            for later in headings[heading_index + 1 :]:
                if later["level"] <= boundary_level:
                    next_start = later["start"]
                    break
            section_text = text[heading["start"] : next_start].strip()
            if not section_text:
                continue
            sections.append(
                MarkdownSection(
                    text=section_text,
                    path=tuple(heading["path"]),
                    start_offset=heading["start"],
                    end_offset=next_start,
                    level=heading["level"],
                )
            )
        return sections

    def merge_short_sections(self, sections: list[MarkdownSection]) -> list[MarkdownSection]:
        """Merge very short sibling sections into adjacent semantic text.

        Args:
            sections: Ordered sections returned by ``build_sections``.

        Returns:
            Ordered sections where title-only or tiny sibling sections have
            been merged with the following sibling, or with the previous section
            when no following sibling is available.
        """

        merged: list[MarkdownSection] = []
        index = 0
        while index < len(sections):
            current = sections[index]
            if len(current.text.strip()) >= self.min_section_chars:
                merged.append(current)
                index += 1
                continue

            if index + 1 < len(sections) and _same_parent(current, sections[index + 1]):
                following = sections[index + 1]
                merged.append(
                    MarkdownSection(
                        text=f"{current.text.rstrip()}\n\n{following.text.lstrip()}",
                        path=current.path or following.path,
                        start_offset=current.start_offset,
                        end_offset=following.end_offset,
                        level=current.level or following.level,
                    )
                )
                index += 2
                continue

            if merged:
                previous = merged.pop()
                merged.append(
                    MarkdownSection(
                        text=f"{previous.text.rstrip()}\n\n{current.text.lstrip()}",
                        path=previous.path,
                        start_offset=previous.start_offset,
                        end_offset=current.end_offset,
                        level=previous.level,
                    )
                )
            else:
                merged.append(current)
            index += 1
        return merged

    def split_long_section(self, section: MarkdownSection) -> list[str]:
        """Split one oversized section while preserving local text context.

        Table blocks are split by rows and repeat their header rows in every
        output fragment. Normal prose blocks are packed by paragraph and use the
        recursive fallback only when a single block is still too large.
        """

        context = ""
        body = _strip_markdown_headings(section.text)
        blocks = _merge_advice_blocks(_markdown_blocks(body))
        chunks: list[str] = []
        current = context

        index = 0
        while index < len(blocks):
            block = blocks[index]
            if _is_table_block(block):
                first_context = current if current.strip() != context.strip() else None
                tail_block = (
                    blocks[index + 1]
                    if index + 1 < len(blocks) and _is_advice_block(blocks[index + 1])
                    else None
                )
                table_chunks = self.split_markdown_table(
                    block,
                    context=context,
                    first_context=first_context,
                    tail_block=tail_block,
                )
                chunks.extend(table_chunks)
                current = context
                index += 2 if tail_block is not None else 1
                continue

            if not current.strip() and chunks and _is_advice_block(block):
                merged_tail = _join_context(chunks[-1], block)
                if len(merged_tail) <= self.chunk_size:
                    chunks[-1] = merged_tail
                    index += 1
                    continue

            candidate = _join_context(current, block)
            if len(candidate) <= self.chunk_size:
                current = candidate
                index += 1
                continue

            if current.strip() != context.strip():
                chunks.append(current.strip())
                current = context

            block_with_context = _join_context(context, block)
            if len(block_with_context) <= self.chunk_size:
                current = block_with_context
                index += 1
                continue

            chunks.extend(self._split_long_text_block(block, context=context))
            index += 1

        if current.strip() != context.strip():
            chunks.append(current.strip())
        return [chunk for chunk in chunks if chunk.strip()]

    def split_markdown_table(
        self,
        table_block: str,
        *,
        context: str,
        first_context: str | None = None,
        tail_block: str | None = None,
    ) -> list[str]:
        """Split a Markdown table into row groups that repeat table headers.

        Args:
            table_block: Consecutive Markdown table lines.
            context: Heading context prepended to every table fragment after the
                first one.
            first_context: Optional richer context for the first table fragment,
                usually containing the section introduction immediately before
                the table.
            tail_block: Optional advice block that should be merged into the
                final table fragment when it fits.

        Returns:
            Table fragments with header row, separator row, and one or more data
            rows in each fragment.
        """

        lines = [line for line in table_block.splitlines() if line.strip()]
        if len(lines) < 3:
            return [_join_context(context, table_block)]

        header = lines[:2]
        rows = lines[2:]
        chunks: list[str] = []
        current_rows: list[str] = []

        active_context = first_context or context
        for row in rows:
            candidate_rows = [*current_rows, row]
            candidate = _join_context(
                active_context,
                "\n".join([*header, *candidate_rows]),
            )
            if len(candidate) <= self.chunk_size or not current_rows:
                current_rows = candidate_rows
                continue
            chunks.append(
                _join_context(active_context, "\n".join([*header, *current_rows]))
            )
            active_context = context
            current_rows = [row]

        if current_rows:
            chunks.extend(
                self._finish_table_rows(
                    header,
                    current_rows,
                    active_context=active_context,
                    base_context=context,
                    tail_block=tail_block,
                )
            )
        elif tail_block is not None:
            chunks.append(_join_context(context, tail_block))
        return chunks

    def _finish_table_rows(
        self,
        header: list[str],
        rows: list[str],
        *,
        active_context: str,
        base_context: str,
        tail_block: str | None,
    ) -> list[str]:
        """Finish the final table fragment and optionally merge advice text.

        ``active_context`` may contain the paragraph that introduced the table.
        If the final table rows must be split again to fit a following advice
        block, only the first emitted fragment keeps that rich context. Later
        fragments use ``base_context`` so the same prose is not embedded
        repeatedly.
        """

        if tail_block is None:
            return [_join_context(active_context, "\n".join([*header, *rows]))]

        final_rows = list(rows)
        deferred_rows: list[str] = []
        while final_rows:
            candidate = _join_context(
                active_context,
                "\n\n".join(
                    ["\n".join([*header, *final_rows]), tail_block.strip()]
                ),
            )
            if len(candidate) <= self.chunk_size:
                break
            deferred_rows.insert(0, final_rows.pop())

        chunks: list[str] = []
        if final_rows:
            chunks.append(_join_context(active_context, "\n".join([*header, *final_rows])))
        if deferred_rows:
            chunks.append(
                _join_context(
                    base_context,
                    "\n\n".join(
                        ["\n".join([*header, *deferred_rows]), tail_block.strip()]
                    ),
                )
            )
        elif chunks:
            chunks[-1] = _join_context(chunks[-1], tail_block)
        else:
            chunks.append(_join_context(base_context, tail_block))
        return chunks

    def _split_long_text_block(self, block: str, *, context: str) -> list[str]:
        """Split one oversized prose block with the recursive fallback."""

        available = self.chunk_size - len(context) - 2
        if available <= 20:
            return [block[: self.chunk_size]]
        fallback = RecursiveCharacterSplitter(
            chunk_size=available,
            chunk_overlap=min(self.chunk_overlap, max(available - 1, 0)),
        )
        return [_join_context(context, part) for part in fallback.split(block)]


def _collect_headings(text: str) -> list[dict[str, Any]]:
    """Collect ATX headings outside fenced-code blocks with active paths."""

    headings: list[dict[str, Any]] = []
    active_path: list[str] = []
    in_code_block = False
    opening_fence = ""
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not in_code_block and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_code_block = True
            opening_fence = stripped[:3]
        elif in_code_block and stripped.startswith(opening_fence):
            in_code_block = False
            opening_fence = ""
            offset += len(line)
            continue

        if not in_code_block:
            match = _ATX_HEADING.match(stripped)
            if match:
                level = len(match.group("marks"))
                title = re.sub(r"\s+#+\s*$", "", match.group("title")).strip()
                active_path = active_path[: level - 1]
                active_path.append(title)
                headings.append(
                    {
                        "level": level,
                        "title": title,
                        "path": list(active_path),
                        "start": offset,
                    }
                )
        offset += len(line)
    return headings


def _has_non_heading_content(text: str) -> bool:
    """Return whether a preamble contains retrievable body text."""

    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not _ATX_HEADING.match(stripped):
            return True
    return False


def _active_path_before(headings: list[dict[str, Any]], offset: int) -> list[str]:
    """Return the last heading path active before an arbitrary source offset."""

    active: list[str] = []
    for heading in headings:
        if heading["start"] >= offset:
            break
        active = list(heading["path"])
    return active


def _same_parent(left: MarkdownSection, right: MarkdownSection) -> bool:
    """Return whether two sections belong under the same parent headings."""

    return left.path[:-1] == right.path[:-1]


def _strip_markdown_headings(text: str) -> str:
    """Remove Markdown ATX heading lines while preserving code blocks.

    Markdown headings are structural metadata for retrieval, not content that
    should be embedded in every chunk. ``DocumentChunker`` later restores the
    active heading path through chunk metadata and ``source_ref``.
    """

    cleaned: list[str] = []
    in_code_block = False
    opening_fence = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not in_code_block and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_code_block = True
            opening_fence = stripped[:3]
            cleaned.append(line)
            continue
        if in_code_block:
            cleaned.append(line)
            if stripped.startswith(opening_fence):
                in_code_block = False
                opening_fence = ""
            continue
        if _ATX_HEADING.match(stripped):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _markdown_blocks(text: str) -> list[str]:
    """Split Markdown body into paragraph/list/table blocks."""

    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line)
            continue
        if current:
            blocks.append("\n".join(current).strip())
            current = []
    if current:
        blocks.append("\n".join(current).strip())
    return blocks


def _merge_advice_blocks(blocks: list[str]) -> list[str]:
    """Merge an advice label block with its following list/content block."""

    merged: list[str] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if _is_advice_block(block) and index + 1 < len(blocks):
            merged.append(f"{block.rstrip()}\n\n{blocks[index + 1].lstrip()}")
            index += 2
            continue
        merged.append(block)
        index += 1
    return merged


def _is_advice_block(block: str) -> bool:
    """Return whether a block is a short buying-advice style section tail."""

    stripped = block.strip().lower()
    return stripped.startswith(
        (
            "选购建议",
            "购买建议",
            "建议",
            "总结",
            "buying advice",
            "advice",
            "summary",
        )
    )


def _is_table_block(block: str) -> bool:
    """Return whether a Markdown block looks like a pipe table."""

    lines = [line.strip() for line in block.splitlines() if line.strip()]
    return len(lines) >= 2 and "|" in lines[0] and bool(_TABLE_SEPARATOR.match(lines[1]))


def _join_context(context: str, content: str) -> str:
    """Join optional local context and chunk content with stable spacing."""

    if not context.strip():
        return content.strip()
    if not content.strip():
        return context.strip()
    return f"{context.strip()}\n\n{content.strip()}"
