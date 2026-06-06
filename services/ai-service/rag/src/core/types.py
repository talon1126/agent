"""Define provider-independent domain objects shared across RAG pipelines.

The classes in this module form the stable data contract between loaders,
ingestion stages, repositories, retrieval routes, response builders, and trace
instrumentation. They validate structural invariants at construction time while
remaining JSON-compatible for PostgreSQL metadata, MCP responses, and JSON
Lines traces.

This module does not generate IDs, split documents, assign image references, or
rank retrieval results. Those behaviors belong to orchestration components that
construct these validated objects.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainModel(BaseModel):
    """Apply strict, assignment-safe validation to shared domain objects.

    Unknown fields are rejected so misspelled contract fields fail immediately.
    Assignment validation prevents long-lived pipeline objects from becoming
    invalid after construction.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ImageMetadata(DomainModel):
    """Describe one extracted image and its position in the source document.

    ``text_offset`` and ``text_length`` locate the image placeholder within the
    canonical ``Document.text``. ``position`` preserves loader-specific physical
    coordinates such as x/y/width/height or bbox without forcing one PDF parser's
    geometry schema on every loader.
    """

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=0)
    text_offset: int = Field(ge=0)
    text_length: int = Field(gt=0)
    position: dict[str, Any] = Field(min_length=1)

    @field_validator("id", "path")
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        """Reject identifiers and paths containing only whitespace.

        Args:
            value: Candidate identifier or filesystem path.

        Returns:
            The original non-blank value, preserving meaningful whitespace in
            paths rather than silently normalizing source metadata.

        Raises:
            ValueError: If the supplied string is blank.
        """

        if not value.strip():
            raise ValueError("Image id and path must not be blank")
        return value


def _normalize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Validate the reserved ``images`` field while preserving custom metadata.

    Args:
        metadata: Extensible document or chunk metadata mapping.

    Returns:
        A shallow copy whose ``images`` entries are validated and serialized as
        ordinary dictionaries.

    Raises:
        ValueError: If ``images`` is not a list.
        pydantic.ValidationError: If an image entry violates
            ``ImageMetadata``.
    """

    normalized = dict(metadata)
    if "images" not in normalized:
        return normalized

    images = normalized["images"]
    if not isinstance(images, list):
        raise ValueError("metadata.images must be a list")
    normalized["images"] = [ImageMetadata.model_validate(image).model_dump() for image in images]
    return normalized


class Document(DomainModel):
    """Represent one canonical source document before business chunking.

    Loaders convert PDF, Markdown, or future source formats into this contract.
    The metadata mapping remains extensible for source-specific fields, while
    the reserved ``images`` list is normalized to the shared image schema.
    """

    id: str = Field(min_length=1)
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def reject_blank_id(cls, value: str) -> str:
        """Require a usable stable document identifier.

        Args:
            value: Candidate document ID.

        Returns:
            The original non-blank identifier.

        Raises:
            ValueError: If the ID contains only whitespace.
        """

        if not value.strip():
            raise ValueError("Document id must not be blank")
        return value

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Require canonical content that can enter the ingestion pipeline.

        Args:
            value: Canonical text produced by a loader.

        Returns:
            The original text when it contains processable content.

        Raises:
            ValueError: If the document text is empty or whitespace-only.
        """

        if not value.strip():
            raise ValueError("Document text must not be blank")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata_images(cls, value: Any) -> dict[str, Any]:
        """Validate document metadata and normalize reserved image entries.

        Args:
            value: Candidate metadata mapping.

        Returns:
            Extensible metadata with JSON-compatible validated image mappings.

        Raises:
            ValueError: If metadata is not a mapping or ``images`` is not a
                list.
        """

        if not isinstance(value, dict):
            raise ValueError("Document metadata must be a mapping")
        return _normalize_metadata(value)

    @model_validator(mode="after")
    def validate_image_ranges(self) -> Self:
        """Ensure every image placeholder range fits inside document text.

        Returns:
            The validated document.

        Raises:
            ValueError: If an image placeholder extends beyond
                ``Document.text``.
        """

        text_length = len(self.text)
        for image in self.metadata.get("images", []):
            placeholder_end = image["text_offset"] + image["text_length"]
            if placeholder_end > text_length:
                raise ValueError(
                    f"Image '{image['id']}' placeholder range exceeds document "
                    f"text length {text_length}"
                )
        return self


