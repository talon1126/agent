"""Protect query preprocessing contracts used by every retrieval route.

D1 establishes the stable ``ProcessedQuery`` object consumed by Dense, Sparse,
Hybrid, trace, and local CLI components. These tests define normalization,
keyword extraction, settings defaults, caller overrides, and optional rewrite
fallback without invoking an external LLM.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, Mock

import psycopg
import pytest
from pydantic import ValidationError

from src.core.errors import DatabaseError, RetrievalError
from src.core.query_engine.dense_route import DenseRoute
from src.core.query_engine.fusion import reciprocal_rank_fusion
from src.core.query_engine.hybrid_engine import CandidateFilter, HybridSearch
from src.core.query_engine.parallel_retrieval import (
    AsyncCollectionRetrievalResult,
    AsyncParallelRetrievalController,
    ParallelRetrievalResult,
)
from src.core.query_engine.query_processor import (
    ProcessedQuery,
    QueryProcessor,
)
from src.core.query_engine.reranker import RerankOutcome
from src.core.query_engine.self_rag_controller import (
    SelfRagController,
    SelfRagDecision,
    SelfRagJudgeResult,
)
from src.core.query_engine.sparse_route import SparseRoute
from src.core.response import KnowledgeHubResponse
from src.core.types import Chunk, RetrievalResult
from src.libs.llm import ChatMessage, LLMResponse
from src.scripts import query as query_module
from src.storage.bm25_storage import BM25Storage


def _settings(*, rewrite_enabled: bool = True) -> SimpleNamespace:
    """Build the minimal settings shape consumed by ``QueryProcessor``."""

    return SimpleNamespace(
        retrieval=SimpleNamespace(
            query_rewrite_enabled=rewrite_enabled,
            dense_top_k=30,
            sparse_top_k=25,
            final_top_k=5,
            filters=SimpleNamespace(default_collection="shopping_guides"),
        ),
        response=SimpleNamespace(
            evidence_context_optimizer=SimpleNamespace(
                enabled=False,
                llm_provider="deepseek",
                prompt_path="config/prompts/evidence_context_optimizer.md",
                fallback_to_raw=True,
            )
        ),
    )


def _chunk(
    chunk_id: str,
    text: str,
    *,
    metadata: dict[str, object] | None = None,
) -> Chunk:
    """Build a valid chunk fixture for retrieval route hydration tests."""

    return Chunk(
        id=chunk_id,
        text=text,
        metadata={"document_id": "doc-1", **dict(metadata or {})},
        chunk_index=0,
        start_offset=0,
        end_offset=max(len(text), 1),
    )


def _result(
    chunk_id: str,
    *,
    score: float,
    text: str | None = None,
    metadata: dict[str, object] | None = None,
) -> RetrievalResult:
    """Build a valid retrieval result fixture for route and fusion tests."""

    return RetrievalResult(
        chunk_id=chunk_id,
        text=text or f"{chunk_id} text",
        score=score,
        metadata={
            "collection": "shopping_guides",
            "doc_type": "guide",
            "source_type": "markdown",
            "document_status": "published",
            "lifecycle_status": "success",
            "permissions": ["public"],
            **dict(metadata or {}),
        },
    )



class FakeParallelRuntime:
    """Serve deterministic per-collection retrieval results to D3 tests."""

    def __init__(
        self,
        results_by_collection: dict[str, list[RetrievalResult]],
    ) -> None:
        """Store collection fixtures and initialize the call log.

        Args:
            results_by_collection: Mapping from collection ID to the final
                retrieval results that a single-collection runtime would return.
        """

        self.results_by_collection = results_by_collection
        self.calls: list[dict[str, object]] = []

    def execute_collection(
        self,
        *,
        query: ProcessedQuery,
        collection: str,
        top_k: int,
        no_rerank: bool,
        trace_id: str,
    ) -> list[RetrievalResult]:
        """Return configured collection results and record execution inputs."""

        self.calls.append(
            {
                "query": query.normalized_query,
                "collection": collection,
                "top_k": top_k,
                "no_rerank": no_rerank,
                "trace_id": trace_id,
            }
        )
        if collection == "broken":
            raise RetrievalError("collection failed")
        return list(self.results_by_collection.get(collection, []))


class FakeParallelTrace:
    """Collect parallel retrieval trace stages for assertions."""

    def __init__(self) -> None:
        """Initialize an empty trace stage list."""

        self.stages: list[dict[str, object]] = []

    def record_stage(self, **payload: object) -> None:
        """Append one trace stage payload."""

        self.stages.append(payload)


class _FakeJudgeLLM:
    """Capture Self-RAG judge prompts and return one configured response."""

    def __init__(self, content: str) -> None:
        """Store the response payload returned by ``chat()``.

        Args:
            content: Strict JSON, invalid JSON, or any text fixture used by a
                controller test to exercise parsing and fallback behavior.
        """

        self.content = content
        self.messages: list[list[ChatMessage]] = []

    def chat(self, messages: list[ChatMessage]) -> LLMResponse:
        """Return a deterministic judge response while preserving messages."""

        self.messages.append(list(messages))
        return LLMResponse(
            content=self.content,
            provider="fake-judge",
            model="fake-self-rag",
        )


class _FailingTraceSink:
    """Trace sink fixture that proves Self-RAG decisions survive trace errors."""

    def record_stage(self, **_: object) -> None:
        """Raise on every trace write attempt."""

        raise RuntimeError("trace sink unavailable")


def _self_rag_settings(**overrides: object) -> SimpleNamespace:
    """Build a minimal settings object consumed by ``SelfRagController``."""

    values = {
        "enabled": True,
        "high_confidence_top_n": 3,
        "high_confidence_min_score": 0.75,
        "medium_confidence_min_top_score": 0.35,
        "judge_min_candidate_score": 0.15,
        "relevance_threshold": 0.7,
        "evidence_sufficiency_threshold": 0.7,
        "fallback_action": "empty",
        "judge_llm_provider": "deepseek",
        "judge_prompt_path": "config/prompts/self_rag_judge_prompt.yaml",
    }
    values.update(overrides)
    return SimpleNamespace(self_rag=SimpleNamespace(**values))

def _processed_query() -> ProcessedQuery:
    """Build a reusable processed query fixture for hybrid retrieval tests."""

    return ProcessedQuery(
        raw_query="无线耳机推荐",
        normalized_query="无线耳机推荐",
        keywords=("无线耳机", "推荐"),
        collection="shopping_guides",
        top_k=5,
    )


def test_query_processor_normalizes_unicode_whitespace_and_punctuation() -> None:
    """Require equivalent full-width and irregularly spaced input to stabilize."""

    processor = QueryProcessor(settings=_settings(rewrite_enabled=False))

    result = processor.process("  如何\t挑选\n高性价比　无线耳机？  ")

    assert isinstance(result, ProcessedQuery)
    assert result.raw_query == "  如何\t挑选\n高性价比　无线耳机？  "
    assert result.normalized_query == "如何 挑选 高性价比 无线耳机?"
    assert result.rewrite_applied is False
    assert result.rewrite_fallback_reason is None


@pytest.mark.parametrize("query", ["", "   ", "\n\t　"])
def test_query_processor_rejects_blank_queries(query: str) -> None:
    """Require blank user input to fail before any retrieval provider is called."""

    processor = QueryProcessor(settings=_settings())

    with pytest.raises(RetrievalError, match="Query must not be blank"):
        processor.process(query)


def test_query_processor_applies_settings_defaults_and_validates_overrides() -> None:
    """Require collection and top-k values to come from settings unless overridden."""

    processor = QueryProcessor(settings=_settings(rewrite_enabled=False))

    default_result = processor.process("无线耳机选购建议")
    overridden_result = processor.process(
        "无线耳机选购建议",
        collection=" premium_guides ",
        top_k=8,
    )

    assert default_result.collection == "shopping_guides"
    assert default_result.top_k == 5
    assert overridden_result.collection == "premium_guides"
    assert overridden_result.top_k == 8

    with pytest.raises(RetrievalError, match="Collection must not be blank"):
        processor.process("无线耳机", collection=" ")
    with pytest.raises(RetrievalError, match="Collection must be a string"):
        processor.process("无线耳机", collection=123)  # type: ignore[arg-type]
    with pytest.raises(RetrievalError, match="top_k must be greater than zero"):
        processor.process("无线耳机", top_k=0)


def test_processed_query_excludes_business_intent_fields() -> None:
    """Query preprocessing must not own business routing decisions."""

    result = QueryProcessor(settings=_settings(rewrite_enabled=False)).process(
        "推荐一款高性价比无线耳机并给我商品链接"
    )

    dumped = result.model_dump()
    assert "intent" not in dumped
    assert "requires_product_tool" not in dumped
    with pytest.raises(ValidationError):
        ProcessedQuery(
            raw_query="q",
            normalized_query="q",
            keywords=(),
            collection="shopping_guides",
            top_k=3,
            intent="recommendation",
        )

def test_query_processor_extracts_ordered_unique_keywords() -> None:
    """Require Sparse Route keywords to remove question filler and duplicates."""

    processor = QueryProcessor(settings=_settings(rewrite_enabled=False))

    result = processor.process("如何挑选高性价比无线耳机，无线耳机需要关注什么？")

    assert result.keywords == ("挑选", "高性价比", "无线耳机", "关注")


def test_processed_query_prevents_downstream_mutation() -> None:
    """Require route consumers to observe one stable query and keyword snapshot."""

    result = QueryProcessor(settings=_settings(rewrite_enabled=False)).process(
        "推荐高性价比无线耳机"
    )

    with pytest.raises(ValidationError):
        result.normalized_query = "mutated"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.keywords.append("mutated")  # type: ignore[attr-defined]


def test_query_processor_applies_optional_rewrite_before_keyword_extraction() -> None:
    """Require a successful rewrite to become the canonical retrieval query."""

    rewriter = Mock()
    rewriter.rewrite.return_value = "通勤场景 高性价比 主动降噪 无线耳机"
    processor = QueryProcessor(settings=_settings(), rewriter=rewriter)

    result = processor.process("地铁上用的耳机怎么选")

    rewriter.rewrite.assert_called_once_with("地铁上用的耳机怎么选")
    assert result.normalized_query == "通勤场景 高性价比 主动降噪 无线耳机"
    assert result.keywords == ("通勤场景", "高性价比", "主动降噪", "无线耳机")
    assert result.rewrite_applied is True
    assert result.rewrite_fallback_reason is None


@pytest.mark.parametrize(
    ("side_effect", "response", "expected_reason"),
    [
        (RuntimeError("provider unavailable"), None, "rewriter_error"),
        (None, "   ", "blank_rewrite"),
    ],
)
def test_query_processor_falls_back_when_rewrite_is_unavailable(
    side_effect: Exception | None,
    response: str | None,
    expected_reason: str,
) -> None:
    """Require rewrite failures to preserve a usable normalized original query."""

    rewriter = Mock()
    rewriter.rewrite.side_effect = side_effect
    rewriter.rewrite.return_value = response
    processor = QueryProcessor(settings=_settings(), rewriter=rewriter)

    result = processor.process("无线耳机怎么选")

    assert result.normalized_query == "无线耳机怎么选"
    assert result.rewrite_applied is False
    assert result.rewrite_fallback_reason == expected_reason


def test_query_processor_does_not_call_rewriter_when_disabled() -> None:
    """Require the settings switch to bypass rewrite without reporting a failure."""

    rewriter = Mock()
    processor = QueryProcessor(
        settings=_settings(rewrite_enabled=False),
        rewriter=rewriter,
    )

    result = processor.process("无线耳机怎么选")

    rewriter.rewrite.assert_not_called()
    assert result.rewrite_applied is False
    assert result.rewrite_fallback_reason is None


def test_dense_route_embeds_processed_query_and_returns_store_results() -> None:
    """Require Dense Route to preserve the existing embedding/store contracts."""

    settings = _settings(rewrite_enabled=False)
    processor = QueryProcessor(settings=settings)
    processed = processor.process("无线耳机主动降噪怎么选")
    embedding = Mock()
    embedding.embed.return_value = [0.1, 0.2, 0.3]
    expected = [
        {
            "chunk_id": "chunk-1",
            "text": "通勤时优先关注低频主动降噪。",
            "score": 0.91,
            "metadata": {"collection": "shopping_guides"},
        }
    ]
    vector_store = Mock()
    vector_store.search.return_value = expected
    route = DenseRoute(
        settings=settings,
        query_processor=processor,
        embedding=embedding,
        vector_store=vector_store,
    )

    results = route.search(processed)

    embedding.embed.assert_called_once_with(processed.normalized_query)
    vector_store.search.assert_called_once_with([0.1, 0.2, 0.3], top_k=30)
    assert [result.model_dump() for result in results] == expected


def test_dense_route_processes_raw_query_and_allows_top_k_override() -> None:
    """Require raw strings to pass through QueryProcessor before Dense retrieval."""

    settings = _settings(rewrite_enabled=False)
    processor = Mock()
    processor.process.return_value = ProcessedQuery(
        raw_query="耳机推荐",
        normalized_query="耳机推荐",
        keywords=("耳机", "推荐"),
        collection="shopping_guides",
        top_k=5,
    )
    embedding = Mock()
    embedding.embed.return_value = [1.0, 0.0]
    vector_store = Mock()
    vector_store.search.return_value = []
    route = DenseRoute(
        settings=settings,
        query_processor=processor,
        embedding=embedding,
        vector_store=vector_store,
    )

    assert route.search("耳机推荐", top_k=7) == []

    processor.process.assert_called_once_with("耳机推荐")
    vector_store.search.assert_called_once_with([1.0, 0.0], top_k=7)


def test_dense_route_rejects_invalid_candidate_limit_before_provider_calls() -> None:
    """Require invalid route limits to fail without embedding or storage access."""

    settings = _settings(rewrite_enabled=False)
    embedding = Mock()
    vector_store = Mock()
    route = DenseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        embedding=embedding,
        vector_store=vector_store,
    )

    with pytest.raises(RetrievalError, match="Dense top_k must be greater than zero"):
        route.search("无线耳机", top_k=0)

    embedding.embed.assert_not_called()
    vector_store.search.assert_not_called()


def test_dense_route_wraps_embedding_failure_with_stage_context() -> None:
    """Require embedding failures to cross the route boundary as RetrievalError."""

    settings = _settings(rewrite_enabled=False)
    embedding = Mock()
    embedding.embed.side_effect = RuntimeError("embedding unavailable")
    vector_store = Mock()
    route = DenseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        embedding=embedding,
        vector_store=vector_store,
    )

    with pytest.raises(RetrievalError, match="Dense query embedding failed") as captured:
        route.search("无线耳机")

    assert captured.value.context == {
        "stage": "dense",
        "operation": "query_embedding",
    }
    assert isinstance(captured.value.cause, RuntimeError)
    vector_store.search.assert_not_called()


def test_dense_route_wraps_vector_search_failure_with_stage_context() -> None:
    """Require vector-store failures to use the retrieval error boundary."""

    settings = _settings(rewrite_enabled=False)
    embedding = Mock()
    embedding.embed.return_value = [0.2, 0.8]
    vector_store = Mock()
    vector_store.search.side_effect = RuntimeError("pgvector unavailable")
    route = DenseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        embedding=embedding,
        vector_store=vector_store,
    )

    with pytest.raises(RetrievalError, match="Dense vector search failed") as captured:
        route.search("无线耳机")

    assert captured.value.context == {
        "stage": "dense",
        "operation": "vector_search",
        "top_k": 30,
    }
    assert isinstance(captured.value.cause, RuntimeError)


def test_dense_route_records_success_and_failure_trace_details() -> None:
    """Require optional trace injection to report timing, counts, and failures."""

    settings = _settings(rewrite_enabled=False)
    embedding = Mock()
    embedding.embed.return_value = [0.3, 0.7]
    vector_store = Mock()
    vector_store.search.return_value = []
    trace = Mock()
    route = DenseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        embedding=embedding,
        vector_store=vector_store,
    )

    assert route.search("无线耳机", trace_context=trace) == []

    trace.record_stage.assert_called_once()
    success_call = trace.record_stage.call_args
    assert success_call.kwargs["stage"] == "dense"
    assert success_call.kwargs["method"] == "vector_search"
    assert success_call.kwargs["status"] == "success"
    assert success_call.kwargs["candidate_count"] == 0
    assert success_call.kwargs["details"] == {"top_k": 30, "chunk_ids": []}
    assert success_call.kwargs["duration_ms"] >= 0

    trace.reset_mock()
    embedding.embed.side_effect = RuntimeError("offline")
    with pytest.raises(RetrievalError):
        route.search("无线耳机", trace_context=trace)

    failure_call = trace.record_stage.call_args
    assert failure_call.kwargs["status"] == "failed"
    assert failure_call.kwargs["candidate_count"] == 0
    assert failure_call.kwargs["details"]["operation"] == "query_embedding"


def test_dense_route_trace_uses_provider_independent_method_name() -> None:
    """Require business tracing to avoid hardcoding one vector-store provider."""

    settings = _settings(rewrite_enabled=False)
    embedding = Mock()
    embedding.embed.return_value = [0.5, 0.5]
    vector_store = Mock()
    vector_store.search.return_value = []
    trace = Mock()
    route = DenseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        embedding=embedding,
        vector_store=vector_store,
    )

    route.search("无线耳机", trace_context=trace)

    assert trace.record_stage.call_args.kwargs["method"] == "vector_search"


def test_dense_route_trace_failure_does_not_break_successful_retrieval() -> None:
    """Require optional observability failure to remain isolated from search."""

    settings = _settings(rewrite_enabled=False)
    embedding = Mock()
    embedding.embed.return_value = [0.4, 0.6]
    vector_store = Mock()
    vector_store.search.return_value = [
        {
            "chunk_id": "chunk-1",
            "text": "无线耳机选购内容",
            "score": 0.8,
            "metadata": {},
        }
    ]
    trace = Mock()
    trace.record_stage.side_effect = RuntimeError("trace sink unavailable")
    route = DenseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        embedding=embedding,
        vector_store=vector_store,
    )

    results = route.search("无线耳机", trace_context=trace)

    assert [result.chunk_id for result in results] == ["chunk-1"]


def test_sparse_route_queries_bm25_and_hydrates_chunks_in_candidate_order() -> None:
    """Require Sparse Route to preserve BM25 ranking while hydrating chunk data."""

    settings = _settings(rewrite_enabled=False)
    processor = QueryProcessor(settings=settings)
    processed = processor.process("高性价比 无线耳机 推荐")
    bm25_indexer = Mock()
    bm25_indexer.query.return_value = [
        SimpleNamespace(chunk_id="chunk-b", score=2.4),
        SimpleNamespace(chunk_id="chunk-a", score=1.7),
    ]
    vector_store = Mock()
    vector_store.get_by_ids.return_value = [
        _chunk("chunk-b", "预算有限时关注蓝牙稳定性。", metadata={"collection": "shopping_guides"}),
        _chunk("chunk-a", "主动降噪适合通勤场景。", metadata={"collection": "shopping_guides"}),
    ]
    route = SparseRoute(
        settings=settings,
        query_processor=processor,
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )

    results = route.search(processed)

    bm25_indexer.query.assert_called_once_with(
        list(processed.keywords),
        top_k=25,
        collection="shopping_guides",
    )
    vector_store.get_by_ids.assert_called_once_with(["chunk-b", "chunk-a"])
    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-a"]
    assert [result.score for result in results] == [2.4, 1.7]
    assert results[0].text == "预算有限时关注蓝牙稳定性。"
    assert results[0].metadata == {
        "collection": "shopping_guides",
        "document_id": "doc-1",
    }


def test_sparse_route_processes_raw_query_and_allows_top_k_override() -> None:
    """Require raw strings to pass through QueryProcessor before BM25 retrieval."""

    settings = _settings(rewrite_enabled=False)
    processor = Mock()
    processor.process.return_value = ProcessedQuery(
        raw_query="耳机推荐",
        normalized_query="耳机推荐",
        keywords=("耳机", "推荐"),
        collection="shopping_guides",
        top_k=5,
    )
    bm25_indexer = Mock()
    bm25_indexer.query.return_value = []
    vector_store = Mock()
    route = SparseRoute(
        settings=settings,
        query_processor=processor,
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )

    assert route.search("耳机推荐", top_k=7) == []

    processor.process.assert_called_once_with("耳机推荐")
    bm25_indexer.query.assert_called_once_with(
        ["耳机", "推荐"],
        top_k=7,
        collection="shopping_guides",
    )
    vector_store.get_by_ids.assert_not_called()


def test_sparse_route_skips_empty_keywords_before_provider_calls() -> None:
    """Require empty keyword snapshots to return no Sparse candidates."""

    settings = _settings(rewrite_enabled=False)
    processed = ProcessedQuery(
        raw_query="?",
        normalized_query="?",
        keywords=(),
        collection="shopping_guides",
        top_k=5,
    )
    bm25_indexer = Mock()
    vector_store = Mock()
    trace = Mock()
    route = SparseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )

    assert route.search(processed, trace_context=trace) == []

    bm25_indexer.query.assert_not_called()
    vector_store.get_by_ids.assert_not_called()
    trace.record_stage.assert_called_once()
    assert trace.record_stage.call_args.kwargs["status"] == "skipped"
    assert trace.record_stage.call_args.kwargs["details"]["reason"] == "empty_keywords"


def test_sparse_route_rejects_invalid_candidate_limit_before_provider_calls() -> None:
    """Require invalid Sparse limits to fail without BM25 or storage access."""

    settings = _settings(rewrite_enabled=False)
    bm25_indexer = Mock()
    vector_store = Mock()
    route = SparseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )

    with pytest.raises(RetrievalError, match="Sparse top_k must be greater than zero"):
        route.search("无线耳机", top_k=0)

    bm25_indexer.query.assert_not_called()
    vector_store.get_by_ids.assert_not_called()


def test_sparse_route_skips_missing_hydrated_chunks_and_records_trace_details() -> None:
    """Require stale BM25 IDs to be omitted while remaining observable."""

    settings = _settings(rewrite_enabled=False)
    processed = ProcessedQuery(
        raw_query="耳机",
        normalized_query="耳机",
        keywords=("耳机",),
        collection="shopping_guides",
        top_k=5,
    )
    bm25_indexer = Mock()
    bm25_indexer.query.return_value = [
        SimpleNamespace(chunk_id="chunk-a", score=3.0),
        SimpleNamespace(chunk_id="missing", score=2.0),
        SimpleNamespace(chunk_id="chunk-b", score=1.0),
    ]
    vector_store = Mock()
    vector_store.get_by_ids.return_value = [
        _chunk("chunk-a", "耳机 A"),
        _chunk("chunk-b", "耳机 B"),
    ]
    trace = Mock()
    route = SparseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )

    results = route.search(processed, trace_context=trace)

    assert [result.chunk_id for result in results] == ["chunk-a", "chunk-b"]
    assert trace.record_stage.call_args.kwargs["details"]["missing_chunk_ids"] == [
        "missing"
    ]


def test_sparse_route_wraps_bm25_query_failure_with_stage_context() -> None:
    """Require BM25 failures to cross the route boundary as RetrievalError."""

    settings = _settings(rewrite_enabled=False)
    bm25_indexer = Mock()
    bm25_indexer.query.side_effect = RuntimeError("bm25 unavailable")
    vector_store = Mock()
    route = SparseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )

    with pytest.raises(RetrievalError, match="Sparse BM25 query failed") as captured:
        route.search("无线耳机")

    assert captured.value.context == {
        "stage": "sparse",
        "operation": "bm25_query",
        "top_k": 25,
    }
    assert isinstance(captured.value.cause, RuntimeError)
    vector_store.get_by_ids.assert_not_called()


def test_sparse_route_wraps_invalid_bm25_candidate_shape() -> None:
    """Require malformed BM25 provider results to stay inside Sparse boundaries."""

    settings = _settings(rewrite_enabled=False)
    bm25_indexer = Mock()
    bm25_indexer.query.return_value = [SimpleNamespace(id="chunk-a", score=1.0)]
    vector_store = Mock()
    trace = Mock()
    route = SparseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )

    with pytest.raises(RetrievalError, match="Sparse BM25 query failed") as captured:
        route.search("无线耳机", trace_context=trace)

    assert captured.value.context == {
        "stage": "sparse",
        "operation": "bm25_query",
        "top_k": 25,
    }
    assert isinstance(captured.value.cause, AttributeError)
    vector_store.get_by_ids.assert_not_called()
    assert trace.record_stage.call_args.kwargs["status"] == "failed"
    assert trace.record_stage.call_args.kwargs["details"]["operation"] == "bm25_query"


def test_sparse_route_wraps_chunk_hydration_failure_with_stage_context() -> None:
    """Require chunk hydration failures to use the retrieval error boundary."""

    settings = _settings(rewrite_enabled=False)
    bm25_indexer = Mock()
    bm25_indexer.query.return_value = [SimpleNamespace(chunk_id="chunk-a", score=1.2)]
    vector_store = Mock()
    vector_store.get_by_ids.side_effect = RuntimeError("database unavailable")
    route = SparseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )

    with pytest.raises(RetrievalError, match="Sparse chunk hydration failed") as captured:
        route.search("无线耳机")

    assert captured.value.context == {
        "stage": "sparse",
        "operation": "chunk_hydration",
        "candidate_count": 1,
    }
    assert isinstance(captured.value.cause, RuntimeError)


def test_sparse_route_trace_failure_does_not_break_successful_retrieval() -> None:
    """Require optional observability failure to remain isolated from Sparse search."""

    settings = _settings(rewrite_enabled=False)
    bm25_indexer = Mock()
    bm25_indexer.query.return_value = [SimpleNamespace(chunk_id="chunk-a", score=1.2)]
    vector_store = Mock()
    vector_store.get_by_ids.return_value = [_chunk("chunk-a", "耳机选购指南")]
    trace = Mock()
    trace.record_stage.side_effect = RuntimeError("trace sink unavailable")
    route = SparseRoute(
        settings=settings,
        query_processor=QueryProcessor(settings=settings),
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )

    results = route.search("无线耳机", trace_context=trace)

    assert [result.chunk_id for result in results] == ["chunk-a"]


def test_rrf_fusion_ranks_by_reciprocal_rank_instead_of_native_scores() -> None:
    """Require RRF to ignore incomparable Dense and BM25 native score scales."""

    dense = [
        _result("dense-top", score=0.01, metadata={"route": "dense"}),
        _result("shared", score=0.02),
    ]
    sparse = [
        _result("sparse-top", score=999.0, metadata={"route": "sparse"}),
        _result("shared", score=500.0),
    ]

    fused = reciprocal_rank_fusion(dense, sparse, top_k=3, rrf_k=60)

    assert [result.chunk_id for result in fused] == ["shared", "dense-top", "sparse-top"]
    assert fused[0].score == pytest.approx((1 / 62) + (1 / 62))
    assert fused[1].score == pytest.approx(1 / 61)
    assert fused[2].score == pytest.approx(1 / 61)
    assert fused[0].metadata["fusion"] == {
        "dense_rank": 2,
        "sparse_rank": 2,
        "dense_score": 0.02,
        "sparse_score": 500.0,
        "sources": ["dense", "sparse"],
    }


def test_rrf_fusion_limits_results_and_preserves_source_payload_deterministically() -> None:
    """Require fused results to expose a stable payload and metadata contract."""

    dense = [
        _result("shared", score=0.8, text="Dense payload", metadata={"collection": "dense"}),
        _result("dense-only", score=0.7),
    ]
    sparse = [
        _result("shared", score=4.0, text="Sparse payload", metadata={"collection": "sparse"}),
        _result("sparse-only", score=3.0),
    ]

    fused = reciprocal_rank_fusion(dense, sparse, top_k=1, rrf_k=60)

    assert [result.chunk_id for result in fused] == ["shared"]
    assert fused[0].text == "Dense payload"
    assert fused[0].metadata["collection"] == "dense"
    assert fused[0].metadata["fusion"]["dense_rank"] == 1
    assert fused[0].metadata["fusion"]["sparse_rank"] == 1


def test_rrf_fusion_deduplicates_repeated_chunk_ids_within_each_route() -> None:
    """Require duplicate provider results to contribute only their first rank."""

    dense = [
        _result("dup", score=0.1),
        _result("dup", score=0.9),
        _result("later", score=0.8),
    ]
    sparse = [_result("later", score=10.0)]

    fused = reciprocal_rank_fusion(dense, sparse, top_k=10, rrf_k=10)

    assert [result.chunk_id for result in fused] == ["later", "dup"]
    assert fused[0].score == pytest.approx((1 / 13) + (1 / 11))
    assert fused[1].score == pytest.approx(1 / 11)
    assert fused[1].metadata["fusion"]["dense_rank"] == 1


def test_rrf_fusion_returns_empty_list_for_empty_routes() -> None:
    """Require empty Dense and Sparse candidate pools to remain a valid result."""

    assert reciprocal_rank_fusion([], [], top_k=5, rrf_k=60) == []


@pytest.mark.parametrize(
    ("top_k", "rrf_k", "message"),
    [
        (0, 60, "Fusion top_k must be greater than zero"),
        (3, 0, "RRF k must be greater than zero"),
        (True, 60, "Fusion top_k must be an integer"),
        (3, True, "RRF k must be an integer"),
    ],
)
def test_rrf_fusion_rejects_invalid_limits(
    top_k: int,
    rrf_k: int,
    message: str,
) -> None:
    """Require invalid fusion parameters to fail before ranking work."""

    with pytest.raises(RetrievalError, match=message):
        reciprocal_rank_fusion([], [], top_k=top_k, rrf_k=rrf_k)


def test_hybrid_search_runs_dense_sparse_and_rrf_fusion() -> None:
    """Require HybridSearch to orchestrate both routes and RRF with settings."""

    settings = _settings(rewrite_enabled=False)
    settings.retrieval.fusion_top_k = 2
    settings.retrieval.rrf_k = 60
    processed = _processed_query()
    dense_route = Mock()
    dense_route.search.return_value = [
        _result("dense-only", score=0.8),
        _result("shared", score=0.7),
    ]
    sparse_route = Mock()
    sparse_route.search.return_value = [
        _result("shared", score=4.0),
        _result("sparse-only", score=3.0),
    ]
    trace = Mock()
    search = HybridSearch(
        settings=settings,
        dense_route=dense_route,
        sparse_route=sparse_route,
    )

    result = search.search(processed, trace_context=trace)

    dense_route.search.assert_called_once_with(processed, trace_context=trace)
    sparse_route.search.assert_called_once_with(processed, trace_context=trace)
    assert [candidate.chunk_id for candidate in result.results] == [
        "shared",
        "dense-only",
    ]
    assert result.dense_results == dense_route.search.return_value
    assert result.sparse_results == sparse_route.search.return_value
    assert result.fallback_used is False
    assert result.fallback_reasons == {}
    assert result.results[0].metadata["fusion"]["dense_rank"] == 2
    assert result.results[0].metadata["fusion"]["sparse_rank"] == 1
    fusion_call = next(
        call for call in trace.record_stage.call_args_list
        if call.kwargs["stage"] == "fusion"
    )
    assert fusion_call.kwargs["status"] == "success"


def test_hybrid_search_falls_back_to_sparse_when_dense_route_fails() -> None:
    """Require Dense failures to degrade to Sparse results when available."""

    settings = _settings(rewrite_enabled=False)
    settings.retrieval.fusion_top_k = 3
    settings.retrieval.rrf_k = 10
    processed = _processed_query()
    dense_route = Mock()
    dense_route.search.side_effect = RetrievalError("dense unavailable")
    sparse_route = Mock()
    sparse_route.search.return_value = [_result("sparse-only", score=2.0)]
    trace = Mock()
    search = HybridSearch(
        settings=settings,
        dense_route=dense_route,
        sparse_route=sparse_route,
    )

    result = search.search(processed, trace_context=trace)

    assert [candidate.chunk_id for candidate in result.results] == ["sparse-only"]
    assert result.dense_results == []
    assert result.sparse_results == sparse_route.search.return_value
    assert result.fallback_used is True
    assert result.fallback_reasons == {"dense": "dense unavailable"}
    assert result.results[0].metadata["fusion"]["sources"] == ["sparse"]
    fusion_call = next(
        call for call in trace.record_stage.call_args_list
        if call.kwargs["stage"] == "fusion"
    )
    assert fusion_call.kwargs["status"] == "degraded"
    assert fusion_call.kwargs["details"]["failed_routes"] == ["dense"]


def test_hybrid_search_falls_back_to_dense_when_sparse_route_fails() -> None:
    """Require Sparse failures to degrade to Dense results when available."""

    settings = _settings(rewrite_enabled=False)
    settings.retrieval.fusion_top_k = 3
    settings.retrieval.rrf_k = 10
    processed = _processed_query()
    dense_route = Mock()
    dense_route.search.return_value = [_result("dense-only", score=0.9)]
    sparse_route = Mock()
    sparse_route.search.side_effect = RetrievalError("sparse unavailable")
    search = HybridSearch(
        settings=settings,
        dense_route=dense_route,
        sparse_route=sparse_route,
    )

    result = search.search(processed)

    assert [candidate.chunk_id for candidate in result.results] == ["dense-only"]
    assert result.dense_results == dense_route.search.return_value
    assert result.sparse_results == []
    assert result.fallback_used is True
    assert result.fallback_reasons == {"sparse": "sparse unavailable"}
    assert result.results[0].metadata["fusion"]["sources"] == ["dense"]


def test_hybrid_search_raises_when_both_routes_fail() -> None:
    """Require HybridSearch to fail only when no retrieval route can return."""

    settings = _settings(rewrite_enabled=False)
    settings.retrieval.fusion_top_k = 3
    settings.retrieval.rrf_k = 10
    dense_route = Mock()
    dense_route.search.side_effect = RetrievalError("dense unavailable")
    sparse_route = Mock()
    sparse_route.search.side_effect = RetrievalError("sparse unavailable")
    trace = Mock()
    search = HybridSearch(
        settings=settings,
        dense_route=dense_route,
        sparse_route=sparse_route,
    )

    with pytest.raises(RetrievalError, match="Hybrid search failed") as captured:
        search.search(_processed_query(), trace_context=trace)

    assert captured.value.context == {
        "stage": "fusion",
        "failed_routes": ["dense", "sparse"],
    }
    assert trace.record_stage.call_args.kwargs["status"] == "failed"
    assert trace.record_stage.call_args.kwargs["details"]["failed_routes"] == [
        "dense",
        "sparse",
    ]


def test_hybrid_search_wraps_unexpected_fusion_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protect the fusion error boundary after both routes return candidates."""

    settings = _settings(rewrite_enabled=False)
    settings.retrieval.fusion_top_k = 3
    settings.retrieval.rrf_k = 10
    dense_route = Mock()
    dense_route.search.return_value = [_result("dense-only", score=0.9)]
    sparse_route = Mock()
    sparse_route.search.return_value = [_result("sparse-only", score=2.0)]
    trace = Mock()
    monkeypatch.setattr(
        "src.core.query_engine.hybrid_engine.reciprocal_rank_fusion",
        Mock(side_effect=RuntimeError("fusion implementation failed")),
    )
    search = HybridSearch(
        settings=settings,
        dense_route=dense_route,
        sparse_route=sparse_route,
    )

    with pytest.raises(RetrievalError, match="Hybrid fusion failed") as captured:
        search.search(_processed_query(), trace_context=trace)

    assert captured.value.context == {
        "stage": "fusion",
        "operation": "fusion",
    }
    assert isinstance(captured.value.cause, RuntimeError)
    assert trace.record_stage.call_args.kwargs["status"] == "failed"


