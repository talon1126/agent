"""Inject Vision LLM image captions into searchable chunk text.

``ImageCaptioner`` is a Transform step, not a generic provider factory. It
uses ``image_refs`` on chunks and document-level ``images`` supplied through
the transform context to find local image files, call a Vision LLM, and replace
``[[image:...]]`` placeholders with ``[[image_caption:...]]`` nodes plus the
caption text. Original provider captions are also written into
``image_caption_artifacts`` on the runtime context so persistence can keep an
auditable caption even when later rewrite steps merge the caption into natural
chunk text. Execution details are exposed through ``trace_details()`` so
Transform Pipeline can attach them to ``transform.sub_stages``.
"""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.core.errors import RagError
from src.core.types import Chunk
from src.ingestion.chunk.chunk_id import build_chunk_id
from src.libs.llm import BaseVisionLLM, VisionCaptionResponse
from src.libs.transform.base_transform import BaseTransform

_IMAGE_PLACEHOLDER = re.compile(r"\[\[image:(?P<image_id>[^\]]+)\]\]")


class ImageCaptioner(BaseTransform):
    """Generate image captions and write them into chunk text."""

    def __init__(
        self,
        *,
        vision_llm: BaseVisionLLM | Any | None,
        prompt: Any | None,
        enabled: bool,
    ) -> None:
        """Configure caption execution.

        Args:
            vision_llm: Object exposing ``caption_image(...)``. Tests may pass
                a mock; production passes a concrete ``BaseVisionLLM``.
            prompt: Prompt document loaded from ``image_caption_prompt.yaml``.
            enabled: Feature switch derived from ``vision_llm.enabled`` and
                whether a Vision client is available.
        """

        self._vision_llm = vision_llm
        self._prompt = prompt
        self._enabled = enabled
        self._last_events: list[dict[str, Any]] = []

    def should_caption(self, chunk: Chunk) -> bool:
        """Return whether one chunk has image references worth processing."""

        return self._enabled and self._vision_llm is not None and _has_image_refs(chunk)

    def transform(
        self,
        chunks: list[Chunk],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Satisfy the ``BaseTransform`` contract by delegating to captioning."""

        return self.caption(chunks, context=context)

    def caption(
        self,
        chunks: list[Chunk],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Caption referenced images while preserving chunk order.

        Args:
            chunks: Ordered chunks whose metadata may include ``image_refs``.
            context: Runtime context. ``document_images`` should contain the
                parent ``Document.metadata.images`` list. When provided,
                ``image_caption_artifacts`` receives provider captions keyed by
                image ID for the later upsert stage.

        Returns:
            New chunk objects. Successful captions update chunk text; skipped,
            failed, and low-quality captions preserve original text.
        """

        runtime_context = context if context is not None else {}
        image_index = _document_image_index(runtime_context.get("document_images"))
        artifacts = _artifact_store(runtime_context)
        self._last_events = []
        result_cache: dict[str, VisionCaptionResponse] = {}
        output: list[Chunk] = []
        for chunk in chunks:
            output.append(
                self._caption_chunk(
                    chunk,
                    image_index=image_index,
                    result_cache=result_cache,
                    artifacts=artifacts,
                )
            )
        return output

    def trace_details(self) -> dict[str, Any]:
        """Return trace-safe execution details for the previous run."""

        statuses = Counter(str(event["status"]) for event in self._last_events)
        successful = [
            event for event in self._last_events if event["status"] == "success"
        ]
        provider = _first_non_blank(event.get("provider") for event in self._last_events)
        model = _first_non_blank(event.get("model") for event in self._last_events)
        return {
            "provider": provider,
            "model": model,
            "image_count": len(self._last_events),
            "caption_count": len(successful),
            "status_counts": dict(statuses),
            "failures": [
                _failure_event(event)
                for event in self._last_events
                if event.get("status") in {"failed", "low_quality", "skipped"}
            ],
        }

    def _caption_chunk(
        self,
        chunk: Chunk,
        *,
        image_index: dict[str, dict[str, Any]],
        result_cache: dict[str, VisionCaptionResponse],
        artifacts: dict[str, dict[str, Any]],
    ) -> Chunk:
        """Caption every placeholder referenced by one chunk."""

        image_refs = _ordered_string_refs(chunk.metadata.get("image_refs", []))
        if not image_refs:
            return chunk.model_copy(deep=True)
        if not self.should_caption(chunk):
            for image_id in image_refs:
                self._record_event(image_id=image_id, status="skipped", reason="disabled")
            return chunk.model_copy(deep=True)

        replacements: dict[str, str] = {}
        for image_id in image_refs:
            image = image_index.get(image_id)
            if image is None:
                self._record_event(
                    image_id=image_id,
                    status="failed",
                    reason="image metadata not found",
                )
                continue
            normalized = result_cache.get(image_id)
            if normalized is None:
                try:
                    response = self._vision_llm.caption_image(
                        image["path"],
                        prompt=self._prompt,
                        image_type=str(image.get("image_type") or "product"),
                    )
                    normalized = _normalize_response(response)
                except Exception as error:
                    normalized = _failed_response(error)
                result_cache[image_id] = normalized
                self._record_event(
                    image_id=image_id,
                    status=normalized.status,
                    reason=normalized.reason,
                    provider=normalized.provider,
                    model=normalized.model,
                    error_type=str(normalized.raw.get("error_type") or ""),
                )
            _record_caption_artifact(
                artifacts,
                image_id=image_id,
                response=normalized,
                chunk_id=chunk.id,
            )
            if normalized.status == "success":
                replacements[image_id] = _caption_node(
                    image_id=image_id,
                    description=normalized.description,
                )

        if not replacements:
            return chunk.model_copy(deep=True)
        updated_text = _replace_placeholders(chunk.text, replacements)
        if updated_text == chunk.text:
            return chunk.model_copy(deep=True)
        section_path = chunk.metadata.get("section_path", [])
        if not isinstance(section_path, list):
            section_path = []
        updated = chunk.model_copy(
            update={
                "id": build_chunk_id(
                    source_path=str(
                        chunk.metadata.get(
                            "source_path",
                            chunk.metadata.get("document_id", chunk.id),
                        )
                    ),
                    section_path=[str(item) for item in section_path],
                    text=updated_text,
                ),
                "text": updated_text,
                "metadata": deepcopy(chunk.metadata),
            },
            deep=True,
        )
        return updated

    def _record_event(
        self,
        *,
        image_id: str,
        status: str,
        reason: str = "",
        provider: str | None = None,
        model: str | None = None,
        error_type: str = "",
    ) -> None:
        """Append one compact event used by ``trace_details()``."""

        self._last_events.append(
            {
                "image_id": image_id,
                "status": status,
                "reason": reason,
                "provider": provider,
                "model": model,
                "error_type": error_type,
            }
        )


def _document_image_index(value: Any) -> dict[str, dict[str, Any]]:
    """Normalize document-level image metadata into an ID lookup."""

    if not isinstance(value, list):
        return {}
    return {
        str(image["id"]): dict(image)
        for image in value
        if isinstance(image, dict)
        and image.get("id")
        and image.get("path")
        and Path(str(image.get("path"))).name
    }


def _artifact_store(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the mutable caption-artifact store for the current run.

    Args:
        context: Runtime transform context shared by configured transform
            steps during one ingestion run.

    Returns:
        A dictionary keyed by image ID. The store is created when missing so
        ImageCaptioner can publish structured caption data without requiring
        the pipeline to pre-populate the optional key in focused unit tests.
    """

    value = context.setdefault("image_caption_artifacts", {})
    if isinstance(value, dict):
        return value
    replacement: dict[str, dict[str, Any]] = {}
    context["image_caption_artifacts"] = replacement
    return replacement


def _record_caption_artifact(
    artifacts: dict[str, dict[str, Any]],
    *,
    image_id: str,
    response: VisionCaptionResponse,
    chunk_id: str,
) -> None:
    """Store one provider caption result for persistence after Transform.

    Args:
        artifacts: Mutable runtime artifact store shared with the pipeline.
        image_id: Stable image identifier referenced by the current chunk.
        response: Provider-independent caption response.
        chunk_id: Chunk identity before ImageCaptioner rewrites the chunk text
            and therefore before downstream transforms may derive new IDs.

    Side Effects:
        Updates ``artifacts`` in place. Repeated references to the same image
        merge their source chunk IDs while preserving the first provider
        caption payload.
    """

    existing = artifacts.get(image_id)
    source_chunk_ids: list[str]
    if existing is None:
        source_chunk_ids = []
        artifacts[image_id] = {
            "image_id": image_id,
            "caption": response.description,
            "status": response.status,
            "provider": response.provider,
            "model": response.model,
            "reason": response.reason,
            "source_chunk_ids": source_chunk_ids,
        }
    else:
        raw_ids = existing.setdefault("source_chunk_ids", [])
        source_chunk_ids = raw_ids if isinstance(raw_ids, list) else []
        existing["source_chunk_ids"] = source_chunk_ids
    if chunk_id not in source_chunk_ids:
        source_chunk_ids.append(chunk_id)


def _normalize_response(response: VisionCaptionResponse | Any) -> VisionCaptionResponse:
    """Accept typed or duck-typed Vision caption responses."""

    if isinstance(response, VisionCaptionResponse):
        if response.status == "success" and len(response.description.strip()) < 8:
            return response.model_copy(
                update={
                    "status": "low_quality",
                    "reason": response.reason or "caption too short",
                }
            )
        return response
    status = str(getattr(response, "status", "success"))
    description = str(getattr(response, "description", "")).strip()
    if status not in {"success", "low_quality", "failed"}:
        status = "failed"
    if status == "success" and len(description) < 8:
        status = "low_quality"
    return VisionCaptionResponse(
        status=status,
        description=description,
        reason=str(getattr(response, "reason", "")),
        provider=str(getattr(response, "provider", "unknown")),
        model=str(getattr(response, "model", "unknown")),
    )


def _failed_response(error: Exception) -> VisionCaptionResponse:
    """Convert one provider exception into a trace-safe failed response."""

    context = error.context if isinstance(error, RagError) else {}
    cause = error.cause if isinstance(error, RagError) else error.__cause__
    cause_type = type(cause).__name__ if cause is not None else type(error).__name__
    return VisionCaptionResponse(
        status="failed",
        description="",
        reason=_safe_error_reason(error, cause=cause),
        provider=_optional_context_string(context.get("provider")) or "unknown",
        model=_optional_context_string(context.get("model")) or "unknown",
        raw={"error_type": cause_type},
    )


def _safe_error_reason(error: Exception, *, cause: Exception | None) -> str:
    """Build a bounded diagnostic message without persisting image payloads."""

    reason = str(error)
    if cause is not None and cause is not error:
        reason = f"{reason}: {type(cause).__name__}: {cause}"
    reason = re.sub(
        r"data:[^;\s]+;base64,[A-Za-z0-9+/=_-]+",
        "[redacted-base64-image]",
        reason,
    )
    reason = re.sub(
        r"(?i)\b(api[_-]?key|authorization)\s*[=:]\s*[^\s;,]+",
        lambda match: f"{match.group(1)}=[redacted-secret]",
        reason,
    )
    reason = re.sub(
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [redacted-secret]",
        reason,
    )
    return reason[:1000]


def _optional_context_string(value: Any) -> str | None:
    """Return a stripped context string or ``None`` for missing values."""

    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def _failure_event(event: dict[str, Any]) -> dict[str, str]:
    """Project one failed caption event into compact trace details."""

    result = {
        "image_id": str(event.get("image_id") or ""),
        "status": str(event.get("status") or ""),
        "reason": str(event.get("reason") or ""),
    }
    if event.get("error_type"):
        result["error_type"] = str(event["error_type"])
    return result


def _replace_placeholders(text: str, replacements: dict[str, str]) -> str:
    """Replace image placeholders whose caption succeeded."""

    def replace(match: re.Match[str]) -> str:
        image_id = match.group("image_id").strip()
        return replacements.get(image_id, match.group(0))

    return _IMAGE_PLACEHOLDER.sub(replace, text)


def _caption_node(*, image_id: str, description: str) -> str:
    """Build the text node injected in place of one image placeholder."""

    return f"[[image_caption:{image_id}]]\n{description.strip()}\n"


def _ordered_string_refs(values: Any) -> list[str]:
    """Return unique non-blank image references in source order."""

    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _has_image_refs(chunk: Chunk) -> bool:
    """Return whether one chunk carries at least one usable image reference."""

    return bool(_ordered_string_refs(chunk.metadata.get("image_refs", [])))


def _first_non_blank(values: Any) -> str | None:
    """Return the first non-blank string from an iterable."""

    for value in values:
        if value is not None and str(value).strip():
            return str(value)
    return None