class Chunk(DomainModel):
    """Represent one ordered, source-addressable unit of retrievable text.

    Offsets refer to the canonical source ``Document.text`` and use a
    start-inclusive, end-exclusive range. Transform stages may enhance
    ``text`` without changing those source coordinates. ``source_ref`` remains
    optional and extensible because loaders expose different citation details.
    """

    id: str = Field(min_length=1)
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunk_index: int = Field(ge=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    source_ref: dict[str, Any] | None = None

    @field_validator("id")
    @classmethod
    def reject_blank_id(cls, value: str) -> str:
        """Require a usable stable chunk identifier.

        Args:
            value: Candidate chunk ID.

        Returns:
            The original non-blank identifier.

        Raises:
            ValueError: If the ID contains only whitespace.
        """

        if not value.strip():
            raise ValueError("Chunk id must not be blank")
        return value

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Prevent empty content from entering embedding or BM25 indexing.

        Args:
            value: Candidate searchable chunk text.

        Returns:
            The original text when it contains at least one non-whitespace
            character.

        Raises:
            ValueError: If the text is empty or whitespace-only.
        """

        if not value.strip():
            raise ValueError("Chunk text must not be blank")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata_images(cls, value: Any) -> dict[str, Any]:
        """Preserve metadata inheritance while validating optional images.

        Args:
            value: Candidate chunk metadata mapping copied from a document and
                enriched by ``DocumentChunker`` or transforms.

        Returns:
            Extensible metadata with normalized image mappings.

        Raises:
            ValueError: If metadata is not a mapping or contains an invalid
                ``images`` list.
        """

        if not isinstance(value, dict):
            raise ValueError("Chunk metadata must be a mapping")
        return _normalize_metadata(value)

    @model_validator(mode="after")
    def validate_source_range(self) -> Self:
        """Ensure the chunk source range is ordered.

        Returns:
            The validated chunk.

        Raises:
            ValueError: If ``end_offset`` is not greater than ``start_offset``.
        """

        if self.end_offset <= self.start_offset:
            raise ValueError("Chunk end_offset must be greater than start_offset")
        return self


class RetrievalResult(DomainModel):
    """Carry one retrieval route's chunk payload and native relevance score.

    Dense and BM25 routes use incomparable score scales. The model therefore
    requires only a finite float and deliberately performs no normalization or
    cross-route ordering.
    """

    chunk_id: str = Field(min_length=1)
    text: str
    score: float = Field(allow_inf_nan=False)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("chunk_id")
    @classmethod
    def reject_blank_chunk_id(cls, value: str) -> str:
        """Require the stable ID needed for fusion, filtering, and citation.

        Args:
            value: Candidate retrieved chunk ID.

        Returns:
            The original non-blank ID.

        Raises:
            ValueError: If the ID contains only whitespace.
        """

        if not value.strip():
            raise ValueError("RetrievalResult chunk_id must not be blank")
        return value

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Require answerable content for every retrieved chunk.

        Args:
            value: Candidate retrieved chunk text.

        Returns:
            The original text when it contains meaningful content.

        Raises:
            ValueError: If the retrieved text is empty or whitespace-only.
        """

        if not value.strip():
            raise ValueError("RetrievalResult text must not be blank")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def require_metadata_mapping(cls, value: Any) -> dict[str, Any]:
        """Require retrieval metadata to remain a filterable mapping.

        Args:
            value: Candidate metadata associated with the retrieved chunk.

        Returns:
            A shallow copy of the supplied mapping.

        Raises:
            ValueError: If metadata is not a mapping.
        """

        if not isinstance(value, dict):
            raise ValueError("RetrievalResult metadata must be a mapping")
        return dict(value)