def test_hybrid_search_trace_failure_does_not_break_successful_retrieval() -> None:
    """Require optional hybrid trace failures to remain isolated from results."""

    settings = _settings(rewrite_enabled=False)
    settings.retrieval.fusion_top_k = 3
    settings.retrieval.rrf_k = 10
    dense_route = Mock()
    dense_route.search.return_value = [_result("dense-only", score=0.9)]
    sparse_route = Mock()
    sparse_route.search.return_value = []
    trace = Mock()
    trace.record_stage.side_effect = RuntimeError("trace sink unavailable")
    search = HybridSearch(
        settings=settings,
        dense_route=dense_route,
        sparse_route=sparse_route,
    )

    result = search.search(_processed_query(), trace_context=trace)

    assert [candidate.chunk_id for candidate in result.results] == ["dense-only"]


def test_candidate_filter_applies_supported_metadata_filters_and_preserves_order() -> None:
    """Require CandidateFilter to keep only candidates matching all filter fields."""

    candidates = [
        _result("keep-a", score=0.9, metadata={"collection": "shopping_guides"}),
        _result("wrong-collection", score=0.8, metadata={"collection": "policy_faq"}),
        _result("keep-b", score=0.7, metadata={"collection": "shopping_guides"}),
    ]

    report = CandidateFilter().apply(candidates, {"collection": "shopping_guides"})

    assert [result.chunk_id for result in report.results] == ["keep-a", "keep-b"]
    assert report.before_count == 3
    assert report.after_count == 2
    assert report.rejected_counts == {"collection": 1}
    assert report.rejected_chunk_ids == {"collection": ["wrong-collection"]}


