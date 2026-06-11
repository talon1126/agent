"""Define provider-independent Vision LLM contracts for image captioning.

The ingestion pipeline only needs one multimodal capability: turn a local
image file plus nearby document context into a retrieval-oriented caption.
This module keeps that contract separate from the text-only ``BaseLLM`` chat
interface so ImageCaptioner can depend on a small, explicit API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VisionCaptionResponse(BaseModel):
    """Represent one normalized image caption result.

    Attributes:
        status: Caption quality status. ``success`` means the description can
            be injected into searchable chunk text. ``low_quality`` means the
            image should keep its original placeholder. ``failed`` is reserved
            for provider or parsing failures.
        description: Retrieval-oriented Simplified Chinese caption.
        reason: Optional provider or quality reason used by trace details.
        provider: Provider label used by Dashboard and trace views.
        model: Model identifier used by the provider implementation.
        raw: Trace-safe provider metadata. Full SDK responses and secrets must
            not be stored here.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    status: Literal["success", "low_quality", "failed"] = "success"
    description: str = ""
    reason: str = ""
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider", "model")
    @classmethod
    def reject_blank_required_strings(cls, value: str) -> str:
        """Reject blank provider identity used by observability output."""

        if not value.strip():
            raise ValueError("Vision provider and model must not be blank")
        return value


class BaseVisionLLM(ABC):
    """Provide the minimal unified interface for image caption providers."""

    @abstractmethod
    def caption_image(
        self,
        image_path: str | Path,
        *,
        prompt: Any | None = None,
        image_type: str = "product",
        document_context: str = "",
    ) -> VisionCaptionResponse:
        """Generate a retrieval-oriented caption for one local image.

        Args:
            image_path: Local image path resolved by the Loader.
            prompt: Optional prompt document loaded from configuration.
            image_type: Strategy key used by the prompt, such as ``product`` or
                ``table``.
            document_context: Nearby document text that helps the provider
                interpret the image without inventing unseen facts.

        Returns:
            Provider-independent caption response.

        Raises:
            ProviderError: Implementations should raise provider errors for
                transport, authentication, timeout, parsing, or SDK failures.
        """
