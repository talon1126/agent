"""Orchestrate optional image captioning for chunks with image references.

``ImageCaptioner`` is the ingestion-layer bridge between chunk metadata and
Vision LLM image understanding. It decides whether captioning should run,
maps ``image_refs`` to the source ``metadata.images`` entries, records skipped
or failed states, and writes structured caption metadata without changing chunk
text or source offsets.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.core.types import Chunk
from src.libs.transform.base_transform import BaseTransform


class ImageCaptioner(BaseTransform):
    """Generate image caption metadata for chunks that reference images."""

    def __init__(
        self,
        *,
        image_transform: Any | None,
        enabled: bool,
    ) -> None:
        """Configure caption execution.

        Args:
            image_transform: Object exposing ``transform(image,
                document_context=...)``. Tests often pass a mock; production
                code passes ``ImageToTextTransform``.
            enabled: Feature switch derived from ``vision_llm.enabled`` and
                whether a Vision client is available.
        """

        self._image_transform = image_transform
        self._enabled = enabled

    def should_caption(self, chunk: Chunk) -> bool:
        """Return whether one chunk has image references worth processing.

        Args:
            chunk: Candidate chunk from the ingestion transform chain.

        Returns:
            ``True`` only when the captioner is enabled, an image transform is
            available, and ``metadata.image_refs`` contains at least one image
            identifier.
        """

        return self._enabled and self._image_transform is not None and _has_image_refs(chunk)

    def transform(
        self,
        chunks: list[Chunk],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Satisfy the ``BaseTransform`` contract by delegating to captioning.

        Args:
            chunks: Ordered chunks from prior transform steps.
            context: Optional trace-safe document context.

        Returns:
            Ordered chunk copies with image caption metadata where applicable.
        """

        return self.caption(chunks, context=context)

    def caption(
        self,
        chunks: list[Chunk],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Caption every referenced image and preserve chunk order.

        Args:
            chunks: Ordered chunks whose metadata may include ``image_refs`` and
                an ``images`` list inherited from the source document.
            context: Optional runtime context. ``document_context`` can be
                supplied to guide the Vision model.

        Returns:
            New chunk objects. Text-only chunks are copied unchanged; disabled
            captioning marks image-bearing chunks as skipped; provider failures
            are captured as failed caption metadata.
        """

        return [self._caption_chunk(chunk, context=context or {}) for chunk in chunks]

    def write_metadata(
        self,
        chunk: Chunk,
        *,
        status: str,
        captions: list[dict[str, Any]] | None = None,
    ) -> Chunk:
        """Return a chunk copy with caption status and optional captions.

        Args:
            chunk: Source chunk to copy.
            status: Aggregate caption status for the chunk, such as
                ``success``, ``skipped``, ``failed``, or ``low_quality``.
            captions: Optional per-image caption records.

        Returns:
            A deep-copied chunk with caption metadata added.
        """

        metadata = deepcopy(chunk.metadata)
        metadata["image_caption_status"] = status
        if captions is not None:
            metadata["image_captions"] = deepcopy(captions)
        return chunk.model_copy(update={"metadata": metadata}, deep=True)

    def _caption_chunk(
        self,
        chunk: Chunk,
        *,
        context: dict[str, Any],
    ) -> Chunk:
        """Caption one chunk while isolating provider failures.

        Args:
            chunk: Source chunk that may reference images.
            context: Runtime metadata used to build nearby document context.

        Returns:
            A copied chunk with caption metadata or the original metadata
            copied unchanged when no image references exist.
        """

        if not _has_image_refs(chunk):
            return chunk.model_copy(deep=True)
        if not self.should_caption(chunk):
            return self.write_metadata(chunk, status="skipped", captions=[])

        image_index = {
            str(image.get("id")): image
            for image in chunk.metadata.get("images", [])
            if isinstance(image, dict) and image.get("id")
        }
        captions: list[dict[str, Any]] = []
        for image_id in _ordered_string_refs(chunk.metadata.get("image_refs", [])):
            image = image_index.get(image_id, {"id": image_id})
            try:
                caption = self._image_transform.transform(
                    image,
                    document_context=str(
                        context.get("document_context")
                        or context.get("title")
                        or chunk.text
                    ),
                )
                normalized = _normalize_caption(image_id=image_id, caption=caption)
            except Exception as error:
                normalized = {
                    "image_id": image_id,
                    "status": "failed",
                    "description": "",
                    "extracted_text": "",
                    "key_facts": [],
                    "reason": str(error),
                    "provider": None,
                    "model": None,
                }
            captions.append(normalized)

        aggregate_status = _aggregate_status(captions)
        return self.write_metadata(
            chunk,
            status=aggregate_status,
            captions=captions,
        )


def _normalize_caption(
    *,
    image_id: str,
    caption: dict[str, Any],
) -> dict[str, Any]:
    """Normalize one adapter result before storing it on chunk metadata.

    Args:
        image_id: Image reference currently being processed.
        caption: Raw adapter result from ``ImageToTextTransform`` or a test
            double.

    Returns:
        A JSON-compatible caption record with stable keys.
    """

    status = str(caption.get("status") or "success")
    description = str(caption.get("description") or "").strip()
    if status != "failed" and len(description) < 8:
        status = "low_quality"
    return {
        "image_id": image_id,
        "status": status,
        "description": description,
        "extracted_text": str(caption.get("extracted_text") or ""),
        "key_facts": [
            str(fact)
            for fact in caption.get("key_facts", [])
            if str(fact).strip()
        ],
        "reason": str(caption.get("reason") or ""),
        "provider": caption.get("provider"),
        "model": caption.get("model"),
    }


def _aggregate_status(captions: list[dict[str, Any]]) -> str:
    """Summarize per-image caption states for quick filtering.

    Args:
        captions: Per-image caption records for one chunk.

    Returns:
        ``failed`` when every caption failed, ``low_quality`` when at least one
        caption is low quality and none succeeded, otherwise ``success``.
    """

    statuses = {caption["status"] for caption in captions}
    if statuses == {"failed"}:
        return "failed"
    if "success" in statuses:
        return "success"
    if "low_quality" in statuses:
        return "low_quality"
    return "failed"


def _ordered_string_refs(values: Any) -> list[str]:
    """Return unique non-blank image references in source order.

    Args:
        values: Candidate ``metadata.image_refs`` value.

    Returns:
        Ordered string image IDs with duplicates removed.
    """

    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _has_image_refs(chunk: Chunk) -> bool:
    """Return whether one chunk carries at least one usable image reference.

    Args:
        chunk: Candidate chunk metadata to inspect.

    Returns:
        ``True`` when ``metadata.image_refs`` is a list with one or more
        non-blank image IDs.
    """

    return bool(_ordered_string_refs(chunk.metadata.get("image_refs", [])))
