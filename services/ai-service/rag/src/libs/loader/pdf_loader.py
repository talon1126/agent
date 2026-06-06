"""Load PDF files into canonical text and multimodal ``Document`` metadata.

``PdfLoader`` consumes the parser-neutral result produced by
``MarkItDownConverter``. It generates stable document and image identifiers,
persists extracted image bytes, injects ``[[image:image_id]]`` markers into the
final text, and constructs offset-addressable image metadata. It does not index
saved images in PostgreSQL or generate captions; those actions belong to later
storage and ImageCaptioner stages.
"""

from __future__ import annotations

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
        """Persist extracted images and append offset-addressable placeholders.

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
        content = conversion.markdown
        metadata: list[dict[str, Any]] = []
        written_targets: list[Path] = []
        active_temporary: Path | None = None
        try:
            for image_index, image in enumerate(conversion.images):
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

                separator = "\n" if content.endswith("\n") else "\n\n"
                placeholder = f"[[image:{image_id}]]"
                text_offset = len(content) + len(separator)
                content = f"{content}{separator}{placeholder}\n"
                metadata.append(
                    {
                        "id": image_id,
                        "path": str(target),
                        "page": image.page,
                        "text_offset": text_offset,
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
        return content, metadata
