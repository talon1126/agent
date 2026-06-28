"""Unit tests for query intent routing before hybrid retrieval.

D2 keeps business routing separate from ``QueryProcessor``. These tests protect
rule-based routing, semantic profile caching, LLM fallback behavior, and the
trace payload that later query stages use for observability.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.query_engine.intent_router import (
    CollectionProfile,
    IntentRoute,
    IntentRouter,
    IntentRule,
    load_collection_profiles,
    load_intent_rules,
)
from src.core.query_engine.query_processor import ProcessedQuery


def _processed(query: str, *, collection: str = "shopping_guides") -> ProcessedQuery:
    """Build the minimal processed query accepted by ``IntentRouter``."""

    return ProcessedQuery(
        raw_query=query,
        normalized_query=query,
        keywords=tuple(query.split()),
        collection=collection,
        top_k=5,
    )


class FakeEmbeddingClient:
    """Return deterministic tiny vectors for profile and query embedding tests."""

    provider = "fake_embedding"
    model = "fake-model"

    def __init__(self) -> None:
        """Track every text sent through the semantic profile path."""

        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        """Return a vector with manual queries close to the manual profile."""

        self.calls.append(text)
        if "客服" in text or "投诉" in text or "回复" in text:
            return [1.0, 0.0, 0.0]
        if "退货" in text or "退款" in text:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


class FakeProfileRepository:
    """In-memory repository that exposes profile cache refresh behavior."""

    def __init__(self, cached: dict[tuple[str, str], dict[str, object]] | None = None) -> None:
        """Store optional cached profile rows keyed by collection/profile name."""

        self.cached = dict(cached or {})
        self.upserts: list[dict[str, object]] = []

    def get_profile_embedding(self, collection: str, profile_name: str) -> dict[str, object] | None:
        """Return one cached profile row if present."""

        return self.cached.get((collection, profile_name))

    def upsert_profile_embedding(
        self,
        *,
        collection: str,
        profile_name: str,
        profile_text: str,
        content_hash: str,
        embedding: list[float],
        provider: str | None,
        model: str | None,
    ) -> None:
        """Persist a refreshed embedding and expose it for assertions."""

        payload = {
            "collection": collection,
            "profile_name": profile_name,
            "profile_text": profile_text,
            "content_hash": content_hash,
            "embedding": list(embedding),
            "provider": provider,
            "model": model,
        }
        self.upserts.append(payload)
        self.cached[(collection, profile_name)] = payload


class FakeLlmRouter:
    """Return a configured fallback route without calling an external model."""

    def __init__(self, route: IntentRoute) -> None:
        """Store the route that should be returned by the fake LLM path."""

        self.route = route
        self.calls: list[str] = []

    def route_intent(self, query: str, processed_query: ProcessedQuery) -> IntentRoute:
        """Record the query and return the configured route."""

        self.calls.append(query)
        return self.route


def test_load_intent_rules_validates_and_compiles_route_config(tmp_path: Path) -> None:
    """Rule config should be versioned data, not hard-coded Python branches."""

    route_path = tmp_path / "intent_routes.yaml"
    route_path.write_text(
        """
version: "1.0"
routers:
  support:
    categories:
      aftersales:
        intents:
          return_exchange:
            collection: policies
            priority: 100
            confidence: 0.94
            match:
              any: [退货, 退款]
              all: []
              regex:
                - ".*多久.*退款.*"
