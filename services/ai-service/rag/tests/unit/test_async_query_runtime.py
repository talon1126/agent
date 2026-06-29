"""Protect Phase I async provider compatibility contracts.

I1 introduces async call boundaries before the concrete query runtime is
rewritten. These tests pin the backwards-compatible behavior required for the
existing synchronous providers: base interfaces expose async methods, adapters
run sync implementations through ``asyncio.to_thread()``, and settings carry the
runtime limits used by later async orchestration tasks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

import pytest

from src.core.config import RagSettings
from src.core.query_engine.async_adapters import (
    SyncToAsyncEmbeddingAdapter,
    SyncToAsyncLLMAdapter,
    SyncToAsyncRerankerAdapter,
    SyncToAsyncVectorStoreAdapter,
)
from src.core.types import Chunk, RetrievalResult
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse
from src.libs.reranker.base_reranker import BaseReranker
from src.libs.vector_store.base_vector_store import BaseVectorStore
from tests.unit.test_config import load_settings_document


class RecordingLLM(BaseLLM):
    """Synchronous test LLM used to verify async adapter delegation."""

    def __init__(self) -> None:
        """Initialize call recording state for assertions."""

        self.calls: list[list[ChatMessage]] = []

    def chat(self, messages: list[ChatMessage]) -> LLMResponse:
        """Return a deterministic response and record the normalized input."""

        self.calls.append(list(messages))
        return LLMResponse(content="pong", provider="fake", model="fake-chat")


class RecordingEmbedding(BaseEmbedding):
    """Synchronous embedding provider used to verify async batch delegation."""

    def __init__(self) -> None:
        """Initialize call recording state for assertions."""

        self.single_calls: list[str] = []
        self.batch_calls: list[list[str]] = []

    def embed(self, text: str) -> list[float]:
        """Return a stable vector for one text input."""

        self.single_calls.append(text)
        return [float(len(text)), 1.0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return stable vectors while preserving input order."""

        self.batch_calls.append(list(texts))
        return [[float(index), float(len(text))] for index, text in enumerate(texts)]


