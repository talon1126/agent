"""Convert one referenced document image into structured caption metadata.

``ImageToTextTransform`` is a small adapter around an injected Vision-capable
LLM. It does not read image bytes, write chunk metadata, decide whether a chunk
needs captioning, or mutate ``Chunk`` objects. Those orchestration concerns
belong to ``ImageCaptioner``. This separation keeps image understanding
replaceable while preserving a simple unit-testable boundary for C5.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from src.core.config import PromptTemplate
from src.libs.llm import BaseLLM, ChatMessage

_LOW_QUALITY_DESCRIPTION_LENGTH = 8


class ImageToTextTransform:
    """Call a Vision LLM and normalize one image-caption response."""

    def __init__(
        self,
        *,
        vision_llm: BaseLLM,
        prompt: PromptTemplate,
    ) -> None:
        """Configure the Vision model and immutable Prompt contract.

        Args:
            vision_llm: Provider-independent LLM client used by tests and
                future multimodal adapters. The current text-only contract
                passes image metadata and context; a later provider can extend
                the client internals without changing this class.
            prompt: Validated image-to-text Prompt loaded from configuration.
        """

        self._vision_llm = vision_llm
        self._prompt = prompt

    def transform(
        self,
        image: Mapping[str, Any],
        *,
        document_context: str = "",
    ) -> dict[str, Any]:
        """Generate and normalize one image caption.

        Args:
            image: Image metadata produced by Loader or ImageStorage. The
                mapping must include ``id`` and may include ``image_type``,
                ``path``, ``page``, and physical position details.
            document_context: Nearby source text and section information used
                to guide the Vision model toward retrieval-oriented captions.

        Returns:
            A trace-safe caption mapping containing status, description,
            extracted text, key facts, reason, provider, and model.

        Raises:
            Exception: Provider or parsing failures are intentionally allowed to
                bubble to ``ImageCaptioner``, which records failed caption
                metadata while preserving the source chunk.
        """

        response = self._vision_llm.chat(
            [
                ChatMessage(role="system", content=self._prompt.system_prompt),
                ChatMessage(
                    role="user",
                    content=self._prompt.user_prompt.format(
                        image_type=str(image.get("image_type") or "document_image"),
                        document_context=_compose_document_context(
                            image=image,
                            document_context=document_context,
                        ),
                    ),
                ),
            ]
        )
        payload = json.loads(response.content)
        if not isinstance(payload, dict):
            payload = {}

        status = str(payload.get("status") or "success")
        description = str(payload.get("description") or "").strip()
        if (
            status != "low_quality"
            and len(description) < _LOW_QUALITY_DESCRIPTION_LENGTH
        ):
            status = "low_quality"

        return {
            "status": status,
            "description": description,
            "extracted_text": str(payload.get("extracted_text") or ""),
            "key_facts": [
                str(fact)
                for fact in payload.get("key_facts", [])
                if str(fact).strip()
            ],
            "reason": str(payload.get("reason") or ""),
            "provider": response.provider,
            "model": response.model,
        }


def _compose_document_context(
    *,
    image: Mapping[str, Any],
    document_context: str,
) -> str:
    """Build the prompt context without exposing raw internal objects.

    Args:
        image: Source image metadata.
        document_context: Caller-provided nearby text or section summary.

    Returns:
        A compact JSON string with image metadata and nearby context. Keeping
        this structured makes fake tests deterministic and future Vision
        provider traces easier to inspect.
    """

    return json.dumps(
        {
            "image_id": image.get("id"),
            "path": image.get("path"),
            "page": image.get("page"),
            "position": image.get("position"),
            "nearby_text": document_context,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
