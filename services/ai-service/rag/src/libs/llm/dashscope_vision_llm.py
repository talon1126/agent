"""Adapt DashScope Qwen-VL models to the local Vision LLM contract.

DashScope's Bailian endpoint can be called through an OpenAI-compatible client.
This adapter keeps image file loading, base64 data URL creation, prompt
rendering, and response normalization inside one provider-specific boundary.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.core.errors import ConfigurationError, ProviderError
from src.libs.llm.base_vision_llm import BaseVisionLLM, VisionCaptionResponse


class DashScopeVisionLLM(BaseVisionLLM):
    """Call a DashScope Qwen-VL model for image caption generation."""

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str | None = None,
        base_url_env: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 90,
        client: Any | None = None,
        environ: Mapping[str, str] | None = None,
        **_: Any,
    ) -> None:
        """Configure the provider client without exposing secret values.

        Args:
            model: DashScope multimodal model identifier, for example
                ``Qwen-VL-Max``.
            api_key_env: Environment variable containing the Bailian API key.
            base_url_env: Environment variable containing the compatible API
                endpoint.
            base_url: Optional literal endpoint used when no environment-backed
                endpoint is configured.
            timeout_seconds: SDK request timeout.
            client: Optional OpenAI-compatible client injected by tests.
            environ: Optional isolated environment mapping for tests.
            **_: Forward-compatible provider settings ignored by this adapter.

        Raises:
            ConfigurationError: If model, timeout, API key, or endpoint
                configuration is invalid.
        """

        if not model.strip():
            raise ConfigurationError("DashScope Vision model must not be blank")
        if timeout_seconds <= 0:
            raise ConfigurationError("DashScope Vision timeout must be positive")

        self._model = model
        if client is not None:
            self._client = client
            return

        environment = os.environ if environ is None else environ
        api_key = _resolve_environment_value(
            environment,
            reference=api_key_env,
            setting="api_key_env",
        )
        resolved_base_url = base_url
        if base_url_env:
            resolved_base_url = _resolve_environment_value(
                environment,
                reference=base_url_env,
                setting="base_url_env",
            )
        if not resolved_base_url:
            raise ConfigurationError(
                "DashScope Vision base URL is required",
                context={"setting": "base_url_env"},
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url=resolved_base_url,
            timeout=timeout_seconds,
        )

    def caption_image(
        self,
        image_path: str | Path,
        *,
        prompt: Any | None = None,
        image_type: str = "product",
    ) -> VisionCaptionResponse:
        """Generate one normalized caption for a local image.

        Args:
            image_path: Local image path emitted by the Loader.
            prompt: Prompt document loaded from settings. The object may be a
                local PromptTemplate or a plain mapping.
            image_type: Image strategy key rendered into the prompt.

        Returns:
            A provider-independent caption response.

        Raises:
            ProviderError: If the file cannot be read, the provider call fails,
                or the response cannot be normalized.
        """

        path = Path(image_path)
        try:
            content = path.read_bytes()
            image_url = _data_url(path, content)
            user_prompt = _render_user_prompt(
                prompt,
                image_type=image_type,
            )
            system_prompt = _system_prompt(prompt)
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": image_url},
                            },
                        ],
                    },
                ],
            )
            raw_content = response.choices[0].message.content
            return _caption_response_from_text(
                str(raw_content or ""),
                provider="dashscope",
                model=self._model,
            )
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(
                "Unable to caption image with DashScope Vision LLM",
                context={"provider": "dashscope", "model": self._model},
                cause=error,
            ) from error


def _resolve_environment_value(
    environment: Mapping[str, str],
    *,
    reference: str | None,
    setting: str,
) -> str:
    """Resolve one required secret or endpoint from an environment mapping."""

    if not reference:
        raise ConfigurationError(
            "DashScope Vision environment reference is required",
            context={"setting": setting},
        )
    value = environment.get(reference)
    if not value:
        raise ConfigurationError(
            "Missing DashScope Vision environment variable",
            context={"environment_variable": reference},
        )
    return value


def _data_url(path: Path, content: bytes) -> str:
    """Build a base64 image data URL accepted by OpenAI-compatible clients."""

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _system_prompt(prompt: Any | None) -> str:
    """Read the system prompt from a prompt document or use a safe default."""

    if hasattr(prompt, "system_prompt"):
        return str(prompt.system_prompt)
    if isinstance(prompt, Mapping):
        return str(prompt.get("system_prompt") or "")
    return (
        "Generate a retrieval-oriented Simplified Chinese image caption. "
        "Do not invent facts absent from the image."
    )


def _render_user_prompt(
    prompt: Any | None,
    *,
    image_type: str,
) -> str:
    """Render the configured user prompt with image strategy inputs."""

    variables = {
        "image_type": image_type,
    }
    if hasattr(prompt, "render_user_prompt"):
        return str(prompt.render_user_prompt(**variables))
    if hasattr(prompt, "user_prompt"):
        return str(prompt.user_prompt).format(**variables)
    if isinstance(prompt, Mapping):
        return str(prompt.get("user_prompt") or "").format(**variables)
    return (
        f"Image type:\n{image_type}\n\n"
        "Return JSON with status, description, extracted_text, key_facts, and reason."
    )


def _caption_response_from_text(
    content: str,
    *,
    provider: str,
    model: str,
) -> VisionCaptionResponse:
    """Normalize JSON or plain-text provider output into a caption response."""

    text = content.strip()
    if not text:
        raise ProviderError(
            "DashScope Vision LLM returned an empty caption",
            context={"provider": provider, "model": model},
        )
    try:
        payload = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError:
        return VisionCaptionResponse(
            status="success" if len(text) >= 8 else "low_quality",
            description=text,
            reason="" if len(text) >= 8 else "caption too short",
            provider=provider,
            model=model,
        )
    if not isinstance(payload, dict):
        raise ProviderError(
            "DashScope Vision LLM caption must be a JSON object",
            context={"provider": provider, "model": model},
        )
    status = str(payload.get("status") or "success")
    description = str(payload.get("description") or "").strip()
    if status not in {"success", "low_quality", "failed"}:
        status = "failed"
    if status == "success" and len(description) < 8:
        status = "low_quality"
    return VisionCaptionResponse(
        status=status,
        description=description,
        reason=str(payload.get("reason") or ""),
        provider=provider,
        model=model,
        raw={
            "extracted_text": payload.get("extracted_text") or "",
            "key_facts": payload.get("key_facts") or [],
        },
    )


def _strip_json_fence(text: str) -> str:
    """Remove a single Markdown JSON fence when a provider adds one."""

    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return text
