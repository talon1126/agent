"""Define provider-independent chat-model contracts for RAG components.

The LLM layer normalizes chat inputs and outputs before provider-specific
adapters are introduced. Ingestion transforms, query rewriting, reranking, and
image caption orchestration should depend on these local contracts rather than
OpenAI, Azure, Ollama, DeepSeek, or DashScope SDK response objects.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatModel(BaseModel):
    """Apply strict validation to local chat request/response contracts."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ChatMessage(ChatModel):
    """Represent one normalized chat message passed to an LLM provider.

    Attributes:
        role: Message role understood by OpenAI-compatible chat APIs.
        content: Non-empty message content after prompt rendering or user input
            normalization.
        name: Optional tool/user name reserved for future provider adapters.
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(min_length=1)
    name: str | None = None

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        """Require messages to contain useful text before provider calls.

        Args:
            value: Candidate message content.

        Returns:
            The original non-blank content.

        Raises:
            ValueError: If the message contains only whitespace.
        """

        if not value.strip():
            raise ValueError("ChatMessage content must not be blank")
        return value


class LLMResponse(ChatModel):
    """Represent one provider-independent LLM response.

    Attributes:
        content: Generated text returned to the caller.
        provider: Provider registry key that produced the response.
        model: Model identifier used by the provider implementation.
        raw: Trace-safe provider metadata. Secrets and full SDK objects must not
            be stored here.
    """

    content: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content", "provider", "model")
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        """Reject blank response fields that would break trace/debug output.

        Args:
            value: Candidate response field value.

        Returns:
            The original non-blank string.

        Raises:
            ValueError: If the value contains only whitespace.
        """

        if not value.strip():
            raise ValueError("LLMResponse string fields must not be blank")
        return value


class BaseLLM(ABC):
    """Provide the minimal unified chat interface for all LLM providers."""

    async def async_chat(self, messages: list[ChatMessage]) -> LLMResponse:
        """Generate one response without blocking the event-loop caller.

        Args:
            messages: Ordered messages already rendered by prompts or business
                logic. Implementations should not mutate this list.

        Returns:
            Provider-independent response text and trace-safe metadata.

        Raises:
            ProviderError: Implementations raise this for provider timeouts,
                invalid SDK responses, rate limits, or transport failures. The
                default compatibility path delegates to ``chat()`` in a worker
                thread until concrete providers add native async transports.
        """

        return await asyncio.to_thread(self.chat, messages)

    @abstractmethod
    def chat(self, messages: list[ChatMessage]) -> LLMResponse:
        """Generate one response from normalized chat messages.

        Args:
            messages: Ordered messages already rendered by prompts or business
                logic. Implementations should not mutate this list.

        Returns:
            Provider-independent response text and trace-safe metadata.

        Raises:
            ProviderError: Implementations raise this for provider timeouts,
                invalid SDK responses, rate limits, or transport failures.
        """