def test_candidate_filter_supports_type_status_permission_and_lifecycle_filters() -> None:
    """Require rerank candidates to honor document and permission constraints."""

    candidates = [
        _result("keep", score=0.9, metadata={"permissions": ["public", "vip"]}),
        _result("wrong-doc-type", score=0.8, metadata={"doc_type": "faq"}),
        _result("wrong-source", score=0.7, metadata={"source_type": "pdf"}),
        _result("draft", score=0.6, metadata={"document_status": "draft"}),
        _result("deleted", score=0.5, metadata={"lifecycle_status": "deleted"}),
        _result("private", score=0.4, metadata={"permissions": ["admin"]}),
    ]

    report = CandidateFilter().apply(
        candidates,
        {
            "doc_type": "guide",
            "source_type": "markdown",
            "document_status": "published",
            "lifecycle_status": "success",
            "permission": "vip",
        },
    )

    assert [result.chunk_id for result in report.results] == ["keep"]
    assert report.rejected_counts == {
        "doc_type": 1,
        "source_type": 1,
        "document_status": 1,
        "lifecycle_status": 1,
        "permission": 1,
    }


def test_candidate_filter_excludes_deleted_lifecycle_by_default() -> None:
    """Require deleted documents to stay out of rerank unless explicitly allowed."""

    candidates = [
        _result("active", score=0.9),
        _result("deleted", score=0.8, metadata={"lifecycle_status": "deleted"}),
    ]

    default_report = CandidateFilter().apply(candidates, {})
    include_deleted_report = CandidateFilter().apply(candidates, {"include_deleted": True})

    assert [result.chunk_id for result in default_report.results] == ["active"]
    assert default_report.rejected_counts == {"lifecycle_status": 1}
    assert [result.chunk_id for result in include_deleted_report.results] == [
        "active",
        "deleted",
    ]


