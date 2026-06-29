"""Verify native async provider behavior introduced by Phase I2.

I2 moves online query providers toward coroutine-friendly execution. These
tests use small fake clients, pools, and scorers so the async contracts can be
validated without model APIs, PostgreSQL, or local Cross-Encoder weights.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from src.core.types import Chunk, RetrievalResult
from src.ingestion.embedding.bm25_indexer import BM25Indexer
from src.libs.embedding.openai_embedding import OpenAIEmbedding
from src.libs.llm import ChatMessage, LLMResponse
from src.libs.llm.openai_compatible_llm import OpenAICompatibleLLM
from src.libs.reranker import CrossEncoderReranker, LLMReranker
from src.libs.vector_store.pgvector_store import PgVectorStore
from src.storage.bm25_storage import BM25Storage


class _AsyncChatCompletions:
    """Fake async OpenAI chat-completions resource used by LLM tests."""

    def __init__(self) -> None:
        """Initialize the call log captured by assertions."""

        self.calls: list[dict[str, object]] = []

    async def create(self, *, model: str, messages: list[dict[str, str]]) -> object:
        """Return one OpenAI-shaped chat response."""

        self.calls.append({"model": model, "messages": messages})
        return SimpleNamespace(
            id="chat-1",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="native async response"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=3,
                completion_tokens=4,
                total_tokens=7,
            ),
        )


class _AsyncChatClient:
    """Expose the nested OpenAI-compatible async chat client shape."""

    def __init__(self) -> None:
        """Create the nested ``chat.completions`` resource."""

        self.chat = SimpleNamespace(completions=_AsyncChatCompletions())


class _AsyncEmbeddings:
    """Fake async OpenAI embeddings resource used by embedding tests."""

    def __init__(self) -> None:
        """Initialize the embedding call log."""

        self.calls: list[dict[str, object]] = []

    async def create(
        self,
        *,
        model: str,
        input: list[str],
        dimensions: int,
    ) -> object:
        """Return vectors intentionally out of order to prove reordering."""

        self.calls.append({"model": model, "input": list(input), "dimensions": dimensions})
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=[0.3, 0.4]),
                SimpleNamespace(index=0, embedding=[0.1, 0.2]),
            ]
        )


class _AsyncEmbeddingClient:
    """Expose the nested OpenAI-compatible async embedding client shape."""

    def __init__(self) -> None:
        """Create the embeddings resource."""

        self.embeddings = _AsyncEmbeddings()



class _SyncChatCompletions:
    """Fake synchronous OpenAI chat resource used by fallback tests."""

    def create(self, *, model: str, messages: list[dict[str, str]]) -> object:
        """Return one sync OpenAI-shaped chat response."""

        return SimpleNamespace(
            id="sync-chat-1",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=f"sync fallback {model}"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
            ),
        )


class _SyncChatClient:
    """Expose the nested synchronous OpenAI-compatible chat client shape."""

    def __init__(self) -> None:
        """Create the nested ``chat.completions`` resource."""

        self.chat = SimpleNamespace(completions=_SyncChatCompletions())


class _SyncEmbeddings:
    """Fake synchronous OpenAI embeddings resource used by fallback tests."""

    def create(
        self,
        *,
        model: str,
        input: list[str],
        dimensions: int,
    ) -> object:
        """Return one vector per input text."""

        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=[float(index), float(dimensions)])
                for index, _ in enumerate(input)
            ]
        )


class _SyncEmbeddingClient:
    """Expose the nested synchronous embedding client shape."""

    def __init__(self) -> None:
        """Create the embeddings resource."""

        self.embeddings = _SyncEmbeddings()

class _ConnectionManager:
    """Small synchronous context manager that mimics ``PostgresPool.connection``."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        """Store rows returned by ``fetchall``."""

        self._rows = rows

    def __enter__(self) -> _ConnectionManager:
        """Return the fake connection object."""

        return self

    def __exit__(self, *_: object) -> None:
        """Release the fake connection without side effects."""

    def execute(self, *_: object) -> _ConnectionManager:
        """Return self so callers can chain ``fetchall``."""

        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return configured rows."""

        return self._rows


class _Pool:
    """Fake pool exposing only the connection method used by query adapters."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        """Store rows for every fake connection."""

        self._rows = rows

    def connection(self) -> _ConnectionManager:
        """Return a new fake connection manager."""

        return _ConnectionManager(self._rows)


class _SlowScorer:
    """Cross-Encoder scorer that simulates local CPU/GPU inference latency."""

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Sleep briefly and return one score per pair."""

        time.sleep(0.02)
        return [float(index + 1) for index, _ in enumerate(pairs)]


class _AsyncRecordingLLM:
    """LLM fixture proving LLMReranker uses ``async_chat`` in async mode."""

    def __init__(self) -> None:
        """Initialize captured messages."""

        self.messages: list[ChatMessage] | None = None

    async def async_chat(self, messages: list[ChatMessage]) -> LLMResponse:
        """Return a deterministic rerank payload."""

        self.messages = list(messages)
        return LLMResponse(
            content='[{"candidate_id": "chunk-b", "score": 0.9}]',
            provider="fake-async",
            model="fake-reranker",
        )


