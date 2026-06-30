"""Protect Phase I async provider compatibility contracts.

I1 introduces async call boundaries before the concrete query runtime is
rewritten. These tests pin the backwards-compatible behavior required for the
existing synchronous providers: base interfaces expose async methods, adapters
run sync implementations through ``asyncio.to_thread()``, and settings carry the
runtime limits used by later async orchestration tasks.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest

from src.core.config import RagSettings
from src.core.query_engine.async_adapters import (
    SyncToAsyncEmbeddingAdapter,
    SyncToAsyncLLMAdapter,
    SyncToAsyncRerankerAdapter,
    SyncToAsyncVectorStoreAdapter,
)
from src.core.query_engine.self_rag_controller import SelfRagDecision
from src.core.types import Chunk, RetrievalResult
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse
from src.libs.reranker.base_reranker import BaseReranker
from src.libs.vector_store.base_vector_store import BaseVectorStore
from src.scripts import query as query_module
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

class _AsyncHybridReport:
    """Simple hybrid search result fixture used by async runtime tests."""

    def __init__(self, candidates: list[RetrievalResult]) -> None:
        """Create route snapshots from one candidate list."""

        self.dense_results = [candidates[0]] if candidates else []
        self.sparse_results = [candidates[-1]] if candidates else []
        self.fused_results = list(candidates)
        self.results = list(candidates)
        self.fallback_used = False


@pytest.mark.asyncio
async def test_async_query_runtime_executes_single_collection_pipeline_and_trace() -> None:
    """Require I3 async runtime to preserve sync trace and response contracts."""

    from src.core.query_engine.async_runtime import AsyncQueryRuntime
    from src.core.query_engine.intent_router import IntentRoute
    from src.core.query_engine.query_processor import ProcessedQuery
    from src.core.query_engine.reranker import RerankOutcome
    from src.core.response import KnowledgeHubResponse

    processed = ProcessedQuery(
        raw_query="无线耳机怎么选",
        normalized_query="无线耳机怎么选",
        keywords=("无线耳机", "怎么选"),
        collection="shopping_guides",
        top_k=2,
    )
    candidates = [
        RetrievalResult(chunk_id="chunk-a", text="A", score=0.4, metadata={"document_id": "doc"}),
        RetrievalResult(chunk_id="chunk-b", text="B", score=0.3, metadata={"document_id": "doc"}),
    ]
    reranked = [candidates[1].model_copy(update={"score": 0.9}, deep=True)]
    selected = [reranked[0].model_copy(deep=True)]
    query_processor = Mock()
    query_processor.process.return_value = processed
    intent_router = Mock()
    intent_router.route.return_value = IntentRoute(
        collection="faq",
        collections=("faq",),
        domain_intent="faq",
        complexity="simple",
        retrieval_strategy="hybrid",
        confidence=0.91,
        method="rules",
        provider="IntentRouter",
        reason="matched faq",
    )
    hybrid_search = Mock()
    hybrid_search.search.return_value = _AsyncHybridReport(candidates)
    rerank_controller = Mock()
    rerank_controller.rerank_with_outcome.return_value = RerankOutcome(
        results=reranked,
        fallback_used=False,
        fallback_reason=None,
    )
    self_rag_controller = Mock()
    self_rag_controller.evaluate.return_value = SelfRagDecision(
        decision="accepted",
        score_band="medium_confidence",
        selected_results=selected,
        fallback_action=None,
        judge_result=None,
        reason="judge_passed",
    )
    response = KnowledgeHubResponse(
        content="[1] B",
        citations=(),
        images=(),
        trace_id="query-async-1",
        is_empty=False,
    )
    response_builder = Mock()
    response_builder.build.return_value = response
    traces: list[dict[str, object]] = []
    runtime = AsyncQueryRuntime(
        query_processor=query_processor,
        intent_router=intent_router,
        hybrid_search=hybrid_search,
        rerank_controller=rerank_controller,
        self_rag_controller=self_rag_controller,
        response_builder=response_builder,
        trace_sink=traces.append,
    )

    execution = await runtime.execute(
        "无线耳机怎么选",
        collection="shopping_guides",
        top_k=1,
        no_rerank=False,
        trace_id="query-async-1",
    )

    query_processor.process.assert_called_once_with(
        "无线耳机怎么选",
        collection="shopping_guides",
        top_k=1,
    )
    hybrid_search.search.assert_called_once_with(
        execution.processed_query,
        filters={"collection": "faq"},
        trace_context=ANY,
    )
    rerank_controller.rerank_with_outcome.assert_called_once_with(
        processed.normalized_query,
        candidates,
        top_k=1,
        trace_context=ANY,
    )
    self_rag_controller.evaluate.assert_called_once()
    assert self_rag_controller.evaluate.call_args.args[0] == processed.normalized_query
    assert [candidate.chunk_id for candidate in self_rag_controller.evaluate.call_args.args[1]] == [
        "chunk-b"
    ]
    response_builder.build.assert_called_once_with(
        selected,
        trace_id="query-async-1",
        query=processed.normalized_query,
    )
    assert execution.response is response
    assert execution.final_results == tuple(selected)
    assert execution.rerank_applied is True
    assert traces[0]["status"] == "success"
    assert traces[0]["collection"] == "faq"
    assert [stage["stage"] for stage in traces[0]["stages"]][:2] == [
        "query_processing",
        "intent_routing",
    ]
    assert traces[0]["query_result"]["contexts"] == [
        {"chunk_id": "chunk-b", "score": 0.9, "rank": 1}
    ]



@pytest.mark.asyncio
async def test_async_query_runtime_merges_collections_before_single_self_rag_and_response() -> None:
    """Require I4 runtime to run multi-collection retrieval before final gates."""

    from src.core.query_engine.async_runtime import AsyncQueryRuntime
    from src.core.query_engine.intent_router import IntentRoute
    from src.core.query_engine.query_processor import ProcessedQuery
    from src.core.response import KnowledgeHubResponse

    processed = ProcessedQuery(
        raw_query="退货和发货规则",
        normalized_query="退货和发货规则",
        keywords=("退货", "发货"),
        collection="shopping_guides",
        top_k=2,
    )
    route = IntentRoute(
        collection="policies",
        collections=("policies", "faq"),
        domain_intent="support_policy",
        complexity="medium",
        retrieval_strategy="hybrid",
        confidence=0.82,
        method="rules",
        provider="IntentRouter",
        reason="multi collection",
    )
    policy = RetrievalResult(
        chunk_id="policy-a",
        text="policy text",
        score=0.7,
        metadata={"collection": "policies", "document_id": "doc-policy"},
    )
    faq = RetrievalResult(
        chunk_id="faq-a",
        text="faq text",
        score=0.6,
        metadata={"collection": "faq", "document_id": "doc-faq"},
    )
    query_processor = Mock()
    query_processor.process.return_value = processed
    intent_router = Mock()
    intent_router.route.return_value = route
    hybrid_search = Mock()

    async def _collection_runner(
        query, *, collection, top_k, no_rerank, routing_score, routing_reason
    ):
        from src.core.query_engine.parallel_retrieval import AsyncCollectionRetrievalResult

        result = policy if collection == "policies" else faq
        return AsyncCollectionRetrievalResult(
            collection=collection,
            results=[result],
            dense_results=[result],
            sparse_results=[],
            fused_results=[result],
            filtered_results=[result],
            rerank_results=[result],
            stages=_collection_stage_fixture(result),
            fallback_used=False,
            rerank_applied=True,
            duration_ms=1.0,
        )

    self_rag_controller = Mock()
    self_rag_controller.evaluate.return_value = SelfRagDecision(
        decision="accepted",
        score_band="medium_confidence",
        selected_results=[policy, faq],
        fallback_action=None,
        judge_result=None,
        reason="judge_passed",
    )
    response = KnowledgeHubResponse(
        content="[1] policy\n[2] faq",
        citations=(),
        images=(),
        trace_id="query-async-multi",
        is_empty=False,
    )
    response_builder = Mock()
    response_builder.build.return_value = response
    traces: list[dict[str, object]] = []
    runtime = AsyncQueryRuntime(
        query_processor=query_processor,
        intent_router=intent_router,
        hybrid_search=hybrid_search,
        rerank_controller=Mock(),
        self_rag_controller=self_rag_controller,
        response_builder=response_builder,
        collection_runner=_collection_runner,
        max_collection_concurrency=2,
        per_collection_timeout_seconds=1,
        trace_sink=traces.append,
    )

    execution = await runtime.execute(
        "退货和发货规则",
        collection="shopping_guides",
        top_k=2,
        no_rerank=False,
        trace_id="query-async-multi",
    )

    hybrid_search.search.assert_not_called()
    self_rag_controller.evaluate.assert_called_once()
    assert self_rag_controller.evaluate.call_args.args[0] == processed.normalized_query
    assert [candidate.chunk_id for candidate in self_rag_controller.evaluate.call_args.args[1]] == [
        "policy-a",
        "faq-a",
    ]
    response_builder.build.assert_called_once_with(
        [policy, faq],
        trace_id="query-async-multi",
        query=processed.normalized_query,
    )
    assert [result.chunk_id for result in execution.final_results] == ["policy-a", "faq-a"]
    assert execution.fallback_used is False
    stage_by_name = {stage["stage"]: stage for stage in traces[0]["stages"]}
    assert stage_by_name["fusion"]["details"]["selected_collections"] == ["policies", "faq"]
    assert traces[0]["summary_metrics"]["candidate_count_by_stage"]["self_rag"] == 2
    assert traces[0]["query_result"]["contexts"] == [
        {"chunk_id": "policy-a", "score": 0.7, "rank": 1},
        {"chunk_id": "faq-a", "score": 0.6, "rank": 2},
    ]


@pytest.mark.asyncio
async def test_async_multi_collection_trace_preserves_query_stage_semantics() -> None:
    """Require async multi-collection traces to keep normal query stage names."""

    from src.core.query_engine.async_runtime import AsyncQueryRuntime
    from src.core.query_engine.intent_router import IntentRoute
    from src.core.query_engine.query_processor import ProcessedQuery
    from src.core.response import KnowledgeHubResponse

    processed = ProcessedQuery(
        raw_query="配送和退货规则",
        normalized_query="配送和退货规则",
        keywords=("配送", "退货"),
        collection="policies",
        top_k=2,
    )
    route = IntentRoute(
        collection="policies",
        collections=("policies", "faq"),
        domain_intent="support_policy",
        complexity="medium",
        retrieval_strategy="hybrid",
        confidence=0.82,
        method="rules",
        provider="IntentRouter",
        reason="multi collection",
    )
    policy = RetrievalResult(
        chunk_id="policy-a",
        text="policy text",
        score=0.8,
        metadata={"collection": "policies", "document_id": "doc-policy"},
    )
    faq = RetrievalResult(
        chunk_id="faq-a",
        text="faq text",
        score=0.7,
        metadata={"collection": "faq", "document_id": "doc-faq"},
    )
    query_processor = Mock()
    query_processor.process.return_value = processed
    intent_router = Mock()
    intent_router.route.return_value = route

    async def _collection_runner(
        query, *, collection, top_k, no_rerank, routing_score, routing_reason
    ):
        from src.core.query_engine.parallel_retrieval import AsyncCollectionRetrievalResult

        result = policy if collection == "policies" else faq
        return AsyncCollectionRetrievalResult(
            collection=collection,
            results=[result],
            dense_results=[result],
            sparse_results=[result],
            fused_results=[result],
            filtered_results=[result],
            rerank_results=[result],
            stages=_collection_stage_fixture(result),
            fallback_used=False,
            rerank_applied=True,
            duration_ms=1.0,
        )

    class _RecordingSelfRagController:
        """Small fake that records the self_rag stage like production does."""

        def evaluate(self, query, candidates, *, trace_context=None):
            decision = SelfRagDecision(
                decision="accepted",
                score_band="medium_confidence",
                selected_results=[policy, faq],
                fallback_action=None,
                judge_result=None,
                reason="judge_passed",
            )
            if trace_context is not None:
                trace_context.record_stage(
                    stage="self_rag",
                    method="score_gate_or_llm_judge",
                    provider="SelfRagController",
                    duration_ms=1.0,
                    candidate_count=len(decision.selected_results),
                    status="success",
                    details={
                        "selected_chunk_ids": [
                            item.chunk_id for item in decision.selected_results
                        ]
                    },
                )
            return decision

    self_rag_controller = _RecordingSelfRagController()
    response_builder = Mock()
    response_builder.build.return_value = KnowledgeHubResponse(
        content="[1] policy\n[2] faq",
        citations=(),
        images=(),
        trace_id="query-async-stage-semantics",
        is_empty=False,
    )
    traces: list[dict[str, object]] = []
    runtime = AsyncQueryRuntime(
        query_processor=query_processor,
        intent_router=intent_router,
        hybrid_search=Mock(),
        rerank_controller=Mock(),
        self_rag_controller=self_rag_controller,
        response_builder=response_builder,
        collection_runner=_collection_runner,
        max_collection_concurrency=2,
        per_collection_timeout_seconds=1,
        trace_sink=traces.append,
    )

    await runtime.execute(
        "配送和退货规则",
        collection="policies",
        top_k=2,
        no_rerank=False,
        trace_id="query-async-stage-semantics",
    )

    stages = traces[0]["stages"]
    stage_names = [stage["stage"] for stage in stages]
    assert stage_names == [
        "query_processing",
        "intent_routing",
        "dense",
        "sparse",
        "fusion",
        "filter",
        "rerank",
        "self_rag",
        "response",
    ]
    for stage_name in ("dense", "sparse", "fusion", "filter", "rerank"):
        stage = next(stage for stage in stages if stage["stage"] == stage_name)
        assert stage["details"]["collection_runs"][0]["collection"] == "policies"
        assert stage["details"]["collection_runs"][1]["collection"] == "faq"
    assert sum(1 for stage in stages if stage["stage"] == "fusion") == 1


def _collection_stage_fixture(result: RetrievalResult) -> list[dict[str, object]]:
    """Build compact collection stage rows for async trace tests."""

    snapshot = [{"rank": 1, "chunk_id": result.chunk_id, "score": result.score}]
    return [
        {
            "stage": "dense",
            "duration_ms": 1.0,
            "candidate_count": 1,
            "status": "success",
            "details": {"chunk_ids": [result.chunk_id]},
        },
        {
            "stage": "sparse",
            "duration_ms": 1.0,
            "candidate_count": 1,
            "status": "success",
            "details": {"chunk_ids": [result.chunk_id]},
        },
        {
            "stage": "fusion",
            "duration_ms": 1.0,
            "candidate_count": 1,
            "status": "success",
            "details": {"fused_candidates": snapshot},
        },
        {
            "stage": "filter",
            "duration_ms": 1.0,
            "candidate_count": 1,
            "status": "success",
            "details": {
                "before_candidates": snapshot,
                "after_candidates": snapshot,
            },
        },
        {
            "stage": "rerank",
            "duration_ms": 1.0,
            "candidate_count": 1,
            "status": "success",
            "details": {
                "before_candidates": snapshot,
                "after_candidates": snapshot,
            },
        },
    ]
def test_run_query_cli_selects_async_runtime_from_settings() -> None:
    """Require CLI to use async runtime when retrieval.async_enabled is true."""

    from src.core.query_engine.intent_router import IntentRoute
    from src.core.query_engine.query_processor import ProcessedQuery
    from src.core.response import KnowledgeHubResponse

    processed = ProcessedQuery(
        raw_query="售后政策",
        normalized_query="售后政策",
        keywords=("售后", "政策"),
        collection="policies",
        top_k=1,
    )
    response = KnowledgeHubResponse(
        content="[1] policy",
        citations=(),
        images=(),
        trace_id="query-cli-async",
        is_empty=False,
    )
    execution = query_module.QueryExecutionResult(
        processed_query=processed,
        intent_route=IntentRoute(
            collection="policies",
            collections=("policies",),
            domain_intent="policy",
            complexity="simple",
            retrieval_strategy="hybrid",
            confidence=0.9,
            method="rules",
            reason="test",
        ),
        dense_results=(),
        sparse_results=(),
        fused_results=(),
        filtered_results=(),
        final_results=(),
        self_rag_decision=None,
        response=response,
        rerank_applied=False,
        fallback_used=False,
    )

    class _AsyncRuntime:
        """Fake async runtime returned by the CLI builder."""

        async def execute(self, *_: object, **__: object) -> object:
            """Return the prepared execution result."""

            return execution

    settings = SimpleNamespace(
        database=SimpleNamespace(),
        retrieval=SimpleNamespace(
            async_enabled=True,
            filters=SimpleNamespace(default_collection="policies"),
        ),
    )
    pool = Mock()
    output: list[str] = []

    exit_code = query_module.run_query_cli(
        ["--query", "售后政策", "--top-k", "1"],
        settings_loader=lambda: settings,
        pool_factory=lambda _: pool,
        schema_initializer=lambda _: None,
        runtime_builder=lambda *_: _AsyncRuntime(),
        trace_id_factory=lambda: "query-cli-async",
        output=output.append,
    )

    assert exit_code == 0
    assert json.loads(output[0])["response"] == response.model_dump(mode="json")


def test_select_runtime_builder_uses_async_runtime_when_enabled(monkeypatch) -> None:
    """Require the default CLI runtime selector to honor retrieval.async_enabled."""

    settings = SimpleNamespace(retrieval=SimpleNamespace(async_enabled=True))
    pool = Mock()
    async_runtime = Mock()
    builder = Mock(return_value=async_runtime)
    monkeypatch.setattr(query_module, "build_async_query_runtime", builder)

    selected = query_module._select_runtime_builder(settings, pool, no_rerank=True)

    assert selected is async_runtime
    builder.assert_called_once_with(settings, pool, True)


def test_async_query_performance_report_summarizes_latency_metrics_and_regressions() -> None:
    """Require I6 reporting to explain first10/last10 async query performance."""

    from src.scripts.run_evaluation import async_query_performance_report

    report = async_query_performance_report(
        [
            {
                "dataset": "first10",
                "mode": "sync",
                "query_latencies_ms": [1200, 1400, 1600],
                "rag_trace_count": 6,
                "self_rag_judge_count": 4,
                "response_builder_count": 6,
                "timeout_count": 0,
                "metrics": {"faithfulness": 0.52, "context_recall": 0.61},
            },
            {
                "dataset": "first10",
                "mode": "async",
                "query_latencies_ms": [700, 900, 1000],
                "rag_trace_count": 3,
                "self_rag_judge_count": 1,
                "response_builder_count": 3,
                "timeout_count": 0,
                "metrics": {"faithfulness": 0.58, "context_recall": 0.67},
            },
            {
                "dataset": "last10",
                "mode": "sync",
                "query_latencies_ms": [2000, 2400, 2800],
                "rag_trace_count": 8,
                "self_rag_judge_count": 5,
                "response_builder_count": 8,
                "timeout_count": 0,
                "metrics": {"faithfulness": 0.62, "answer_relevancy": 0.91},
            },
            {
                "dataset": "last10",
                "mode": "async",
                "query_latencies_ms": [2100, 2600, 3100],
                "rag_trace_count": 6,
                "self_rag_judge_count": 2,
                "response_builder_count": 6,
                "timeout_count": 1,
                "metrics": {"faithfulness": 0.59, "answer_relevancy": 0.9},
            },
        ]
    )

    first10 = report["datasets"]["first10"]
    assert first10["sync"]["avg_latency_ms"] == 1400.0
    assert first10["async"]["p95_latency_ms"] == 1000.0
    assert first10["delta"]["avg_latency_improvement_pct"] == pytest.approx(38.09, abs=0.01)
    assert first10["delta"]["rag_trace_count"] == -3
    assert first10["delta"]["self_rag_judge_count"] == -3
    assert first10["delta"]["metrics"]["faithfulness"]["delta"] == pytest.approx(0.06)
    last10 = report["datasets"]["last10"]
    assert last10["delta"]["timeout_count"] == 1
    assert any("last10" in item and "timeout" in item for item in report["recommendations"])
    assert any("faithfulness" in item for item in report["recommendations"])
    assert "first10" in report["markdown"]
    assert "P95" in report["markdown"]