def test_candidate_filter_rejects_unknown_filter_keys() -> None:
    """Require unsupported filter parameters to fail before silently changing recall."""

    with pytest.raises(RetrievalError, match="Unsupported metadata filter"):
        CandidateFilter().apply([], {"unknown": "value"})


def test_candidate_filter_rejects_non_boolean_include_deleted() -> None:
    """Require lifecycle visibility flags to reject ambiguous string values."""

    with pytest.raises(RetrievalError, match="include_deleted must be a boolean"):
        CandidateFilter().apply([], {"include_deleted": "false"})


def test_hybrid_search_applies_metadata_filter_after_fusion_and_records_trace() -> None:
    """Require HybridSearch filtering to happen after RRF and before rerank."""

    settings = _settings(rewrite_enabled=False)
    settings.retrieval.fusion_top_k = 3
    settings.retrieval.rrf_k = 10
    dense_route = Mock()
    dense_route.search.return_value = [
        _result("keep", score=0.9, metadata={"collection": "shopping_guides"}),
        _result("drop", score=0.8, metadata={"collection": "policy_faq"}),
    ]
    sparse_route = Mock()
    sparse_route.search.return_value = []
    trace = Mock()
    search = HybridSearch(
        settings=settings,
        dense_route=dense_route,
        sparse_route=sparse_route,
    )

    result = search.search(
        _processed_query(),
        filters={"collection": "shopping_guides"},
        trace_context=trace,
    )

    assert [candidate.chunk_id for candidate in result.results] == ["keep"]
    assert result.filter_report is not None
    assert result.filter_report.before_count == 2
    assert result.filter_report.after_count == 1
    filter_call = next(
        call for call in trace.record_stage.call_args_list
        if call.kwargs["stage"] == "filter"
    )
    assert filter_call.kwargs["stage"] == "filter"
    assert filter_call.kwargs["details"]["rejected_counts"] == {"collection": 1}


