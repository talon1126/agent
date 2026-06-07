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
from src.core.query_engine.query_processor import (
    ProcessedQuery,
    QueryIntent,
    QueryProcessor,
)


def _settings(*, rewrite_enabled: bool = True) -> SimpleNamespace:
    """Build the minimal settings shape consumed by ``QueryProcessor``."""

    return SimpleNamespace(
        retrieval=SimpleNamespace(
            query_rewrite_enabled=rewrite_enabled,
            final_top_k=5,
            filters=SimpleNamespace(default_collection="shopping_guides"),
        )
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
