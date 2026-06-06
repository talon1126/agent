"""Adapt Bailian-hosted DeepSeek chat models to the local LLM contract.

Alibaba Cloud Bailian exposes DeepSeek models through an OpenAI-compatible
endpoint. This adapter resolves credentials and endpoint values from the
environment-variable names stored in ``settings.yaml``, converts local
``ChatMessage`` objects into SDK payloads, and returns trace-safe
``LLMResponse`` metadata.

The adapter never logs API keys, provider exception text, or complete SDK
objects. Tests can inject an OpenAI-compatible client to avoid network access.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from openai import OpenAI

from src.core.errors import ConfigurationError, ProviderError
from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse


class DeepSeekClient(BaseLLM):
    """Call a Bailian DeepSeek model through an OpenAI-compatible client."""

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str | None = None,
        base_url_env: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 60,
        client: Any | None = None,
        environ: Mapping[str, str] | None = None,
        **_: Any,
    ) -> None:
        """Configure the provider client without exposing secret values.

        Args:
            model: Bailian model identifier, normally ``deepseek-v4-flash``.
            api_key_env: Environment variable containing the Bailian API key.
            base_url_env: Environment variable containing the compatible API
                endpoint.
            base_url: Optional literal endpoint used when no environment-backed
                endpoint is configured.
            timeout_seconds: SDK request timeout.
            client: Optional OpenAI-compatible client injected by unit tests.
            environ: Optional isolated environment mapping for tests.
            **_: Forward-compatible provider settings ignored by this adapter.

        Raises:
            ConfigurationError: If model, timeout, API key, or endpoint
                configuration is invalid.

        Side Effects:
            Creates an OpenAI SDK client when ``client`` is not supplied. Client
            construction performs no model request.
        """

        if not model.strip():
            raise ConfigurationError("DeepSeek model must not be blank")
        if timeout_seconds <= 0:
            raise ConfigurationError("DeepSeek timeout must be positive")

        self._model = model
        if client is not None:
            self._client = client
            return

        environment = os.environ if environ is None else environ
        api_key = self._resolve_environment_value(
            environment,
            reference=api_key_env,
            setting="api_key_env",
        )
        resolved_base_url = base_url
        if base_url_env:
            resolved_base_url = self._resolve_environment_value(
                environment,
                reference=base_url_env,
                setting="base_url_env",
            )
        if not resolved_base_url or not resolved_base_url.strip():
            raise ConfigurationError(
                "DeepSeek compatible endpoint must be configured",
                context={"provider": "deepseek"},
            )

        try:
            self._client = OpenAI(
                api_key=api_key,
                base_url=resolved_base_url,
                timeout=timeout_seconds,
            )
        except Exception as error:
            raise ConfigurationError(
                "Unable to initialize DeepSeek SDK client",
                context={"provider": "deepseek"},
                cause=error,
            ) from error

    def chat(self, messages: list[ChatMessage]) -> LLMResponse:
        """Generate one normalized response through Bailian DeepSeek.

        Args:
            messages: Ordered local chat messages. At least one message is
                required and the list is not mutated.

        Returns:
            Generated content plus trace-safe response ID, finish reason, and
            token counts.

        Raises:
            ProviderError: If no messages are supplied, the SDK request fails,
                or the provider returns no usable text.
        """

        if not messages:
            raise ProviderError(
                "DeepSeek chat requires at least one message",
                context={"provider": "deepseek", "model": self._model},
            )

        payload: list[dict[str, str]] = []
        for message in messages:
            item = {"role": message.role, "content": message.content}
            if message.name is not None:
                item["name"] = message.name
            payload.append(item)

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=payload,
            )
        except Exception as error:
            raise ProviderError(
                "DeepSeek chat request failed",
                context={"provider": "deepseek", "model": self._model},
                cause=error,
            ) from error

        choices = getattr(response, "choices", None)
        content = (
            getattr(getattr(choices[0], "message", None), "content", None)
            if choices
            else None
        )
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(
                "DeepSeek returned an empty response",
                context={"provider": "deepseek", "model": self._model},
            )

        first_choice = choices[0]
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=content,
            provider="deepseek",
            model=self._model,
            raw={
                "response_id": getattr(response, "id", None),
                "finish_reason": getattr(first_choice, "finish_reason", None),
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            },
        )

    @staticmethod
    def _resolve_environment_value(
        environment: Mapping[str, str],
        *,
        reference: str | None,
        setting: str,
    ) -> str:
        """Resolve one required secret or endpoint environment reference.

        Args:
            environment: Environment mapping selected by the caller.
            reference: Environment-variable name from provider settings.
            setting: Trace-safe settings field used in error context.

        Returns:
            Non-blank environment value.

        Raises:
            ConfigurationError: If the reference or referenced value is absent.
        """

        if not reference or not reference.strip():
            raise ConfigurationError(
                f"DeepSeek {setting} must be configured",
                context={"provider": "deepseek", "setting": setting},
            )
        value = environment.get(reference, "").strip()
        if not value:
            raise ConfigurationError(
                f"Missing DeepSeek environment variable: {reference}",
                context={
                    "provider": "deepseek",
                    "environment_variable": reference,
                },
            )
        return value