def test_hybrid_search_apply_metadata_filter_is_reusable_for_cli_parameters() -> None:
    """Require CLI/query adapters to reuse the same metadata filter method."""

    settings = _settings(rewrite_enabled=False)
    search = HybridSearch(
        settings=settings,
        dense_route=Mock(),
        sparse_route=Mock(),
    )
    trace = Mock()

    report = search.apply_metadata_filter(
        [
            _result("keep", score=0.9, metadata={"doc_type": "guide"}),
            _result("drop", score=0.8, metadata={"doc_type": "faq"}),
        ],
        filters={"doc_type": "guide"},
        trace_context=trace,
    )

    assert [candidate.chunk_id for candidate in report.results] == ["keep"]
    assert trace.record_stage.call_args.kwargs["stage"] == "filter"


def test_hybrid_search_filter_trace_failure_does_not_break_results() -> None:
    """Require optional filter trace failures to remain isolated from results."""

    settings = _settings(rewrite_enabled=False)
    search = HybridSearch(
        settings=settings,
        dense_route=Mock(),
        sparse_route=Mock(),
    )
    trace = Mock()
    trace.record_stage.side_effect = RuntimeError("trace sink unavailable")

    report = search.apply_metadata_filter(
        [_result("keep", score=0.9)],
        filters={"collection": "shopping_guides"},
        trace_context=trace,
    )

    assert [candidate.chunk_id for candidate in report.results] == ["keep"]


def test_query_parse_args_supports_required_query_and_optional_controls() -> None:
    """Require the local query CLI to expose the complete D12 option contract."""

    defaults = query_module.parse_args(["--query", "无线耳机怎么选"])
    configured = query_module.parse_args(
        [
            "--query",
            "无线耳机怎么选",
            "--top-k",
            "7",
            "--collection",
            "premium_guides",
            "--verbose",
            "--no-rerank",
        ]
    )

    assert defaults.query == "无线耳机怎么选"
    assert defaults.top_k == 10
    assert defaults.collection is None
    assert defaults.verbose is False
    assert defaults.no_rerank is False
    assert configured.top_k == 7
    assert configured.collection == "premium_guides"
    assert configured.verbose is True
    assert configured.no_rerank is True


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--query", " "],
        ["--query", "valid", "--top-k", "0"],
        ["--query", "valid", "--collection", " "],
    ],
)
def test_query_parse_args_rejects_invalid_required_values(
    argv: list[str],
) -> None:
    """Reject missing, blank, or non-positive query parameters at parsing."""

    with pytest.raises(SystemExit):
        query_module.parse_args(argv)


def test_run_query_cli_emits_public_response_and_verbose_stage_summaries() -> None:
    """Run one injected query runtime and expose only trace-safe diagnostics."""

    processed = _processed_query().model_copy(
        update={"top_k": 3, "collection": "premium_guides"}
    )
    dense = [_result("dense-a", score=0.91)]
    sparse = [_result("sparse-a", score=2.4)]
    fused = [_result("shared", score=0.032)]
    filtered = [_result("shared", score=0.032)]
    final = [_result("shared", score=0.97)]
    response = KnowledgeHubResponse(
        content="[1] Ranked knowledge.",
        citations=(),
        images=(),
        trace_id="query-test-001",
        is_empty=False,
    )
    execution = query_module.QueryExecutionResult(
        processed_query=processed,
        intent_route=query_module.IntentRoute(
            collection="premium_guides",
            collections=("premium_guides",),
            domain_intent="buying_guide",
            complexity="simple",
            retrieval_strategy="hybrid",
            confidence=0.95,
            method="rules",
            reason="test route",
        ),
        dense_results=tuple(dense),
        sparse_results=tuple(sparse),
        fused_results=tuple(fused),
        filtered_results=tuple(filtered),
        final_results=tuple(final),
        self_rag_decision=None,
        response=response,
        rerank_applied=True,
        fallback_used=False,
    )
    runtime = Mock()
    runtime.execute.return_value = execution
    runtime_builder = Mock(return_value=runtime)
    pool = Mock()
    settings = SimpleNamespace(
        database=SimpleNamespace(),
        retrieval=SimpleNamespace(
            filters=SimpleNamespace(default_collection="shopping_guides")
        ),
    )
    output: list[str] = []

    exit_code = query_module.run_query_cli(
        [
            "--query",
            "无线耳机怎么选",
            "--top-k",
            "3",
            "--collection",
            "premium_guides",
            "--verbose",
        ],
        settings_loader=lambda: settings,
        pool_factory=lambda _: pool,
        schema_initializer=lambda _: None,
        runtime_builder=runtime_builder,
        trace_id_factory=lambda: "query-test-001",
        output=output.append,
    )

    assert exit_code == 0
    pool.open.assert_called_once_with()
    pool.close.assert_called_once_with()
    runtime_builder.assert_called_once_with(settings, pool, False)
    runtime.execute.assert_called_once_with(
        "无线耳机怎么选",
        collection="premium_guides",
        top_k=3,
        no_rerank=False,
        trace_id="query-test-001",
        request_source="query_cli",
    )
    payload = json.loads(output[0])
    assert payload["response"] == response.model_dump(mode="json")
    assert payload["debug"]["query_processor"]["collection"] == "premium_guides"
    assert payload["debug"]["dense"] == [{"chunk_id": "dense-a", "score": 0.91}]
    assert payload["debug"]["sparse"] == [{"chunk_id": "sparse-a", "score": 2.4}]
    assert payload["debug"]["fusion"] == [{"chunk_id": "shared", "score": 0.032}]
    assert payload["debug"]["filter"] == [{"chunk_id": "shared", "score": 0.032}]
    assert payload["debug"]["rerank"] == {
        "applied": True,
        "fallback_used": False,
        "results": [{"chunk_id": "shared", "score": 0.97}],
    }
    serialized = output[0]
    assert "metadata" not in serialized
    assert "tool_result" not in serialized


