"""Protect query preprocessing contracts used by every retrieval route.

D1 establishes the stable ``ProcessedQuery`` object consumed by Dense, Sparse,
Hybrid, trace, and local CLI components. These tests define normalization,
intent classification, keyword extraction, settings defaults, caller
overrides, and optional rewrite fallback without invoking an external LLM.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from src.core.errors import RetrievalError
from src.core.query_engine.dense_route import DenseRoute
from src.core.query_engine.query_processor import (
    ProcessedQuery,
    QueryIntent,
    QueryProcessor,
)
from src.core.query_engine.sparse_route import SparseRoute
from src.core.types import Chunk


def _settings(*, rewrite_enabled: bool = True) -> SimpleNamespace:
    """Build the minimal settings shape consumed by ``QueryProcessor``."""

    return SimpleNamespace(
        retrieval=SimpleNamespace(
            query_rewrite_enabled=rewrite_enabled,
            dense_top_k=30,
            sparse_top_k=25,
            final_top_k=5,
            filters=SimpleNamespace(default_collection="shopping_guides"),
        )
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
        metadata=dict(metadata or {}),
        chunk_index=0,
        start_offset=0,
        end_offset=max(len(text), 1),
        source_ref={"document_id": "doc-1"},
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


@pytest.mark.parametrize(
    ("query", "expected_intent", "requires_product_tool"),
    [
        ("帮我对比这两款无线耳机", QueryIntent.COMPARISON, True),
        ("推荐一款高性价比无线耳机并给我商品链接", QueryIntent.RECOMMENDATION, True),
        ("主动降噪耳机的原理是什么", QueryIntent.KNOWLEDGE_QUERY, False),
        ("这款耳机现在多少钱还有库存吗", QueryIntent.PRODUCT_LOOKUP, True),
    ],
)
def test_query_processor_classifies_shopping_intent_and_tool_coordination(
    query: str,
    expected_intent: QueryIntent,
    requires_product_tool: bool,
) -> None:
    """Require deterministic intent labels to drive RAG and product-tool routing."""

    result = QueryProcessor(settings=_settings(rewrite_enabled=False)).process(query)

    assert result.intent is expected_intent
    assert result.requires_product_tool is requires_product_tool


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
        intent=QueryIntent.RECOMMENDATION,
        collection="shopping_guides",
        top_k=5,
        requires_product_tool=True,
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
    assert success_call.kwargs["details"] == {"top_k": 30}
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

    bm25_indexer.query.assert_called_once_with(list(processed.keywords), top_k=25)
    vector_store.get_by_ids.assert_called_once_with(["chunk-b", "chunk-a"])
    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-a"]
    assert [result.score for result in results] == [2.4, 1.7]
    assert results[0].text == "预算有限时关注蓝牙稳定性。"
    assert results[0].metadata == {"collection": "shopping_guides"}


def test_sparse_route_processes_raw_query_and_allows_top_k_override() -> None:
    """Require raw strings to pass through QueryProcessor before BM25 retrieval."""

    settings = _settings(rewrite_enabled=False)
    processor = Mock()
    processor.process.return_value = ProcessedQuery(
        raw_query="耳机推荐",
        normalized_query="耳机推荐",
        keywords=("耳机", "推荐"),
        intent=QueryIntent.RECOMMENDATION,
        collection="shopping_guides",
        top_k=5,
        requires_product_tool=True,
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
    bm25_indexer.query.assert_called_once_with(["耳机", "推荐"], top_k=7)
    vector_store.get_by_ids.assert_not_called()


def test_sparse_route_skips_empty_keywords_before_provider_calls() -> None:
    """Require empty keyword snapshots to return no Sparse candidates."""

    settings = _settings(rewrite_enabled=False)
    processed = ProcessedQuery(
        raw_query="?",
        normalized_query="?",
        keywords=(),
        intent=QueryIntent.KNOWLEDGE_QUERY,
        collection="shopping_guides",
        top_k=5,
        requires_product_tool=False,
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
        intent=QueryIntent.RECOMMENDATION,
        collection="shopping_guides",
        top_k=5,
        requires_product_tool=True,
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
