"""Provide a reusable OpenAI-compatible chat-model adapter.

Several RAG model providers expose the OpenAI chat-completions HTTP contract but
need different provider labels, model names, and endpoint configuration. This
module owns the shared SDK request/response normalization so concrete providers
such as DeepSeek and CCSwitch do not duplicate transport, validation, and
trace-safe metadata handling.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from openai import OpenAI

from src.core.errors import ConfigurationError, ProviderError
from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse


class OpenAICompatibleLLM(BaseLLM):
    """Call one OpenAI-compatible chat-completions provider.

    Concrete subclasses only supply provider identity and user-facing error
    labels. The adapter accepts literal credentials/endpoints for local proxies
    and environment-backed references for deployed providers.
    """

    provider_name = "openai_compatible"
    display_name = "OpenAI-compatible"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        api_key_env: str | None = None,
        base_url: str | None = None,
        base_url_env: str | None = None,
        timeout_seconds: int = 60,
        client: Any | None = None,
        environ: Mapping[str, str] | None = None,
        **_: Any,
    ) -> None:
        """Configure the SDK client without exposing secret values.

        Args:
            model: Provider model identifier sent to ``chat.completions``.
            api_key: Optional literal API key for local proxy services.
            api_key_env: Optional environment variable containing the API key.
            base_url: Optional literal OpenAI-compatible endpoint.
            base_url_env: Optional environment variable containing the endpoint.
            timeout_seconds: SDK request timeout in seconds.
            client: Optional OpenAI-compatible client injected by tests.
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
            raise ConfigurationError(f"{self.display_name} model must not be blank")
        if timeout_seconds <= 0:
            raise ConfigurationError(f"{self.display_name} timeout must be positive")

        self._model = model
        if client is not None:
            self._client = client
            return

        environment = os.environ if environ is None else environ
        resolved_api_key = self._resolve_config_value(
            environment,
            literal_value=api_key,
            reference=api_key_env,
            setting="api_key",
        )
        resolved_base_url = self._resolve_config_value(
            environment,
            literal_value=base_url,
            reference=base_url_env,
            setting="base_url",
        )

        try:
            self._client = OpenAI(
                api_key=resolved_api_key,
                base_url=resolved_base_url,
                timeout=timeout_seconds,
            )
        except Exception as error:
            raise ConfigurationError(
                f"Unable to initialize {self.display_name} SDK client",
                context={"provider": self.provider_name},
                cause=error,
            ) from error

    def chat(self, messages: list[ChatMessage]) -> LLMResponse:
        """Generate one normalized response through the configured provider.

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
                f"{self.display_name} chat requires at least one message",
                context={"provider": self.provider_name, "model": self._model},
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
                f"{self.display_name} chat request failed",
                context={"provider": self.provider_name, "model": self._model},
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
                f"{self.display_name} returned an empty response",
                context={"provider": self.provider_name, "model": self._model},
            )

        first_choice = choices[0]
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=self._model,
            raw={
                "response_id": getattr(response, "id", None),
                "finish_reason": getattr(first_choice, "finish_reason", None),
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            },
        )

    def _resolve_config_value(
        self,
        environment: Mapping[str, str],
        *,
        literal_value: str | None,
        reference: str | None,
        setting: str,
    ) -> str:
        """Resolve a literal value or an environment-backed provider setting.

        Args:
            environment: Environment mapping selected by the caller.
            literal_value: Direct settings value, used for trusted local proxies.
            reference: Environment-variable name from provider settings.
            setting: Trace-safe settings field used in error context.

        Returns:
            Non-blank configured value.

        Raises:
            ConfigurationError: If neither source provides a non-blank value.
        """

        if literal_value is not None and literal_value.strip():
            return literal_value.strip()
        if reference and reference.strip():
            value = environment.get(reference, "").strip()
            if value:
                return value
            raise ConfigurationError(
                f"Missing {self.display_name} environment variable: {reference}",
                context={
                    "provider": self.provider_name,
                    "environment_variable": reference,
                },
            )
        raise ConfigurationError(
            f"{self.display_name} {setting} must be configured",
            context={"provider": self.provider_name, "setting": setting},
        )
