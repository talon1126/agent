"""Adapt synchronous providers to the Phase I async query boundary.

The first async-query phase introduces coroutine contracts before every concrete
provider has a native async transport. These adapters let ``AsyncQueryRuntime``
depend on async methods while preserving the existing synchronous providers and
factory registrations. They intentionally wrap only online query operations;
ingestion upsert and batch processing remain synchronous in Phase I.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

from src.core.types import RetrievalResult
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse
from src.libs.reranker.base_reranker import BaseReranker
from src.libs.vector_store.base_vector_store import BaseVectorStore


async def _run_with_optional_timeout[T](
    coroutine: Awaitable[T],
    *,
    timeout_seconds: float | None,
    timeout_message: str,
) -> T:
    """Await a provider coroutine and convert timeout into a stable error.

    Args:
        coroutine: Awaitable returned by an async provider method.
        timeout_seconds: Optional positive timeout in seconds. ``None`` leaves
            the awaitable unbounded.
        timeout_message: Stable message used by tests, traces, and callers.

    Returns:
        The awaited provider result.

    Raises:
        TimeoutError: If the provider call exceeds ``timeout_seconds``.
        asyncio.CancelledError: If the caller cancels the task. Cancellation is
            deliberately preserved so collection-level timeout logic can stop
            waiting without being converted into a provider failure.
    """

    try:
        if timeout_seconds is None:
            return await coroutine
        return await asyncio.wait_for(coroutine, timeout=timeout_seconds)
    except TimeoutError as exc:
        raise TimeoutError(timeout_message) from exc


class SyncToAsyncLLMAdapter:
    """Expose async chat for a synchronous ``BaseLLM`` implementation.

    Args:
        provider: Existing synchronous LLM provider.
        timeout_seconds: Optional timeout applied to each async chat call.
    """

    def __init__(self, provider: BaseLLM, *, timeout_seconds: float | None = None) -> None:
        """Store the wrapped provider and timeout policy."""

        self._provider = provider
        self._timeout_seconds = timeout_seconds

    async def async_chat(self, messages: list[ChatMessage]) -> LLMResponse:
        """Generate a chat response through the wrapped synchronous provider."""

        return await _run_with_optional_timeout(
            self._provider.async_chat(messages),
            timeout_seconds=self._timeout_seconds,
            timeout_message="LLM async adapter timed out",
        )


class SyncToAsyncEmbeddingAdapter:
    """Expose async embedding calls for a synchronous ``BaseEmbedding``."""

    def __init__(
        self,
        provider: BaseEmbedding,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        """Store the wrapped provider and timeout policy."""

        self._provider = provider
        self._timeout_seconds = timeout_seconds

    async def async_embed(self, text: str) -> list[float]:
        """Embed one text through the wrapped synchronous provider."""

        return await _run_with_optional_timeout(
            self._provider.async_embed(text),
            timeout_seconds=self._timeout_seconds,
            timeout_message="Embedding async adapter timed out",
        )

    async def async_embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts through the wrapped synchronous provider."""

        return await _run_with_optional_timeout(
            self._provider.async_embed_batch(texts),
            timeout_seconds=self._timeout_seconds,
            timeout_message="Embedding batch async adapter timed out",
        )


class SyncToAsyncVectorStoreAdapter:
    """Expose async vector search for a synchronous ``BaseVectorStore``."""

    def __init__(
        self,
        provider: BaseVectorStore,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        """Store the wrapped provider and timeout policy."""

        self._provider = provider
        self._timeout_seconds = timeout_seconds

    async def async_search(
        self,
        vector: Sequence[float],
        *,
        filters: Mapping[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[RetrievalResult]:
        """Run dense vector search through the wrapped synchronous store."""

        return await _run_with_optional_timeout(
            self._provider.async_search(vector, filters=filters, top_k=top_k),
            timeout_seconds=self._timeout_seconds,
            timeout_message="Vector store async adapter timed out",
        )


class SyncToAsyncRerankerAdapter:
    """Expose async reranking for a synchronous ``BaseReranker``."""

    def __init__(
        self,
        provider: BaseReranker,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        """Store the wrapped provider and timeout policy."""

        self._provider = provider
        self._timeout_seconds = timeout_seconds

    async def async_rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Rerank candidates through the wrapped synchronous provider."""

        return await _run_with_optional_timeout(
            self._provider.async_rerank(query, candidates, top_k=top_k),
            timeout_seconds=self._timeout_seconds,
            timeout_message="Reranker async adapter timed out",
        )