def test_run_query_cli_forwards_no_rerank_and_closes_pool_after_failure() -> None:
    """Forward rerank bypass and release PostgreSQL when query execution fails."""

    runtime = Mock()
    runtime.execute.side_effect = RuntimeError("query failed")
    runtime_builder = Mock(return_value=runtime)
    pool = Mock()
    settings = SimpleNamespace(
        database=SimpleNamespace(),
        retrieval=SimpleNamespace(
            filters=SimpleNamespace(default_collection="shopping_guides")
        ),
    )
    errors: list[str] = []

    exit_code = query_module.run_query_cli(
        ["--query", "无线耳机", "--no-rerank"],
        settings_loader=lambda: settings,
        pool_factory=lambda _: pool,
        schema_initializer=lambda _: None,
        runtime_builder=runtime_builder,
        trace_id_factory=lambda: "query-test-failure",
        output=lambda _: None,
        error_output=errors.append,
    )

    assert exit_code == 1
    runtime_builder.assert_called_once_with(settings, pool, True)
    runtime.execute.assert_called_once_with(
        "无线耳机",
        collection="shopping_guides",
        top_k=10,
        no_rerank=True,
        trace_id="query-test-failure",
        request_source="query_cli",
    )
    pool.close.assert_called_once_with()
    assert errors == ["Query failed: query failed"]


def test_postgres_bm25_query_scores_collection_postings_in_rank_order() -> None:
    """Calculate BM25 from persisted postings using collection corpus stats."""

    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = [
        ("chunk-b", "无线耳机", 2, 10, 3, 12.0, 2),
        ("chunk-a", "无线耳机", 1, 8, 3, 12.0, 2),
        ("chunk-a", "推荐", 1, 8, 3, 12.0, 1),
    ]
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = connection
    storage = BM25Storage(pool)

    candidates = storage.query(
        ["无线耳机", "推荐"],
        top_k=2,
        collection="shopping_guides",
    )

    assert [candidate.chunk_id for candidate in candidates] == [
        "chunk-a",
        "chunk-b",
    ]
    assert candidates[0].score > candidates[1].score > 0
    params = connection.execute.call_args.args[1]
    assert params[0] == "shopping_guides"
    assert params[1] == ["无线耳机", "推荐"]


@pytest.mark.parametrize(
    "keywords,top_k,collection,message",
    [
        (["耳机"], 0, "shopping_guides", "top_k"),
        (["耳机"], True, "shopping_guides", "top_k"),
        (["耳机"], 5, None, "collection"),
        (["耳机"], 5, " ", "collection"),
    ],
)
def test_postgres_bm25_query_rejects_invalid_boundaries_before_database_access(
    keywords: list[str],
    top_k: int,
    collection: str | None,
    message: str,
) -> None:
    """Fail fast for invalid Sparse query limits and collection isolation."""

    pool = MagicMock()
    storage = BM25Storage(pool)

    with pytest.raises(ValueError, match=message):
        storage.query(keywords, top_k=top_k, collection=collection)

    pool.connection.assert_not_called()


def test_postgres_bm25_query_short_circuits_empty_terms() -> None:
    """Avoid PostgreSQL work when query analysis produces no searchable terms."""

    pool = MagicMock()

    assert BM25Storage(pool).query(
        "   --- ",
        top_k=5,
        collection="shopping_guides",
    ) == []
    pool.connection.assert_not_called()


def test_postgres_bm25_query_wraps_driver_failures() -> None:
    """Translate psycopg failures into the shared database error contract."""

    connection = MagicMock()
    connection.execute.side_effect = psycopg.OperationalError("database offline")
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = connection

    with pytest.raises(DatabaseError, match="PostgreSQL BM25 query failed") as captured:
        BM25Storage(pool).query(
            ["无线耳机"],
            top_k=5,
            collection="shopping_guides",
        )

    assert captured.value.context == {
        "operation": "bm25_query",
        "collection": "shopping_guides",
    }
    assert isinstance(captured.value.cause, psycopg.OperationalError)



def test_self_rag_controller_accepts_high_confidence_without_judge() -> None:
    """Skip LLM judging when the top reranked candidates are already strong."""

    judge = _FakeJudgeLLM('{"relevant": false}')
    traces: list[dict[str, object]] = []
    controller = SelfRagController(settings=_self_rag_settings(), llm_client=judge)
    candidates = [
        _result("chunk-a", score=0.91),
        _result("chunk-b", score=0.86),
        _result("chunk-c", score=0.79),
    ]

    decision = controller.evaluate(
        "无线耳机怎么选",
        candidates,
        trace_context=SimpleNamespace(record_stage=lambda **kwargs: traces.append(kwargs)),
    )

    assert decision.decision == "accepted"
    assert decision.score_band == "high_confidence"
    assert [result.chunk_id for result in decision.selected_results] == [
        "chunk-a",
        "chunk-b",
        "chunk-c",
    ]
    assert judge.messages == []
    assert decision.selected_results[0] is not candidates[0]
    assert traces[0]["stage"] == "self_rag"
    assert traces[0]["details"]["judge_called"] is False


def test_self_rag_controller_trims_low_score_candidates_and_accepts_judge() -> None:
    """Call one judge after dropping weak rerank candidates from the Prompt."""

    judge = _FakeJudgeLLM(
        json.dumps(
            {
                "relevant": True,
                "relevance_score": 0.82,
                "sufficient": True,
                "evidence_sufficiency_score": 0.78,
                "missing_evidence": [],
                "reason": "The retained evidence answers the query.",
            }
        )
    )
    controller = SelfRagController(settings=_self_rag_settings(), llm_client=judge)
    candidates = [
        _result("chunk-a", score=0.64),
        _result("chunk-b", score=0.32),
        _result("chunk-c", score=0.04),
    ]

    decision = controller.evaluate("退货规则是什么", candidates)

    assert decision.decision == "accepted"
    assert decision.score_band == "medium_confidence"
    assert decision.judge_result == SelfRagJudgeResult(
        relevant=True,
        relevance_score=0.82,
        sufficient=True,
        evidence_sufficiency_score=0.78,
        missing_evidence=(),
        reason="The retained evidence answers the query.",
    )
    assert [result.chunk_id for result in decision.selected_results] == [
        "chunk-a",
        "chunk-b",
    ]
    assert len(judge.messages) == 1
    rendered_prompt = judge.messages[0][1].content
    assert "chunk-a" in rendered_prompt
    assert "chunk-b" in rendered_prompt
    assert "chunk-c" not in rendered_prompt


def test_self_rag_controller_returns_empty_when_judge_rejects_evidence() -> None:
    """Use the empty fallback when relevance or sufficiency fails."""

    judge = _FakeJudgeLLM(
        json.dumps(
            {
                "relevant": True,
                "relevance_score": 0.75,
                "sufficient": False,
                "evidence_sufficiency_score": 0.42,
                "missing_evidence": ["delivery window"],
                "reason": "No delivery timing evidence is present.",
            }
        )
    )
    controller = SelfRagController(settings=_self_rag_settings(), llm_client=judge)

    decision = controller.evaluate("多久能送到", [_result("chunk-a", score=0.58)])

    assert decision.decision == "empty"
    assert decision.selected_results == []
    assert decision.fallback_action == "empty"
    assert decision.reason == "judge_rejected"
    assert decision.judge_result is not None
    assert decision.judge_result.missing_evidence == ("delivery window",)