""".strip(),
        encoding="utf-8",
    )

    rules = load_intent_rules(route_path)

    assert rules == (
        IntentRule(
            name="support_aftersales_return_exchange",
            collection="policies",
            domain="support",
            category="aftersales",
            intent="return_exchange",
            domain_intent="support.aftersales.return_exchange",
            priority=100,
            confidence=0.94,
            any_terms=("退货", "退款"),
            all_terms=(),
            regex_patterns=(".*多久.*退款.*",),
        ),
    )


def test_rule_router_uses_confidence_match_strength_and_priority() -> None:
    """Strong policy matches should outrank broader buying-guide matches."""

    router = IntentRouter(
        rules=(
            IntentRule(
                name="shopping_recommendation",
                collection="shopping_guides",
                domain="support",
                category="presale",
                intent="buying_recommendation",
                domain_intent="support.presale.buying_recommendation",
                priority=70,
                confidence=0.82,
                any_terms=("推荐", "防晒霜"),
            ),
            IntentRule(
                name="policies_after_sales",
                collection="policies",
                domain="support",
                category="aftersales",
                intent="return_exchange",
                domain_intent="support.aftersales.return_exchange",
                priority=100,
                confidence=0.94,
                any_terms=("退货",),
                regex_patterns=(".*可以.*退货.*",),
            ),
        ),
        profiles=(),
        default_collection="shopping_guides",
    )

    route = router.route(
        "防晒霜过敏可以退货吗？",
        _processed("防晒霜过敏可以退货吗？"),
    )

    assert route.collection == "policies"
    assert route.method == "rules"
    assert route.matched_rule == "policies_after_sales"
    assert route.matched_terms == ("退货",)
    assert route.matched_regex == (".*可以.*退货.*",)
    assert route.confidence == pytest.approx(0.99)


def test_semantic_profile_reuses_cached_embedding_without_reembedding() -> None:
    """Unchanged collection profiles should load from PostgreSQL cache."""

    profile = CollectionProfile(
        collection="manual",
        profile_name="default",
        description="客服话术、投诉处理、催发货、安抚解释",
        examples=("用户投诉发货慢怎么回复",),
    )
    cached_hash = profile.content_hash
    repository = FakeProfileRepository(
        {
            ("manual", "default"): {
                "content_hash": cached_hash,
                "embedding": [1.0, 0.0, 0.0],
                "provider": "fake_embedding",
                "model": "fake-model",
            }
        }
    )
    embedding = FakeEmbeddingClient()
    router = IntentRouter(
        rules=(),
        profiles=(profile,),
        default_collection="shopping_guides",
        embedding_client=embedding,
        profile_repository=repository,
        semantic_threshold=0.7,
    )

    route = router.route("客户投诉发货慢我怎么回复", _processed("客户投诉发货慢我怎么回复"))

    assert route.collection == "manual"
    assert route.method == "semantic_profile"
    assert route.confidence == pytest.approx(0.99)
    assert embedding.calls == ["客户投诉发货慢我怎么回复"]
    assert repository.upserts == []


def test_semantic_profile_refreshes_changed_profile_embedding() -> None:
    """Changed profile text should be embedded once and persisted for reuse."""

    profile = CollectionProfile(
        collection="manual",
        profile_name="default",
        description="客服话术、投诉处理、催退款",
        examples=("用户催退款怎么回复",),
    )
    repository = FakeProfileRepository()
    embedding = FakeEmbeddingClient()

    IntentRouter(
        rules=(),
        profiles=(profile,),
        default_collection="shopping_guides",
        embedding_client=embedding,
        profile_repository=repository,
    )

    assert embedding.calls == [profile.profile_text]
    assert repository.upserts[0]["collection"] == "manual"
    assert repository.upserts[0]["content_hash"] == profile.content_hash


def test_router_uses_llm_fallback_before_safe_default() -> None:
    """Low-confidence rule/profile misses may use the configured LLM router."""

    llm_route = IntentRoute(
        collection="faq",
        collections=("faq",),
        domain=None,
        category=None,
        intent=None,
        domain_intent="faq_query",
        complexity="medium",
        retrieval_strategy="hybrid",
        confidence=0.76,
        method="llm_fallback",
        reason="LLM selected FAQ",
        fallback_used=True,
    )
    llm = FakeLlmRouter(llm_route)
    router = IntentRouter(
        rules=(),
        profiles=(),
        default_collection="shopping_guides",
        llm_router=llm,
    )

    route = router.route("这个报错是什么意思", _processed("这个报错是什么意思"))

    assert route == llm_route
    assert llm.calls == ["这个报错是什么意思"]


def test_router_returns_safe_default_when_no_strategy_matches() -> None:
    """Routing must degrade predictably when no optional strategy is available."""

    router = IntentRouter(
        rules=(),
        profiles=(),
        default_collection="shopping_guides",
    )

    route = router.route("随便问一句", _processed("随便问一句"))

    assert route.collection == "shopping_guides"
    assert route.method == "default"
    assert route.fallback_used is True
    assert route.to_trace_details()["fallback_used"] is True
    assert route.to_trace_details()["domain"] is None
    assert route.to_trace_details()["category"] is None
    assert route.to_trace_details()["intent"] is None


def test_tree_route_trace_details_include_intent_node() -> None:
    """Rule routes should expose the selected tree node to query trace."""

    router = IntentRouter(
        rules=(
            IntentRule(
                name="support_usage_troubleshooting",
                collection="faq",
                domain="support",
                category="usage",
                intent="troubleshooting",
                domain_intent="support.usage.troubleshooting",
                priority=80,
                confidence=0.86,
                any_terms=("加热不均匀",),
            ),
        ),
        profiles=(),
        default_collection="shopping_guides",
    )

    route = router.route(
        "微波炉加热不均匀 外面热中间冷 原因",
        _processed("微波炉加热不均匀 外面热中间冷 原因"),
    )

    assert route.collection == "faq"
    assert route.domain == "support"
    assert route.category == "usage"
    assert route.intent == "troubleshooting"
    assert route.domain_intent == "support.usage.troubleshooting"
    assert route.to_trace_details()["domain"] == "support"
    assert route.to_trace_details()["category"] == "usage"
    assert route.to_trace_details()["intent"] == "troubleshooting"


def test_tree_route_trace_details_include_direct_reply_metadata() -> None:
    """Chat routes should expose that callers can bypass RAG retrieval."""

    router = IntentRouter(
        rules=(
            IntentRule(
                name="chat_chat_all_greeting",
                collection="shopping_guides",
                domain="chat",
                category="chat_all",
                intent="greeting",
                domain_intent="chat.chat_all.greeting",
                priority=200,
                confidence=0.95,
                any_terms=("你好",),
                rag_enabled=False,
                action="direct_llm_reply",
            ),
        ),
        profiles=(),
        default_collection="shopping_guides",
    )

    route = router.route("你好", _processed("你好"))

    assert route.domain == "chat"
    assert route.category == "chat_all"
    assert route.intent == "greeting"
    assert route.rag_enabled is False
    assert route.action == "direct_llm_reply"
    assert route.to_trace_details()["rag_enabled"] is False
    assert route.to_trace_details()["action"] == "direct_llm_reply"



def test_load_collection_profiles_builds_aggregated_profile_text(tmp_path: Path) -> None:
    """Profile config should produce one hashable text payload per collection."""

    profile_path = tmp_path / "collection_profiles.yaml"
    profile_path.write_text(
        """
version: "1.0"
profiles:
  shopping_guides:
    description: 商品选购指南、品牌型号对比、参数解释、购买建议
    examples:
      - 空调怎么选
      - 微波炉哪个品牌适合家用
""".strip(),
        encoding="utf-8",
    )

    profiles = load_collection_profiles(profile_path)

    assert profiles[0].collection == "shopping_guides"
    assert "商品选购指南" in profiles[0].profile_text
    assert "空调怎么选" in profiles[0].profile_text
    assert len(profiles[0].content_hash) == 64
