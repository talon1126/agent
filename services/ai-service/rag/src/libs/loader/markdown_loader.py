"""Load Markdown sources into canonical RAG ``Document`` objects.

The loader normalizes source text, extracts an ordered ATX heading hierarchy,
replaces existing local Markdown images with stable RAG placeholders, and
records offset-addressable ``metadata.images`` entries. This is the source
adapter boundary only: it does not split documents, copy image files, generate
captions, or write repository records.
"""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.core.errors import IngestionError
from src.core.types import Document
from src.ingestion.pdf_to_markdown import canonicalize_markdown
from src.libs.loader.base_loader import BaseLoader

_ATX_HEADING = re.compile(
    r"^[ \t]{0,3}(?P<marks>#{1,6})[ \t]+(?P<title>.+?)\s*$",
    flags=re.MULTILINE,
)
_FENCED_CODE = re.compile(r"^[ \t]{0,3}(?P<marker>`{3,}|~{3,})")
_MARKDOWN_IMAGE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<target><[^>]+>|[^)\s]+)(?:\s+[\"'][^)]*[\"'])?\)"
)


def _stable_document_id(path: Path, source_hash: str) -> str:
    """Build a stable document ID from canonical source identity and bytes.

    Args:
        path: Resolved source file path.
        source_hash: SHA256 digest of the original source bytes.

    Returns:
        A SHA256-based document identifier stable across repeated loads of the
        same source version.
    """

    digest = sha256(f"{path.as_posix()}|{source_hash}".encode()).hexdigest()
    return f"doc-{digest}"


def extract_heading_hierarchy(content: str) -> list[dict[str, Any]]:
    """Extract ordered heading paths from canonical ATX Markdown.

    Args:
        content: Canonical Markdown document.

    Returns:
        Heading entries containing level, title, and the active hierarchical
        path at that location.
    """

    hierarchy: list[dict[str, Any]] = []
    active_path: list[str] = []
    fenced_ranges = _fenced_code_ranges(content)
    for match in _ATX_HEADING.finditer(content):
        if _offset_in_ranges(match.start(), fenced_ranges):
            continue
        level = len(match.group("marks"))
        title = re.sub(r"[ \t]+#+[ \t]*$", "", match.group("title")).strip()
        if not title:
            continue
        active_path = active_path[: level - 1]
        active_path.append(title)
        hierarchy.append(
            {
                "level": level,
                "title": title,
                "path": list(active_path),
            }
        )
    return hierarchy


def _fenced_code_ranges(content: str) -> list[tuple[int, int]]:
    """Locate Markdown fenced-code regions using source text offsets.

    Args:
        content: Canonical Markdown text.

    Returns:
        Start-inclusive, end-exclusive ranges covering complete backtick or
        tilde fences. An unclosed fence extends to the end of the document.
    """

    ranges: list[tuple[int, int]] = []
    active_marker: str | None = None
    active_start = 0
    offset = 0
    for line in content.splitlines(keepends=True):
        fence_match = _FENCED_CODE.match(line)
        if active_marker is None:
            if fence_match:
                active_marker = fence_match.group("marker")
                active_start = offset
        elif fence_match:
            candidate = fence_match.group("marker")
            remainder = line[fence_match.end() :].strip()
            if (
                candidate[0] == active_marker[0]
                and len(candidate) >= len(active_marker)
                and not remainder
            ):
                ranges.append((active_start, offset + len(line)))
                active_marker = None
        offset += len(line)
    if active_marker is not None:
        ranges.append((active_start, len(content)))
    return ranges


def _offset_in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    """Return whether a source offset belongs to a fenced-code region."""

    return any(start <= offset < end for start, end in ranges)


def _is_local_image_target(target: str) -> bool:
    """Return whether a Markdown image target can be resolved on disk."""

    lowered = target.lower()
    return not (
        lowered.startswith(("http://", "https://", "data:", "file:"))
        or target.startswith("#")
    )


def extract_markdown_images(
    content: str,
    *,
    source_path: Path,
) -> tuple[str, list[dict[str, Any]]]:
    """Replace existing local Markdown images with stable RAG placeholders.

    Args:
        content: Canonical Markdown text.
        source_path: Resolved Markdown source used to resolve relative images.

    Returns:
        Transformed text and validated image metadata. Missing or remote image
        targets remain unchanged and do not create invalid metadata entries.
    """

    output: list[str] = []
    images: list[dict[str, Any]] = []
    source_cursor = 0
    output_length = 0
    fenced_ranges = _fenced_code_ranges(content)
    for image_index, match in enumerate(_MARKDOWN_IMAGE.finditer(content)):
        prefix = content[source_cursor : match.start()]
        output.append(prefix)
        output_length += len(prefix)

        if _offset_in_ranges(match.start(), fenced_ranges):
            original = match.group(0)
            output.append(original)
            output_length += len(original)
            source_cursor = match.end()
            continue

        raw_target = match.group("target").strip("<>")
        if not _is_local_image_target(raw_target):
            original = match.group(0)
            output.append(original)
            output_length += len(original)
            source_cursor = match.end()
            continue

        source_directory = source_path.parent.resolve()
        candidate = (source_directory / raw_target).expanduser().resolve()
        try:
            candidate.relative_to(source_directory)
        except ValueError:
            original = match.group(0)
            output.append(original)
            output_length += len(original)
            source_cursor = match.end()
            continue
        if not candidate.is_file():
            original = match.group(0)
            output.append(original)
            output_length += len(original)
            source_cursor = match.end()
            continue

        image_hash = sha256(candidate.read_bytes()).hexdigest()
        identity = f"{source_path.as_posix()}|markdown|{image_index}|{image_hash}"
        image_id = f"image-{sha256(identity.encode()).hexdigest()}"
        placeholder = f"[[image:{image_id}]]"
        output.append(placeholder)
        images.append(
            {
                "id": image_id,
                "path": str(candidate),
                "page": None,
                "text_offset": output_length,
                "text_length": len(placeholder),
                "position": {
                    "source_type": "markdown",
                    "line": content.count("\n", 0, match.start()) + 1,
                    "alt_text": match.group("alt"),
                },
            }
        )
        output_length += len(placeholder)
        source_cursor = match.end()

    output.append(content[source_cursor:])
    return "".join(output), images


class MarkdownLoader(BaseLoader):
    """Convert one Markdown file into the shared canonical ``Document``."""

    def load(self, source: str | Path) -> Document:
        """Read a Markdown source file as UTF-8 and return a ``Document``.

        Args:
            source: Filesystem path ending in ``.md`` or another Markdown
                extension selected by ``LoaderFactory``.

        Returns:
            A validated ``Document`` containing canonical Markdown, stable
            source identity, heading hierarchy, and optional image metadata.

        Raises:
            IngestionError: If the file cannot be read or the loaded content is
                invalid for ``Document`` construction.
        """

        path = Path(source).expanduser().resolve()
        try:
            source_bytes = path.read_bytes()
            source_hash = sha256(source_bytes).hexdigest()
            canonical = canonicalize_markdown(source_bytes.decode("utf-8-sig"))
            content, images = extract_markdown_images(
                canonical,
                source_path=path,
            )
            headings = extract_heading_hierarchy(content)
            metadata = {
                "source_path": str(path),
                "source_type": "markdown",
                "source_hash": source_hash,
            }
            if headings:
                metadata["title"] = headings[0]["title"]
                metadata["headings"] = headings
            if images:
                metadata["images"] = images
            return Document(
                id=_stable_document_id(path, source_hash),
                text=content,
                metadata=metadata,
            )
        except IngestionError:
            raise
        except Exception as error:
            raise IngestionError(
                "Unable to load Markdown document",
                context={"operation": "markdown_load", "source": str(path)},
                cause=error,
            ) from error
