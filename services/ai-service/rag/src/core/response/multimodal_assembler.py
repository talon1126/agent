"""Assemble public image references for ranked retrieval results.

``MultimodalAssembler`` bridges retrieval metadata and the persisted image
index. It collects stable ``image_refs`` from ranked chunks, performs one batch
lookup through an injected resolver, and returns a deliberately small public
contract. Raw chunk metadata, provider responses, image hashes, and internal
tool payloads never cross this response boundary.

The module does not read image bytes, generate captions, expose HTTP asset
URLs, or decide whether an image should influence ranking. Those concerns
belong to storage, ingestion transforms, transport adapters, and retrieval.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.types import RetrievalResult


class ImageIndexRecordLike(Protocol):
    """Describe the image-index fields required by response assembly."""

    image_id: str
    file_path: str
    mime_type: str | None
    page_num: int | None
    width: int | None
    height: int | None
    quality_status: str
    metadata: dict[str, Any]


class ImageResolver(Protocol):
    """Define the minimal batch lookup used by ``MultimodalAssembler``."""

    def find_by_ids(
        self,
        image_ids: list[str],
    ) -> Sequence[ImageIndexRecordLike]:
        """Return persisted records matching the requested stable image IDs.

        Args:
            image_ids: Ordered unique image IDs collected from ranked chunks.

        Returns:
            Matching image-index records in any order. The assembler restores
            the original reference order.
        """


class ResponseImage(BaseModel):
    """Represent one image safe to expose through public RAG responses.

    Attributes:
        image_id: Stable image identifier referenced by one or more chunks.
        file_path: Managed image path stored by ``ImageStorage``. Transport
            adapters may later convert this path into a downloadable URL.
        mime_type: Persisted MIME type when known.
        page: Source-document page number when known.
        width: Persisted image width in pixels when known.
        height: Persisted image height in pixels when known.
        caption: Retrieval-oriented caption when ingestion produced one.
        quality_status: Persisted image processing quality state.
        chunk_ids: Ranked chunks that referenced the image.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    image_id: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    mime_type: str | None = None
    page: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    caption: str | None = None
    quality_status: str = Field(min_length=1)
    chunk_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator(
        "image_id",
        "file_path",
        "quality_status",
    )
    @classmethod
    def reject_blank_required_strings(cls, value: str) -> str:
        """Reject whitespace-only public image fields.

        Args:
            value: Candidate image identifier, managed path, or quality state.

        Returns:
            The original non-blank string.

        Raises:
            ValueError: If the supplied string contains only whitespace.
        """

        if not value.strip():
            raise ValueError("Response image string fields must not be blank")
        return value

    @field_validator("caption")
    @classmethod
    def normalize_optional_caption(cls, value: str | None) -> str | None:
        """Normalize empty persisted captions to an absent public caption.

        Args:
            value: Caption text copied from image-index metadata.

        Returns:
            Stripped caption text or ``None`` when no useful caption exists.
        """

        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("chunk_ids")
    @classmethod
    def validate_chunk_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require ordered unique non-blank chunk references.

        Args:
            value: Ranked chunk IDs associated with this image.

        Returns:
            The validated tuple in original rank order.

        Raises:
            ValueError: If an ID is blank or repeated.
        """

        if any(not chunk_id.strip() for chunk_id in value):
            raise ValueError("Response image chunk_ids must not contain blanks")
        if len(set(value)) != len(value):
            raise ValueError("Response image chunk_ids must be unique")
        return value


class MultimodalAssembler:
    """Resolve ranked chunk image references into public response images."""

    def __init__(self, *, resolver: ImageResolver | None = None) -> None:
        """Configure optional persisted-image resolution.

        Args:
            resolver: Batch image-index lookup, normally ``ImageStorage``.
                ``None`` keeps text responses available when multimodal storage
                is intentionally not configured.
        """

        self._resolver = resolver

    def assemble(
        self,
        candidates: Sequence[RetrievalResult],
    ) -> list[ResponseImage]:
        """Resolve unique image references while preserving retrieval order.

        Args:
            candidates: Final ranked retrieval results after filtering and
                reranking.

        Returns:
            Public image records in first-reference order. Missing persisted
            image IDs are skipped because a stale optional image reference must
            not suppress otherwise valid textual knowledge.

        Raises:
            ValueError: If ``metadata.image_refs`` is not a list of non-blank
                strings, or if the resolver returns duplicate records for one
                stable image ID.

        Side Effects:
            Performs at most one resolver lookup when image references exist.
            Input retrieval results and metadata are never mutated.
        """

        image_ids, chunk_ids_by_image = self._collect_references(candidates)
        if not image_ids or self._resolver is None:
            return []

        records_by_id: dict[str, ImageIndexRecordLike] = {}
        for record in self._resolver.find_by_ids(image_ids):
            if record.image_id in records_by_id:
                raise ValueError(
                    f"Image resolver returned duplicate record '{record.image_id}'"
                )
            if record.image_id in chunk_ids_by_image:
                records_by_id[record.image_id] = record

        return [
            self._to_response_image(
                records_by_id[image_id],
                chunk_ids=tuple(chunk_ids_by_image[image_id]),
            )
            for image_id in image_ids
            if image_id in records_by_id
        ]

    @staticmethod
    def _collect_references(
        candidates: Sequence[RetrievalResult],
    ) -> tuple[list[str], dict[str, list[str]]]:
        """Collect ordered unique image IDs and their ranked chunk owners.

        Args:
            candidates: Final ranked retrieval results.

        Returns:
            Ordered image IDs and a mapping to ordered unique chunk IDs.

        Raises:
            ValueError: If an ``image_refs`` value violates the ingestion
                contract.
        """

        image_ids: list[str] = []
        chunk_ids_by_image: dict[str, list[str]] = {}
        for candidate in candidates:
            refs = candidate.metadata.get("image_refs")
            if refs is None:
                continue
            if not isinstance(refs, list):
                raise ValueError(
                    f"image_refs must be a list for chunk '{candidate.chunk_id}'"
                )
            for image_id in refs:
                if not isinstance(image_id, str) or not image_id.strip():
                    raise ValueError(
                        f"image_refs must contain non-blank strings for chunk "
                        f"'{candidate.chunk_id}'"
                    )
                if image_id not in chunk_ids_by_image:
                    image_ids.append(image_id)
                    chunk_ids_by_image[image_id] = []
                if candidate.chunk_id not in chunk_ids_by_image[image_id]:
                    chunk_ids_by_image[image_id].append(candidate.chunk_id)
        return image_ids, chunk_ids_by_image

    @staticmethod
    def _to_response_image(
        record: ImageIndexRecordLike,
        *,
        chunk_ids: tuple[str, ...],
    ) -> ResponseImage:
        """Project an internal image-index record onto the public contract.

        Args:
            record: Persisted image-index record returned by the resolver.
            chunk_ids: Ranked chunks that referenced the image.

        Returns:
            A validated immutable ``ResponseImage``.

        Notes:
            Only the retrieval caption is selected from extensible metadata.
            Hashes, source extraction paths, provider details, and arbitrary
            metadata remain internal.
        """

        caption = record.metadata.get("caption")
        return ResponseImage(
            image_id=record.image_id,
            file_path=record.file_path,
            mime_type=record.mime_type,
            page=record.page_num,
            width=record.width,
            height=record.height,
            caption=caption if isinstance(caption, str) else None,
            quality_status=record.quality_status,
            chunk_ids=chunk_ids,
        )