def test_self_rag_controller_returns_empty_for_low_confidence_without_judge() -> None:
    """Avoid model cost when rerank scores are below the medium threshold."""

    judge = _FakeJudgeLLM('{"relevant": true}')
    controller = SelfRagController(settings=_self_rag_settings(), llm_client=judge)

    decision = controller.evaluate("保修政策", [_result("chunk-a", score=0.18)])

    assert decision.decision == "empty"
    assert decision.score_band == "low_confidence"
    assert decision.reason == "low_confidence"
    assert judge.messages == []


def test_self_rag_controller_invalid_judge_json_returns_empty() -> None:
    """Treat malformed judge output as an empty fallback instead of leaking results."""

    controller = SelfRagController(
        settings=_self_rag_settings(),
        llm_client=_FakeJudgeLLM("not json"),
    )

    decision = controller.evaluate("退货规则", [_result("chunk-a", score=0.61)])

    assert decision.decision == "empty"
    assert decision.reason == "invalid_judge_output"
    assert decision.selected_results == []



def test_self_rag_controller_invalid_judge_schema_returns_empty() -> None:
    """Reject parsed JSON whose field types violate the judge schema."""

    controller = SelfRagController(
        settings=_self_rag_settings(),
        llm_client=_FakeJudgeLLM(
            json.dumps(
                {
                    "relevant": "false",
                    "relevance_score": 0.8,
                    "sufficient": True,
                    "evidence_sufficiency_score": 0.8,
                    "missing_evidence": [],
                    "reason": "Invalid boolean fixture.",
                }
            )
        ),
    )

    decision = controller.evaluate("退货规则", [_result("chunk-a", score=0.61)])

    assert decision.decision == "empty"
    assert decision.reason == "invalid_judge_output"
    assert decision.selected_results == []


def test_self_rag_controller_trace_failure_does_not_break_decision() -> None:
    """Keep answer gating independent from observability failures."""

    controller = SelfRagController(settings=_self_rag_settings(), llm_client=None)

    decision = controller.evaluate(
        "无线耳机怎么选",
        [
            _result("chunk-a", score=0.9),
            _result("chunk-b", score=0.86),
            _result("chunk-c", score=0.8),
        ],
        trace_context=_FailingTraceSink(),
    )

    assert decision.decision == "accepted"
    assert [result.chunk_id for result in decision.selected_results] == [
        "chunk-a",
        "chunk-b",
        "chunk-c",
    ]


def test_query_runtime_skips_reranker_and_preserves_filtered_order() -> None:
    """Bypass the rerank controller and build from filtered hybrid results."""

    processed = _processed_query().model_copy(update={"top_k": 2})
    fused = [
        _result("filtered-a", score=0.04),
        _result("filtered-b", score=0.03),
        _result("filtered-c", score=0.02),
    ]
    hybrid_report = SimpleNamespace(
        dense_results=[_result("dense-a", score=0.9)],
        sparse_results=[_result("sparse-a", score=2.1)],
        fused_results=list(fused),
        results=list(fused),
        fallback_used=False,
    )
    query_processor = Mock()
    query_processor.process.return_value = processed
    hybrid_search = Mock()
    hybrid_search.search.return_value = hybrid_report
    rerank_controller = Mock()
    response = KnowledgeHubResponse(
        content="[1] A\n\n[2] B",
        citations=(),
        images=(),
        trace_id="query-runtime-no-rerank",
        is_empty=False,
    )
    response_builder = Mock()
    response_builder.build.return_value = response
    runtime = query_module.QueryRuntime(
        query_processor=query_processor,
        hybrid_search=hybrid_search,
        rerank_controller=rerank_controller,
        response_builder=response_builder,
    )

    execution = runtime.execute(
        "无线耳机怎么选",
        collection="shopping_guides",
        top_k=2,
        no_rerank=True,
        trace_id="query-runtime-no-rerank",
    )

    query_processor.process.assert_called_once_with(
        "无线耳机怎么选",
        collection="shopping_guides",
        top_k=2,
    )
    hybrid_search.search.assert_called_once_with(
        processed,
        filters={"collection": "shopping_guides"},
        trace_context=ANY,
    )
    rerank_controller.rerank_or_fallback.assert_not_called()
    assert [result.chunk_id for result in execution.final_results] == [
        "filtered-a",
        "filtered-b",
    ]
    response_builder.build.assert_called_once_with(
        list(execution.final_results),
        trace_id="query-runtime-no-rerank",
        query=processed.normalized_query,
    )
    assert execution.rerank_applied is False
    assert execution.fallback_used is False



def test_query_runtime_records_routed_collection_in_trace_snapshot() -> None:
    """Trace top-level collection should follow the Intent Router output."""

    processed = _processed_query().model_copy(update={"collection": "shopping_guides"})
    hybrid_report = SimpleNamespace(
        dense_results=[],
        sparse_results=[],
        fused_results=[],
        results=[],
        fallback_used=False,
    )
    query_processor = Mock()
    query_processor.process.return_value = processed
    hybrid_search = Mock()
    hybrid_search.search.return_value = hybrid_report
    response_builder = Mock()
    response_builder.build.return_value = KnowledgeHubResponse(
        content="",
        citations=(),
        images=(),
        trace_id="query-runtime-routed",
        is_empty=True,
    )
    intent_router = Mock()
    intent_router.route.return_value = query_module.IntentRoute(
        collection="policies",
        collections=("policies",),
        domain_intent="policy_query",
        complexity="simple",
        retrieval_strategy="hybrid",
        confidence=0.96,
        method="rules",
        reason="matched policy route",
    )
    traces: list[dict[str, object]] = []
    runtime = query_module.QueryRuntime(
        query_processor=query_processor,
        hybrid_search=hybrid_search,
        rerank_controller=None,
        response_builder=response_builder,
        intent_router=intent_router,
        trace_sink=traces.append,
    )

    execution = runtime.execute(
        "退货规则是什么",
        collection="shopping_guides",
        top_k=2,
        no_rerank=True,
        trace_id="query-runtime-routed",
    )

    assert execution.processed_query.collection == "policies"
    assert traces[0]["collection"] == "policies"
    assert traces[0]["basic_info"]["collection"] == "policies"
    hybrid_search.search.assert_called_once_with(
        execution.processed_query,
        filters={"collection": "policies"},
        trace_context=ANY,
    )

def test_query_runtime_applies_reranker_before_response_construction() -> None:
    """Exercise the enabled rerank path and preserve its final result snapshot."""

    processed = _processed_query().model_copy(update={"top_k": 1})
    filtered = [
        _result("filtered-a", score=0.04),
        _result("filtered-b", score=0.03),
    ]
    reranked = [
        _result(
            "filtered-b",
            score=0.97,
            metadata={"rerank": {"provider": "fake"}},
        )
    ]
    query_processor = Mock()
    query_processor.process.return_value = processed
    hybrid_search = Mock()
    hybrid_search.search.return_value = SimpleNamespace(
        dense_results=[],
        sparse_results=[],
        fused_results=list(filtered),
        results=list(filtered),
        fallback_used=False,
    )
    rerank_controller = Mock()
    rerank_controller.rerank_with_outcome.return_value = RerankOutcome(
        results=reranked,
        fallback_used=False,
        fallback_reason=None,
    )
    response_builder = Mock()
    response_builder.build.return_value = KnowledgeHubResponse(
        content="[1] Reranked",
        citations=(),
        images=(),
        trace_id="query-runtime-rerank",
        is_empty=False,
    )
    runtime = query_module.QueryRuntime(
        query_processor=query_processor,
        hybrid_search=hybrid_search,
        rerank_controller=rerank_controller,
        response_builder=response_builder,
    )

    execution = runtime.execute(
        "无线耳机怎么选",
        collection="shopping_guides",
        top_k=1,
        no_rerank=False,
        trace_id="query-runtime-rerank",
    )

    rerank_controller.rerank_with_outcome.assert_called_once_with(
        processed.normalized_query,
        filtered,
        top_k=1,
        trace_context=ANY,
    )
    response_builder.build.assert_called_once_with(
        reranked,
        trace_id="query-runtime-rerank",
        query=processed.normalized_query,
    )
    assert execution.final_results == tuple(reranked)
    assert execution.rerank_applied is True
    assert execution.fallback_used is False



def test_query_runtime_applies_self_rag_before_response_construction() -> None:
    """Gate reranked candidates through Self-RAG before building public context."""

    processed = _processed_query().model_copy(update={"top_k": 2})
    filtered = [
        _result("filtered-a", score=0.04),
        _result("filtered-b", score=0.03),
    ]
    reranked = [
        _result("filtered-b", score=0.64),
        _result("filtered-a", score=0.21),
    ]
    selected = [reranked[0].model_copy(deep=True)]
    query_processor = Mock()
    query_processor.process.return_value = processed
    hybrid_search = Mock()
    hybrid_search.search.return_value = SimpleNamespace(
        dense_results=[],
        sparse_results=[],
        fused_results=list(filtered),
        results=list(filtered),
        fallback_used=False,
    )
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
    response_builder = Mock()
    response_builder.build.return_value = KnowledgeHubResponse(
        content="[1] Selected",
        citations=(),
        images=(),
        trace_id="query-runtime-self-rag",
        is_empty=False,
    )
    runtime = query_module.QueryRuntime(
        query_processor=query_processor,
        hybrid_search=hybrid_search,
        rerank_controller=rerank_controller,
        self_rag_controller=self_rag_controller,
        response_builder=response_builder,
    )

    execution = runtime.execute(
        "无线耳机怎么选",
        collection="shopping_guides",
        top_k=2,
        no_rerank=False,
        trace_id="query-runtime-self-rag",
    )

    self_rag_controller.evaluate.assert_called_once_with(
        processed.normalized_query,
        reranked,
        trace_context=ANY,
    )
    response_builder.build.assert_called_once_with(
        selected,
        trace_id="query-runtime-self-rag",
        query=processed.normalized_query,
    )
    assert execution.final_results == tuple(selected)
    assert execution.self_rag_decision is self_rag_controller.evaluate.return_value
    assert execution.fallback_used is False