def _candidate(chunk_id: str, text: str, *, score: float = 0.1) -> RetrievalResult:
    """Build a valid retrieval candidate for provider tests."""

    return RetrievalResult(chunk_id=chunk_id, text=text, score=score, metadata={})


@pytest.mark.asyncio
async def test_openai_compatible_llm_async_chat_uses_async_client() -> None:
    """Require OpenAI-compatible LLM providers to call async chat transport."""

    async_client = _AsyncChatClient()
    llm = OpenAICompatibleLLM(
        model="model-a",
        client=object(),
        async_client=async_client,
    )

    response = await llm.async_chat([ChatMessage(role="user", content="hello")])

    assert response.content == "native async response"
    assert response.provider == "openai_compatible"
    assert response.model == "model-a"
    assert async_client.chat.completions.calls == [
        {
            "model": "model-a",
            "messages": [{"role": "user", "content": "hello"}],
        }
    ]


@pytest.mark.asyncio
async def test_openai_embedding_async_batch_uses_async_client_and_preserves_order() -> None:
    """Require OpenAI-compatible embeddings to use async transport."""

    async_client = _AsyncEmbeddingClient()
    embedding = OpenAIEmbedding(
        model="embedding-a",
        dimensions=2,
        provider_name="dashscope",
        client=object(),
        async_client=async_client,
    )

    vectors = await embedding.async_embed_batch(["first", "second"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert async_client.embeddings.calls == [
        {
            "model": "embedding-a",
            "input": ["first", "second"],
            "dimensions": 2,
        }
    ]


@pytest.mark.asyncio
async def test_pgvector_store_async_search_returns_retrieval_results() -> None:
    """Require pgvector search to expose an async online query method."""

    store = PgVectorStore(
        pool=_Pool([("chunk-1", "content", 0.75, {"collection": "faq"})]),
        embedding_dimensions=2,
    )

    results = await store.async_search([0.1, 0.2], filters={"collection": "faq"}, top_k=1)

    assert [result.chunk_id for result in results] == ["chunk-1"]
    assert results[0].text == "content"
    assert results[0].score == 0.75
    assert results[0].metadata == {"collection": "faq"}


@pytest.mark.asyncio
async def test_bm25_query_paths_expose_async_methods() -> None:
    """Require both in-memory and PostgreSQL-backed BM25 paths to be awaitable."""

    indexer = BM25Indexer()
    indexer.index(
        [
            Chunk(
                id="chunk-1",
                text="微波炉 容量 功率",
                metadata={"document_id": "doc-1"},
                chunk_index=0,
                start_offset=0,
                end_offset=8,
            )
        ]
    )
    indexer_results = await indexer.async_query(["微波炉"], top_k=3)

    storage = BM25Storage(
        _Pool(
            [
                ("chunk-1", "微波炉", 2, 8, 1, 8.0, 1),
            ]
        )
    )
    storage_results = await storage.async_query(["微波炉"], top_k=3, collection="faq")

    assert [candidate.chunk_id for candidate in indexer_results] == ["chunk-1"]
    assert [candidate.chunk_id for candidate in storage_results] == ["chunk-1"]


@pytest.mark.asyncio
async def test_llm_reranker_async_rerank_uses_async_llm_client() -> None:
    """Require LLM reranking to call the async LLM contract in async mode."""

    llm = _AsyncRecordingLLM()
    reranker = LLMReranker(llm_client=llm)  # type: ignore[arg-type]

    results = await reranker.async_rerank(
        "query",
        [_candidate("chunk-a", "A"), _candidate("chunk-b", "B")],
    )

    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-a"]
    assert llm.messages is not None


@pytest.mark.asyncio
async def test_cross_encoder_async_rerank_runs_in_executor() -> None:
    """Require local Cross-Encoder scoring to avoid blocking the event loop."""

    reranker = CrossEncoderReranker(model="local-cross-encoder", scorer=_SlowScorer())
    task = asyncio.create_task(
        reranker.async_rerank(
            "query",
            [_candidate("chunk-a", "A"), _candidate("chunk-b", "B")],
        )
    )

    await asyncio.sleep(0)
    assert not task.done()

    results = await task
    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-a"]
@pytest.mark.asyncio
async def test_openai_providers_async_methods_fallback_to_sync_client_when_needed() -> None:
    """Require async provider methods to preserve sync-only test injection."""

    llm = OpenAICompatibleLLM(model="model-sync", client=_SyncChatClient())
    embedding = OpenAIEmbedding(
        model="embedding-sync",
        dimensions=2,
        provider_name="dashscope",
        client=_SyncEmbeddingClient(),
    )

    llm_response = await llm.async_chat([ChatMessage(role="user", content="hello")])
    vectors = await embedding.async_embed_batch(["first", "second"])

    assert llm_response.content == "sync fallback model-sync"
    assert vectors == [[0.0, 2.0], [1.0, 2.0]]
