"""Provide a deterministic LLM implementation for unit tests.

``FakeLLM`` exercises pipeline and factory code without external model access.
It records message counts in trace-safe metadata and returns a configured text
response through the same ``BaseLLM.chat()`` contract used by real providers.
"""

from __future__ import annotations

from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse


class FakeLLM(BaseLLM):
    """Return a configured response for every chat request."""

    def __init__(
        self,
        *,
        response_text: str = "Fake LLM response.",
        model: str = "fake-llm",
    ) -> None:
        """Configure deterministic fake-model behavior.

        Args:
            response_text: Text returned by every ``chat()`` call.
            model: Trace-visible fake model identifier.

        Raises:
            ValueError: If Pydantic validation rejects the eventual response
                fields when ``chat()`` is called.
        """

        self._response_text = response_text
        self._model = model

    def chat(self, messages: list[ChatMessage]) -> LLMResponse:
        """Return the configured response while preserving request metadata.

        Args:
            messages: Ordered normalized chat messages.

        Returns:
            An ``LLMResponse`` with deterministic content and a raw metadata
            section containing message count and roles for assertions or traces.
        """

        return LLMResponse(
            content=self._response_text,
            provider="fake",
            model=self._model,
            raw={
                "message_count": len(messages),
                "roles": [message.role for message in messages],
            },
        )