def test_build_runtime_does_not_construct_reranker_when_explicitly_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid model-backed reranker initialization for ``--no-rerank``."""

    settings = _settings()
    settings.rerank = SimpleNamespace(enabled=True)
    settings.ingestion = SimpleNamespace(image_dir="data/images")
    embedding = Mock()
    vector_store = Mock()
    embedding_create = Mock(return_value=embedding)
    vector_store_create = Mock(return_value=vector_store)
    reranker_create = Mock(side_effect=AssertionError("must not construct reranker"))
    llm_create = Mock(side_effect=AssertionError("must not construct LLM"))
    monkeypatch.setattr(query_module.EmbeddingFactory, "create", embedding_create)
    monkeypatch.setattr(
        query_module.VectorStoreFactory,
        "create",
        vector_store_create,
    )
    monkeypatch.setattr(query_module.RerankerFactory, "create", reranker_create)
    monkeypatch.setattr(query_module.LLMFactory, "create", llm_create)
    pool = Mock()

    runtime = query_module._build_runtime(settings, pool, no_rerank=True)

    assert isinstance(runtime, query_module.QueryRuntime)
    embedding_create.assert_called_once_with(settings=settings)
    vector_store_create.assert_called_once_with(settings=settings, pool=pool)
    reranker_create.assert_not_called()
    llm_create.assert_not_called()



class FakeAsyncCollectionRunner:
    """Serve deterministic async per-collection retrieval fixtures to I4 tests."""

    def __init__(
        self,
        results_by_collection: dict[str, list[RetrievalResult]],
        *,
        delays: dict[str, float] | None = None,
        failures: set[str] | None = None,
    ) -> None:
        """Store async fixtures and initialize the call log."""

        self.results_by_collection = results_by_collection
        self.delays = dict(delays or {})
        self.failures = set(failures or set())
        self.calls: list[dict[str, object]] = []
        self.started: list[str] = []
        self.completed: list[str] = []

    async def run_collection(
        self,
        query: ProcessedQuery,
        *,
        collection: str,
        top_k: int,
        no_rerank: bool,
        routing_score: float,
        routing_reason: str | None,
    ) -> AsyncCollectionRetrievalResult:
        """Return configured collection results after an optional async delay."""

        self.started.append(collection)
        self.calls.append(
            {
                "query": query.normalized_query,
                "collection": collection,
                "top_k": top_k,
                "no_rerank": no_rerank,
                "routing_score": routing_score,
                "routing_reason": routing_reason,
            }
        )
        await asyncio.sleep(self.delays.get(collection, 0.0))
        if collection in self.failures:
            raise RetrievalError("collection failed")
        self.completed.append(collection)
        candidates = list(self.results_by_collection.get(collection, []))[:top_k]
        return AsyncCollectionRetrievalResult(
            collection=collection,
            results=candidates,
            dense_results=candidates[:1],
            sparse_results=candidates[-1:] if candidates else [],
            fused_results=candidates,
            filtered_results=candidates,
            rerank_results=candidates,
            fallback_used=False,
            rerank_applied=not no_rerank,
            duration_ms=1.0,
        )
@pytest.mark.asyncio
async def test_parallel_retrieval_runs_collections_concurrently_and_merges_metadata() -> None:
    """Require I4 controller to run routed collections concurrently and merge once."""

    processed = _processed_query().model_copy(update={"top_k": 2})
    runner = FakeAsyncCollectionRunner(
        {
            "faq": [
                _result("faq-a", score=0.91, metadata={"collection": "faq"}),
                _result("faq-b", score=0.72, metadata={"collection": "faq"}),
            ],
            "policies": [
                _result("policy-a", score=0.88, metadata={"collection": "policies"}),
            ],
        },
        delays={"faq": 0.05, "policies": 0.05},
    )
    trace = FakeParallelTrace()
    controller = AsyncParallelRetrievalController(
        collection_runner=runner.run_collection,
        max_collections=3,
        max_concurrency=2,
        per_collection_timeout_seconds=1,
    )

    started = asyncio.get_running_loop().time()
    result = await controller.search(
        processed,
        collections=("faq", "policies"),
        routing_scores={"faq": 0.91, "policies": 0.86},
        routing_reasons={"faq": "semantic_profile", "policies": "rule_match"},
        top_k=2,
        no_rerank=False,
        trace_context=trace,
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert isinstance(result, ParallelRetrievalResult)
    assert elapsed < 0.09
    assert set(runner.started) == {"faq", "policies"}
    assert [candidate.chunk_id for candidate in result.results] == ["faq-a", "faq-b"]
    assert result.partial_failure_count == 0
    assert [row["collection"] for row in result.collection_results] == ["faq", "policies"]
    assert [row["status"] for row in result.collection_results] == ["success", "success"]
    assert [row["candidate_count"] for row in result.collection_results] == [2, 1]
    assert [row["routing_reason"] for row in result.collection_results] == [
        "semantic_profile",
        "rule_match",
    ]
    first_metadata = result.results[0].metadata
    assert first_metadata["collection"] == "faq"
    assert first_metadata["collection_rank"] == 1
    assert first_metadata["routing_score"] == 0.91
    assert first_metadata["merge_reason"] == "routing_score_rrf_fallback"
    assert isinstance(first_metadata["merge_score"], float)
    assert trace.stages[0]["stage"] == "fusion"
    assert trace.stages[0]["details"]["selected_collections"] == ["faq", "policies"]
    assert trace.stages[0]["details"]["merged_candidate_count"] == 3


@pytest.mark.asyncio
async def test_parallel_retrieval_keeps_successes_and_reports_timeout_failures() -> None:
    """Allow partial collection failures and timeouts without losing successes."""

    runner = FakeAsyncCollectionRunner(
        {"faq": [_result("faq-a", score=0.82, metadata={"collection": "faq"})]},
        delays={"slow": 0.2},
        failures={"broken"},
    )
    trace = FakeParallelTrace()
    controller = AsyncParallelRetrievalController(
        collection_runner=runner.run_collection,
        max_collections=3,
        max_concurrency=3,
        per_collection_timeout_seconds=0.05,
    )

    result = await controller.search(
        _processed_query(),
        collections=("broken", "slow", "faq"),
        routing_scores={"broken": 0.94, "slow": 0.9, "faq": 0.83},
        top_k=3,
        no_rerank=True,
        trace_context=trace,
    )

    assert [candidate.chunk_id for candidate in result.results] == ["faq-a"]
    assert result.partial_failure_count == 2
    assert result.collection_results[0]["status"] == "failed"
    assert result.collection_results[0]["error_type"] == "RetrievalError"
    assert result.collection_results[1]["status"] == "timeout"
    assert result.collection_results[1]["error_type"] == "TimeoutError"
    assert result.collection_results[2]["status"] == "success"
    assert trace.stages[0]["status"] == "degraded"
    assert trace.stages[0]["details"]["partial_failure_count"] == 2


@pytest.mark.asyncio
async def test_parallel_retrieval_trace_failure_does_not_break_results() -> None:
    """Keep retrieval low-intrusion when aggregate trace recording fails."""

    runner = FakeAsyncCollectionRunner(
        {"faq": [_result("faq-a", score=0.82, metadata={"collection": "faq"})]}
    )
    controller = AsyncParallelRetrievalController(
        collection_runner=runner.run_collection,
        max_collections=1,
        max_concurrency=1,
        per_collection_timeout_seconds=1,
    )

    result = await controller.search(
        _processed_query(),
        collections=("faq",),
        routing_scores={"faq": 0.83},
        top_k=3,
        no_rerank=True,
        trace_context=_FailingTraceSink(),
    )

    assert [candidate.chunk_id for candidate in result.results] == ["faq-a"]
    assert result.partial_failure_count == 0


@pytest.mark.asyncio
async def test_parallel_retrieval_reports_all_failed_without_results() -> None:
    """Return an empty merged list when every collection fails or times out."""

    runner = FakeAsyncCollectionRunner({}, failures={"broken"})
    controller = AsyncParallelRetrievalController(
        collection_runner=runner.run_collection,
        max_collections=2,
        max_concurrency=2,
        per_collection_timeout_seconds=1,
    )

    result = await controller.search(
        _processed_query(),
        collections=("broken",),
        routing_scores={"broken": 0.94},
        top_k=3,
        no_rerank=True,
    )

    assert result.results == []
    assert result.partial_failure_count == 1
    assert result.collection_results[0]["status"] == "failed"
