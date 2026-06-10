"""Load PDF files into canonical text and multimodal ``Document`` metadata.

``PdfLoader`` consumes the parser-neutral result produced by
``MarkItDownConverter``. It generates stable document and image identifiers,
persists extracted image bytes, injects ``[[image:image_id]]`` markers into the
final text, and constructs offset-addressable image metadata. It does not index
saved images in PostgreSQL or generate captions; those actions belong to later
storage and ImageCaptioner stages.
"""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.core.errors import IngestionError
from src.core.types import Document
from src.ingestion.pdf_to_markdown import (
    ExtractedImage,
    MarkItDownConverter,
    PdfConversionResult,
)
from src.libs.loader.base_loader import BaseLoader
from src.libs.loader.markdown_loader import extract_heading_hierarchy

_PAGE_MARKER_PATTERN = re.compile(r"<!--\s*page:\s*(\d+)\s*-->")


def _stable_pdf_document_id(path: Path, source_hash: str) -> str:
    """Generate a stable document ID from PDF path and original byte hash."""

    identity = f"{path.as_posix()}|{source_hash}"
    return f"doc-{sha256(identity.encode()).hexdigest()}"


def _stable_pdf_image_id(
    path: Path,
    *,
    image: ExtractedImage,
    image_index: int,
) -> str:
    """Generate one stable image ID from source position and image content."""

    image_hash = sha256(image.content).hexdigest()
    identity = f"{path.as_posix()}|{image.page}|{image_index}|{image_hash}"
    return f"image-{sha256(identity.encode()).hexdigest()}"