class RecordingVectorStore(BaseVectorStore):
    """Synchronous vector store used to verify async search delegation."""

    def __init__(self) -> None:
        """Initialize call recording state for assertions."""

        self.search_calls: list[tuple[list[float], Mapping[str, object] | None, int]] = []

    def upsert(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> list[str]:
        """Return IDs for the supplied chunks.

        Upsert remains a synchronous ingestion concern in Phase I.
        """

        return [chunk.id for chunk in chunks]

    def search(
        self,
        vector: Sequence[float],
        *,
        filters: Mapping[str, object] | None = None,
        top_k: int = 10,
    ) -> list[RetrievalResult]:
        """Return one deterministic retrieval result and record inputs."""

        self.search_calls.append((list(vector), filters, top_k))
        return [
            RetrievalResult(
                chunk_id="chunk-1",
                text="retrieved text",
                score=0.9,
                metadata={"document_id": "doc-1"},
            )
        ]

    def get_by_ids(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        """Return no chunks because I1 only verifies vector search."""

        return []


class RecordingReranker(BaseReranker):
    """Synchronous reranker used to verify async rerank delegation."""

    def __init__(self) -> None:
        """Initialize call recording state for assertions."""

        self.calls: list[tuple[str, list[str], int | None]] = []

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Return candidates in reverse order to make delegation visible."""

        self.calls.append((query, [candidate.chunk_id for candidate in candidates], top_k))
        ranked = list(reversed(candidates))
        return ranked[:top_k] if top_k is not None else ranked


@pytest.mark.asyncio
async def test_base_provider_async_methods_delegate_to_sync_methods() -> None:
    """Protect the default async methods on base provider contracts.

    The first async phase cannot require every concrete provider to be rewritten
    at once. Base classes therefore provide async methods that execute existing
    synchronous implementations without changing return shapes.
    """

    llm = RecordingLLM()
    embedding = RecordingEmbedding()
    vector_store = RecordingVectorStore()
    reranker = RecordingReranker()
    candidates = [
        RetrievalResult(chunk_id="a", text="A", score=0.1, metadata={"document_id": "doc"}),
        RetrievalResult(chunk_id="b", text="B", score=0.2, metadata={"document_id": "doc"}),
    ]

    response = await llm.async_chat([ChatMessage(role="user", content="ping")])
    vector = await embedding.async_embed("hello")
    vectors = await embedding.async_embed_batch(["a", "bb"])
    results = await vector_store.async_search([0.1, 0.2], filters={"collection": "faq"}, top_k=3)
    reranked = await reranker.async_rerank("question", candidates, top_k=1)

    assert response.content == "pong"
    assert llm.calls[0][0].content == "ping"
    assert vector == [5.0, 1.0]
    assert vectors == [[0.0, 1.0], [1.0, 2.0]]
    assert vector_store.search_calls == [([0.1, 0.2], {"collection": "faq"}, 3)]
    assert [result.chunk_id for result in results] == ["chunk-1"]
    assert [result.chunk_id for result in reranked] == ["b"]


@pytest.mark.asyncio
async def test_sync_to_async_adapters_preserve_provider_contracts() -> None:
    """Protect explicit adapter objects used by AsyncQueryRuntime builders."""

    llm = SyncToAsyncLLMAdapter(RecordingLLM())
    embedding = SyncToAsyncEmbeddingAdapter(RecordingEmbedding())
    vector_store = SyncToAsyncVectorStoreAdapter(RecordingVectorStore())
    reranker = SyncToAsyncRerankerAdapter(RecordingReranker())
    candidates = [
        RetrievalResult(chunk_id="a", text="A", score=0.1, metadata={"document_id": "doc"}),
        RetrievalResult(chunk_id="b", text="B", score=0.2, metadata={"document_id": "doc"}),
    ]

    assert (await llm.async_chat([ChatMessage(role="user", content="ping")])).provider == "fake"
    assert await embedding.async_embed("abc") == [3.0, 1.0]
    assert await embedding.async_embed_batch(["x", "yy"]) == [[0.0, 1.0], [1.0, 2.0]]
    assert (await vector_store.async_search([1.0], top_k=1))[0].chunk_id == "chunk-1"
    assert [item.chunk_id for item in await reranker.async_rerank("q", candidates)] == ["b", "a"]


@pytest.mark.asyncio
async def test_sync_to_async_adapter_timeout_is_reported_without_hanging() -> None:
    """Protect timeout handling around blocking synchronous providers."""

    class SlowLLM(BaseLLM):
        """Provider whose sync call exceeds the adapter timeout."""

        def chat(self, messages: list[ChatMessage]) -> LLMResponse:
            """Sleep longer than the configured timeout."""

            import time

            time.sleep(0.05)
            return LLMResponse(content="late", provider="fake", model="fake-chat")

    adapter = SyncToAsyncLLMAdapter(SlowLLM(), timeout_seconds=0.001)

    with pytest.raises(TimeoutError, match="LLM async adapter timed out"):
        await adapter.async_chat([ChatMessage(role="user", content="ping")])


@pytest.mark.asyncio
async def test_sync_to_async_adapter_preserves_cancellation() -> None:
    """Protect cancellation semantics for later per-collection timeouts."""

    class BlockingEmbedding(BaseEmbedding):
        """Provider whose sync call blocks long enough to cancel the task."""

        def embed(self, text: str) -> list[float]:
            """Sleep long enough for the async caller to cancel."""

            import time

            time.sleep(0.2)
            return [1.0]

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            """Return deterministic vectors for completeness."""

            return [[1.0] for _ in texts]

    task = asyncio.create_task(
        SyncToAsyncEmbeddingAdapter(BlockingEmbedding()).async_embed("query")
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_async_settings_are_loaded_from_example_configuration() -> None:
    """Protect Phase I runtime knobs in the versioned settings template."""

    raw_settings = load_settings_document()
    settings = RagSettings.model_validate(raw_settings)

    assert raw_settings["retrieval"]["async_enabled"] is True
    assert raw_settings["retrieval"]["max_collection_concurrency"] == 3
    assert raw_settings["retrieval"]["per_collection_timeout_seconds"] == 60
    assert raw_settings["retrieval"]["final_judge_timeout_seconds"] == 90
    assert raw_settings["retrieval"]["response_timeout_seconds"] == 90
    assert raw_settings["evaluation"]["async_enabled"] is True
    assert raw_settings["evaluation"]["max_sample_concurrency"] == 2
    assert raw_settings["evaluation"]["max_metric_concurrency"] == 2
    assert settings.retrieval.async_enabled is True
    assert settings.retrieval.max_collection_concurrency == 3
    assert settings.evaluation.async_enabled is True
    assert settings.evaluation.max_metric_concurrency == 2
