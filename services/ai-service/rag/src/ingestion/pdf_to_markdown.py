"""Convert PDF sources into canonical Markdown and extracted image records.

This module isolates the two parser technologies used by the PDF ingestion
boundary. MarkItDown converts document text into Markdown, while PyMuPDF
extracts original embedded image bytes and source geometry. Keeping both
operations behind small injectable callables lets ``PdfLoader`` remain focused
on constructing the shared ``Document`` contract and lets unit tests run
without importing native PDF packages.

The module does not generate document or image IDs, persist image files, or
construct ``metadata.images`` offsets. Those business responsibilities belong
to ``PdfLoader`` because they depend on the final canonical document text.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from src.core.errors import IngestionError

_TRAILING_WHITESPACE = re.compile(r"[ \t]+$", flags=re.MULTILINE)
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")
_SAFE_IMAGE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]+$")


def canonicalize_markdown(text: str) -> str:
    """Normalize parser output into stable Markdown used by downstream stages.

    Args:
        text: Markdown-like text returned by MarkItDown or read from a source
            file.

    Returns:
        UTF-8-compatible text with a removed BOM, LF newlines, no trailing
        horizontal whitespace, at most one blank line between blocks, and one
        final newline.

    Raises:
        ValueError: If normalization produces an empty document.
    """

    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    normalized = _TRAILING_WHITESPACE.sub("", normalized)
    normalized = _EXCESS_BLANK_LINES.sub("\n\n", normalized)
    normalized = normalized.strip()
    if not normalized:
        raise ValueError("Canonical Markdown must not be blank")
    return f"{normalized}\n"


@dataclass(frozen=True, slots=True)
class ExtractedImage:
    """Carry one PDF image from the parser boundary to ``PdfLoader``.

    Attributes:
        content: Original encoded image bytes returned by PyMuPDF.
        suffix: File extension including the leading dot.
        page: One-based source page number for operator-facing citations.
        position: Physical source geometry and parser-specific details.
    """

    content: bytes
    suffix: str
    page: int
    position: Mapping[str, Any]

    def __post_init__(self) -> None:
        """Validate parser output and freeze mutable geometry metadata."""

        if not self.content:
            raise ValueError("Extracted image content must not be empty")
        if not _SAFE_IMAGE_SUFFIX.fullmatch(self.suffix):
            raise ValueError("Extracted image suffix must be a portable extension")
        if self.page < 1:
            raise ValueError("Extracted image page must use one-based numbering")
        if not self.position:
            raise ValueError("Extracted image position must not be empty")
        object.__setattr__(self, "suffix", self.suffix.lower())
        object.__setattr__(self, "position", MappingProxyType(dict(self.position)))


@dataclass(frozen=True, slots=True)
class PdfConversionResult:
    """Return canonical PDF text and any extracted image payloads together."""

    markdown: str
    images: tuple[ExtractedImage, ...] = ()

    def __post_init__(self) -> None:
        """Normalize sequence input so conversion results remain immutable."""

        object.__setattr__(self, "images", tuple(self.images))


def _rectangle_position(
    rectangle: Any | None,
    *,
    pixel_width: int | None,
    pixel_height: int | None,
    sequence: int,
) -> dict[str, Any]:
    """Convert a PyMuPDF rectangle into JSON-compatible physical metadata.

    Args:
        rectangle: PyMuPDF rectangle for one image occurrence, when available.
        pixel_width: Encoded image width reported by the PDF parser.
        pixel_height: Encoded image height reported by the PDF parser.
        sequence: Zero-based image order within the source page.

    Returns:
        Position metadata containing physical coordinates when available and
        encoded pixel dimensions for diagnostics.
    """

    position: dict[str, Any] = {
        "pixel_width": pixel_width,
        "pixel_height": pixel_height,
        "sequence": sequence,
    }
    if rectangle is None:
        position.update(
            {
                "x": 0.0,
                "y": 0.0,
                "width": float(pixel_width or 0),
                "height": float(pixel_height or 0),
            }
        )
        return position

    x0 = float(rectangle.x0)
    y0 = float(rectangle.y0)
    x1 = float(rectangle.x1)
    y1 = float(rectangle.y1)
    position.update(
        {
            "x": x0,
            "y": y0,
            "width": x1 - x0,
            "height": y1 - y0,
            "bbox": [x0, y0, x1, y1],
        }
    )
    return position


def extract_images(source: str | Path) -> tuple[ExtractedImage, ...]:
    """Extract embedded PDF images through the optional PyMuPDF dependency.

    Args:
        source: Existing PDF file passed to PyMuPDF.

    Returns:
        Images in page and occurrence order. PDFs without embedded images
        return an empty tuple.

    Raises:
        IngestionError: If PyMuPDF is unavailable or PDF image parsing fails.
    """

    path = Path(source).expanduser().resolve()
    try:
        fitz = importlib.import_module("fitz")
    except ImportError as error:
        raise IngestionError(
            "PyMuPDF is required for PDF image extraction",
            context={"operation": "pdf_image_dependency", "source": str(path)},
            cause=error,
        ) from error

    images: list[ExtractedImage] = []
    try:
        with fitz.open(str(path)) as document:
            for page_number, page in enumerate(document, start=1):
                seen_xrefs: set[int] = set()
                for image_info in page.get_images(full=True):
                    xref = int(image_info[0])
                    if xref in seen_xrefs:
                        continue
                    sequence = len(seen_xrefs)
                    seen_xrefs.add(xref)
                    payload = document.extract_image(xref)
                    content = payload["image"]
                    suffix = f".{payload.get('ext') or 'bin'}"
                    rectangles: Sequence[Any] = page.get_image_rects(xref) or (None,)
                    for rectangle in rectangles:
                        images.append(
                            ExtractedImage(
                                content=content,
                                suffix=suffix,
                                page=page_number,
                                position=_rectangle_position(
                                    rectangle,
                                    pixel_width=payload.get("width"),
                                    pixel_height=payload.get("height"),
                                    sequence=sequence,
                                ),
                            )
                        )
    except IngestionError:
        raise
    except Exception as error:
        raise IngestionError(
            "Unable to extract images from PDF",
            context={"operation": "pdf_image_extract", "source": str(path)},
            cause=error,
        ) from error
    return tuple(images)


class MarkItDownConverter:
    """Combine injectable PDF text conversion and image extraction adapters."""

    def __init__(
        self,
        *,
        converter: Any | None = None,
        image_extractor: Callable[[Path], Sequence[ExtractedImage]] = extract_images,
    ) -> None:
        """Configure parser dependencies without importing them eagerly.

        Args:
            converter: Optional object exposing ``convert(path)``. When omitted,
                MarkItDown is imported only when ``convert()`` is first called.
            image_extractor: Callable returning image records for one PDF.

        Notes:
            Lazy MarkItDown loading keeps factory discovery and text-only unit
            tests usable before optional native dependencies are installed.
        """

        self._converter = converter
        self._image_extractor = image_extractor

    def convert(self, source: str | Path) -> PdfConversionResult:
        """Convert one PDF into canonical Markdown and extracted image records.

        Args:
            source: Existing PDF file.

        Returns:
            Immutable canonical text and ordered extracted images.

        Raises:
            IngestionError: If MarkItDown is unavailable, the source is not a
                readable PDF, text conversion fails, or image extraction fails.
        """

        path = Path(source).expanduser().resolve()
        if path.suffix.lower() != ".pdf":
            raise IngestionError(
                "MarkItDownConverter only accepts PDF sources",
                context={"operation": "pdf_convert", "source": str(path)},
            )
        if not path.is_file():
            raise IngestionError(
                "PDF source does not exist or is not a file",
                context={"operation": "pdf_convert", "source": str(path)},
            )

        converter = self._converter or self._load_default_converter(path)
        try:
            converted = converter.convert(str(path))
            text = getattr(converted, "text_content", None)
            if text is None:
                text = str(converted)
            markdown = canonicalize_markdown(text)
            images = tuple(self._image_extractor(path))
        except IngestionError:
            raise
        except Exception as error:
            raise IngestionError(
                "Unable to convert PDF to canonical Markdown",
                context={"operation": "pdf_convert", "source": str(path)},
                cause=error,
            ) from error
        return PdfConversionResult(markdown=markdown, images=images)

    @staticmethod
    def _load_default_converter(path: Path) -> Any:
        """Import and construct MarkItDown for a real PDF conversion.

        Args:
            path: Source path included in trace-safe dependency error context.

        Returns:
            A configured MarkItDown converter instance.

        Raises:
            IngestionError: If the declared MarkItDown dependency is missing.
        """

        try:
            module = importlib.import_module("markitdown")
            return module.MarkItDown()
        except (ImportError, AttributeError) as error:
            raise IngestionError(
                "MarkItDown is required for PDF text conversion",
                context={"operation": "pdf_convert_dependency", "source": str(path)},
                cause=error,
            ) from error