class PdfLoader(BaseLoader):
    """Convert one PDF source into the shared canonical ``Document``."""

    def __init__(
        self,
        *,
        converter: MarkItDownConverter | Any | None = None,
        image_output_dir: str | Path = Path("data/images"),
    ) -> None:
        """Configure conversion and local image persistence boundaries.

        Args:
            converter: Parser adapter returning ``PdfConversionResult``. The
                default lazily loads MarkItDown and PyMuPDF.
            image_output_dir: Root directory used to store extracted image
                bytes under a stable document-ID subdirectory.
        """

        self._converter = converter or MarkItDownConverter()
        self._image_output_dir = Path(image_output_dir).expanduser().resolve()

    def load(self, source: str | Path) -> Document:
        """Convert a PDF source through MarkItDown and return a ``Document``.

        Args:
            source: Filesystem path selected as a PDF by ``LoaderFactory``.

        Returns:
            A validated ``Document`` containing canonical Markdown, heading
            hierarchy, source hash, and optional persisted image metadata.

        Raises:
            IngestionError: If the source is invalid, conversion fails, image
                persistence fails, or the result violates ``Document``.

        Side Effects:
            Writes extracted image bytes below
            ``image_output_dir/{document_id}/`` only when images exist.
        """

        path = Path(source).expanduser().resolve()
        try:
            if path.suffix.lower() != ".pdf":
                raise ValueError("PdfLoader only accepts .pdf sources")
            source_bytes = path.read_bytes()
            source_hash = sha256(source_bytes).hexdigest()
            document_id = _stable_pdf_document_id(path, source_hash)
            conversion = self._converter.convert(path)
            if not isinstance(conversion, PdfConversionResult):
                raise TypeError("PDF converter must return PdfConversionResult")
            content, images = self._persist_and_inject_images(
                path,
                document_id=document_id,
                conversion=conversion,
            )
            headings = extract_heading_hierarchy(content)
            metadata: dict[str, Any] = {
                "source_path": str(path),
                "source_type": "pdf",
                "source_hash": source_hash,
            }
            if headings:
                metadata["title"] = headings[0]["title"]
                metadata["headings"] = headings
            if images:
                metadata["images"] = images
            return Document(
                id=document_id,
                text=content,
                metadata=metadata,
            )
        except IngestionError as error:
            if error.context.get("operation") == "pdf_load":
                raise
            raise IngestionError(
                "Unable to load PDF document",
                context={"operation": "pdf_load", "source": str(path)},
                cause=error,
            ) from error
        except Exception as error:
            raise IngestionError(
                "Unable to load PDF document",
                context={"operation": "pdf_load", "source": str(path)},
                cause=error,
            ) from error

    def _persist_and_inject_images(
        self,
        source_path: Path,
        *,
        document_id: str,
        conversion: PdfConversionResult,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Persist extracted images and insert offset-addressable placeholders.

        Args:
            source_path: Canonical PDF path used in stable image IDs.
            document_id: Stable parent document ID and image directory name.
            conversion: Canonical Markdown and extracted image payloads.

        Returns:
            Final document text and ``metadata.images`` entries. Text-only
            conversions return the original Markdown and an empty list without
            creating image directories.

        Raises:
            OSError: If an extracted image cannot be written atomically.
        """

        if not conversion.images:
            return conversion.markdown, []

        image_directory = self._image_output_dir / document_id
        image_directory.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        written_targets: list[Path] = []
        active_temporary: Path | None = None
        try:
            for image_index, image in enumerate(_sort_images_by_source_position(conversion.images)):
                image_id = _stable_pdf_image_id(
                    source_path,
                    image=image,
                    image_index=image_index,
                )
                target = image_directory / f"{image_id}{image.suffix}"
                active_temporary = target.with_name(f".{target.name}.tmp")
                active_temporary.write_bytes(image.content)
                active_temporary.replace(target)
                active_temporary = None
                written_targets.append(target)

                placeholder = f"[[image:{image_id}]]"
                entries.append(
                    {
                        "id": image_id,
                        "path": str(target),
                        "page": image.page,
                        "placeholder": placeholder,
                        "text_length": len(placeholder),
                        "position": dict(image.position),
                    }
                )
        except OSError:
            if active_temporary is not None:
                active_temporary.unlink(missing_ok=True)
            for target in written_targets:
                target.unlink(missing_ok=True)
            try:
                image_directory.rmdir()
            except OSError:
                pass
            raise
        content, metadata = _insert_placeholders_by_page(
            conversion.markdown,
            entries,
        )
        return content, metadata


def _sort_images_by_source_position(
    images: tuple[ExtractedImage, ...],
) -> tuple[ExtractedImage, ...]:
    """Order extracted images by page, vertical position, and source sequence.

    Args:
        images: Raw image records returned by the PDF parser boundary.

    Returns:
        Images sorted in the same order readers normally encounter them in the
        source document.
    """

    return tuple(
        sorted(
            images,
            key=lambda image: (
                image.page,
                _numeric_position(image.position.get("y")),
                _numeric_position(image.position.get("x")),
                _numeric_position(image.position.get("sequence")),
            ),
        )
    )


def _insert_placeholders_by_page(
    markdown: str,
    entries: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Insert image placeholders into page-local text regions.

    Args:
        markdown: Canonical Markdown returned by the PDF text converter.
        entries: Persisted image metadata entries containing page numbers and
            placeholders but not final text offsets.

    Returns:
        Updated document text and metadata entries whose ``text_offset`` values
        point at the inserted placeholders.

    Notes:
        MarkItDown output does not expose exact character offsets for PDF
        image rectangles. When page markers such as ``<!-- page: 2 -->`` are
        available, placeholders are inserted at the end of their page region
        before the next marker. Without markers the function degrades to a
        sorted append, which is still deterministic and preserves source order.
    """

    if not entries:
        return markdown, []

    page_ranges = _page_ranges(markdown)
    insertions: list[tuple[int, dict[str, Any]]] = []
    anchor_search_start = 0
    for entry in entries:
        anchored = _anchor_insertion_offset(
            markdown,
            position=entry["position"],
            search_start=anchor_search_start,
        )
        if anchored is not None:
            offset, anchor_search_start = anchored
        else:
            offset = _insertion_offset(markdown, page_ranges, entry["page"])
        insertions.append((offset, entry))
    insertions.sort(
        key=lambda item: (
            item[0],
            int(item[1]["page"]),
            _numeric_position(item[1]["position"].get("y")),
            _numeric_position(item[1]["position"].get("x")),
        )
    )

    content_parts: list[str] = []
    metadata: list[dict[str, Any]] = []
    cursor = 0
    for offset, entry in insertions:
        content_parts.append(markdown[cursor:offset])
        prefix = _placeholder_prefix("".join(content_parts))
        placeholder_text = f"{prefix}{entry['placeholder']}\n"
        text_offset = len("".join(content_parts)) + len(prefix)
        content_parts.append(placeholder_text)
        cursor = offset
        metadata_entry = dict(entry)
        metadata_entry.pop("placeholder")
        metadata_entry["text_offset"] = text_offset
        metadata.append(metadata_entry)
    content_parts.append(markdown[cursor:])
    return "".join(content_parts), metadata


def _anchor_insertion_offset(
    markdown: str,
    *,
    position: dict[str, Any],
    search_start: int,
) -> tuple[int, int] | None:
    """Locate an image insertion point from a nearby physical text anchor.

    Args:
        markdown: Canonical MarkItDown text.
        position: Image geometry enriched by ``extract_images`` with optional
            ``anchor_text`` and ``anchor_relation`` fields.
        search_start: Lower bound for matching repeated anchors. Images are
            processed in page order, so advancing this cursor maps repeated
            section labels to their corresponding later pages.

    Returns:
        A pair containing the insertion offset and the next anchor search
        cursor, or ``None`` when the anchor is absent from Markdown.
    """

    anchor_text = str(position.get("anchor_text") or "").strip()
    relation = str(position.get("anchor_relation") or "")
    if not anchor_text or relation not in {"before", "after"}:
        return None
    tokens = anchor_text.split()
    if not tokens:
        return None
    pattern = re.compile(r"\s+".join(re.escape(token) for token in tokens))
    match = pattern.search(markdown, pos=max(search_start, 0))
    if match is None:
        return None
    offset = match.start() if relation == "before" else match.end()
    return _trim_trailing_blank_space(markdown, offset), match.end()


def _page_ranges(markdown: str) -> dict[int, tuple[int, int]]:
    """Return source page text ranges inferred from MarkItDown page markers."""

    markers = [
        (int(match.group(1)), match.start())
        for match in _PAGE_MARKER_PATTERN.finditer(markdown)
    ]
    if not markers:
        return {1: (0, len(markdown))}

    ranges: dict[int, tuple[int, int]] = {}
    first_marker_offset = markers[0][1]
    if first_marker_offset > 0:
        ranges[1] = (0, first_marker_offset)
    for index, (page, start) in enumerate(markers):
        end = markers[index + 1][1] if index + 1 < len(markers) else len(markdown)
        ranges[page] = (start, end)
    return ranges


def _insertion_offset(
    markdown: str,
    page_ranges: dict[int, tuple[int, int]],
    page: int,
) -> int:
    """Choose a stable placeholder insertion offset for one image page."""

    if page in page_ranges:
        _start, end = page_ranges[page]
        return _trim_trailing_blank_space(markdown, end)
    return _trim_trailing_blank_space(markdown, len(markdown))


def _trim_trailing_blank_space(text: str, offset: int) -> int:
    """Move an insertion offset before trailing whitespace in a page region."""

    while offset > 0 and text[offset - 1].isspace():
        offset -= 1
    return offset


def _placeholder_prefix(prefix_content: str) -> str:
    """Return spacing needed before an inserted placeholder."""

    if not prefix_content:
        return ""
    if prefix_content.endswith("\n\n"):
        return ""
    if prefix_content.endswith("\n"):
        return "\n"
    return "\n\n"


def _numeric_position(value: Any) -> float:
    """Convert optional geometry values into sortable numbers."""

    if isinstance(value, int | float):
        return float(value)
    return 0.0
