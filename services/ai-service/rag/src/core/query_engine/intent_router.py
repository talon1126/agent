"""Route processed user questions to the most appropriate RAG collection.

``IntentRouter`` is the business-routing layer that runs after
``QueryProcessor`` and before hybrid retrieval. It keeps deterministic rule
matching, collection semantic profiles, and optional LLM fallback outside the
query-preprocessing contract so Dense and Sparse routes continue to consume a
stable ``ProcessedQuery``.

The router does not query chunks, build responses, or force AImodel to call RAG.
It returns trace-safe routing metadata that composition roots can record in the
``intent_routing`` query trace stage and use as input for collection filters.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

import yaml

from src.core.query_engine.query_processor import ProcessedQuery


class ProfileEmbeddingRepository(Protocol):
    """Minimal persistence boundary for collection profile embeddings."""

    def get_profile_embedding(
        self,
        collection: str,
        profile_name: str,
    ) -> Mapping[str, Any] | None:
        """Return one cached profile embedding row, or ``None`` when absent."""

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
        """Persist a refreshed profile embedding cache row."""


class TextEmbeddingClient(Protocol):
    """Minimal embedding boundary used by semantic profile routing."""

    provider: str
    model: str

    def embed(self, text: str) -> list[float]:
        """Return a dense vector for query or profile text."""


class LlmIntentRouter(Protocol):
    """Optional fallback boundary for model-based intent routing."""

    def route_intent(self, query: str, processed_query: ProcessedQuery) -> IntentRoute:
        """Return a route selected by an LLM fallback implementation."""


@dataclass(frozen=True, slots=True)
class IntentRule:
    """Represent one configured deterministic routing rule.

    Args:
        name: Stable rule identifier written to trace details.
        collection: Target collection selected when this rule wins.
        domain_intent: Business intent label for diagnostics.
        priority: Human-controlled conflict weight. It only acts as a small
            tie-breaker through ``priority / 1000``.
        confidence: Base confidence assigned when the rule matches.
        any_terms: Terms where any match contributes a weak signal.
        all_terms: Terms that must all be present to contribute a stronger
            grouped signal.
        regex_patterns: Regular expressions that describe sentence patterns.
    """

    name: str
    collection: str
    domain: str
    category: str
    intent: str
    domain_intent: str
    priority: int
    confidence: float
    any_terms: tuple[str, ...] = ()
    all_terms: tuple[str, ...] = ()
    regex_patterns: tuple[str, ...] = ()
    rag_enabled: bool = True
    action: str = "retrieve"

    def compiled_regex(self) -> tuple[re.Pattern[str], ...]:
        """Compile configured regular expressions on demand."""

        return tuple(re.compile(pattern, flags=re.IGNORECASE) for pattern in self.regex_patterns)


@dataclass(frozen=True, slots=True)
class CollectionProfile:
    """Represent one hashable semantic profile for a collection."""

    collection: str
    profile_name: str
    description: str
    examples: tuple[str, ...]

    @property
    def profile_text(self) -> str:
        """Return the aggregated text embedded for this collection profile."""

        parts = [self.description.strip(), *[example.strip() for example in self.examples]]
        return "\n".join(part for part in parts if part)

    @property
    def content_hash(self) -> str:
        """Return a SHA256 hash that detects profile text changes."""

        return sha256(self.profile_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _RuleMatch:
    """Internal score payload for one matched rule."""

    rule: IntentRule
    score: float
    matched_terms: tuple[str, ...]
    matched_regex: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ProfileVector:
    """Internal in-memory vector attached to one collection profile."""

    profile: CollectionProfile
    embedding: tuple[float, ...]
    provider: str | None
    model: str | None


@dataclass(frozen=True, slots=True)
class IntentRoute:
    """Represent the routing result consumed by query runtime and trace.

    Args:
        collection: Primary collection used for retrieval filtering.
        collections: Ordered candidate collections considered by the route.
        domain_intent: Business intent label such as ``policy_query``.
        complexity: Routing-level difficulty label for diagnostics.
        retrieval_strategy: Suggested retrieval mode, typically ``hybrid``.
        confidence: Final route confidence in the inclusive range ``0..1``.
        method: Strategy that produced the route: rules, semantic_profile,
            llm_fallback, or default.
        reason: Human-readable trace-safe explanation.
        fallback_used: Whether a fallback strategy was needed.
        matched_rule: Winning rule name when method is ``rules``.
        matched_terms: Terms that contributed to rule scoring.
        matched_regex: Regex patterns that contributed to rule scoring.
        provider: Optional provider name for semantic or LLM fallback routes.
        model: Optional model name for semantic or LLM fallback routes.
    """

    collection: str
    collections: tuple[str, ...]
    domain_intent: str
    complexity: str
    retrieval_strategy: str
    confidence: float
    method: str
    reason: str
    fallback_used: bool = False
    domain: str | None = None
    category: str | None = None
    intent: str | None = None
    matched_rule: str | None = None
    matched_terms: tuple[str, ...] = ()
    matched_regex: tuple[str, ...] = ()
    provider: str | None = None
    model: str | None = None
    rag_enabled: bool = True
    action: str = "retrieve"

    def to_trace_details(self) -> dict[str, Any]:
        """Return the JSON-safe payload persisted in ``intent_routing``."""

        return {
            "collection": self.collection,
            "collections": list(self.collections),
            "domain": self.domain,
            "category": self.category,
            "intent": self.intent,
            "domain_intent": self.domain_intent,
            "complexity": self.complexity,
            "retrieval_strategy": self.retrieval_strategy,
            "confidence": self.confidence,
            "method": self.method,
            "reason": self.reason,
            "fallback_used": self.fallback_used,
            "matched_rule": self.matched_rule,
            "matched_terms": list(self.matched_terms),
            "matched_regex": list(self.matched_regex),
            "provider": self.provider,
            "model": self.model,
            "rag_enabled": self.rag_enabled,
            "action": self.action,
        }


class IntentRouter:
    """Choose the collection and strategy for one processed query."""

    def __init__(
        self,
        *,
        rules: Sequence[IntentRule],
        profiles: Sequence[CollectionProfile],
        default_collection: str,
        embedding_client: TextEmbeddingClient | None = None,
        profile_repository: ProfileEmbeddingRepository | None = None,
        llm_router: LlmIntentRouter | None = None,
        rule_threshold: float = 0.75,
        semantic_threshold: float = 0.7,
    ) -> None:
        """Load deterministic rules and optional semantic profile vectors.

        Args:
            rules: Ordered rules loaded from ``intent_routes.yaml``.
            profiles: Collection profiles loaded from ``collection_profiles.yaml``.
            default_collection: Safe collection used when every strategy misses.
            embedding_client: Optional embedding provider for semantic profiles.
            profile_repository: Optional persistent cache for profile vectors.
            llm_router: Optional model-based fallback strategy.
            rule_threshold: Minimum rule score required to accept rule routing.
            semantic_threshold: Minimum cosine similarity required for profile
                routing.
        """

        self._rules = tuple(rules)
        self._profiles = tuple(profiles)
        self._default_collection = _non_blank(default_collection, "default_collection")
        self._embedding_client = embedding_client
        self._profile_repository = profile_repository
        self._llm_router = llm_router
        self._rule_threshold = _validate_threshold(rule_threshold, "rule_threshold")
        self._semantic_threshold = _validate_threshold(semantic_threshold, "semantic_threshold")
        self._profile_vectors = self._load_profile_vectors()

    def route(self, query: str, processed_query: ProcessedQuery) -> IntentRoute:
        """Route one query through rules, semantic profiles, LLM, then default.

        Args:
            query: Raw or normalized query text. The router normalizes only for
                local matching and never mutates ``ProcessedQuery``.
            processed_query: Stable preprocessing result from ``QueryProcessor``.

        Returns:
            IntentRoute describing the selected collection and routing evidence.
        """

        normalized_query = _normalize_for_matching(processed_query.normalized_query or query)
        rule_route = self._route_by_rules(normalized_query)
        if rule_route is not None:
            return rule_route
        semantic_route = self._route_by_semantic_profile(normalized_query)
        if semantic_route is not None:
            return semantic_route
        if self._llm_router is not None:
            try:
                return self._llm_router.route_intent(normalized_query, processed_query)
            except Exception as error:
                return self._default_route(reason=f"llm_fallback_error:{type(error).__name__}")
        return self._default_route(reason="no_route_matched")

    def _route_by_rules(self, normalized_query: str) -> IntentRoute | None:
        """Return the highest-scoring rule route above threshold."""

        matches = [
            match
            for rule in self._rules
            if (match := self._score_rule_match(rule, normalized_query))
        ]
        if not matches:
            return None
        winner = max(matches, key=lambda match: (match.score, match.rule.priority, match.rule.name))
        if winner.score < self._rule_threshold:
            return None
        rule = winner.rule
        return IntentRoute(
            collection=rule.collection,
            collections=(rule.collection,),
            domain=rule.domain,
            category=rule.category,
            intent=rule.intent,
            domain_intent=rule.domain_intent,
            complexity="simple",
            retrieval_strategy="hybrid",
            confidence=round(min(0.99, winner.score), 4),
            method="rules",
            reason=f"matched rule {rule.name}",
            matched_rule=rule.name,
            matched_terms=winner.matched_terms,
            matched_regex=winner.matched_regex,
            rag_enabled=rule.rag_enabled,
            action=rule.action,
        )

    def _score_rule_match(self, rule: IntentRule, normalized_query: str) -> _RuleMatch | None:
        """Score one rule using configured confidence and match strength."""

        matched_any = tuple(term for term in rule.any_terms if term and term in normalized_query)
        all_group_matched = bool(rule.all_terms) and all(
            term in normalized_query for term in rule.all_terms
        )
        matched_regex = tuple(
            pattern.pattern
            for pattern in rule.compiled_regex()
            if pattern.search(normalized_query)
        )
        if not matched_any and not all_group_matched and not matched_regex:
            return None
        matched_terms = matched_any + (rule.all_terms if all_group_matched else ())
        score = (
            rule.confidence
            + len(matched_any) * 0.03
            + (0.08 if all_group_matched else 0.0)
            + len(matched_regex) * 0.10
            + rule.priority / 1000
        )
        return _RuleMatch(
            rule=rule,
            score=score,
            matched_terms=tuple(dict.fromkeys(matched_terms)),
            matched_regex=matched_regex,
        )

    def _route_by_semantic_profile(self, normalized_query: str) -> IntentRoute | None:
        """Route by cosine similarity against cached collection profiles."""

        if self._embedding_client is None or not self._profile_vectors:
            return None
        query_vector = tuple(
            float(value) for value in self._embedding_client.embed(normalized_query)
        )
        scored = [
            (_cosine_similarity(query_vector, vector.embedding), vector)
            for vector in self._profile_vectors
        ]
        if not scored:
            return None
        score, winner = max(scored, key=lambda item: item[0])
        if score < self._semantic_threshold:
            return None
        return IntentRoute(
            collection=winner.profile.collection,
            collections=(winner.profile.collection,),
            domain=None,
            category=None,
            intent=None,
            domain_intent=f"{winner.profile.collection}_query",
            complexity="medium",
            retrieval_strategy="hybrid",
            confidence=round(min(0.99, score), 4),
            method="semantic_profile",
            reason=(
                "matched collection profile "
                f"{winner.profile.collection}/{winner.profile.profile_name}"
            ),
            provider=winner.provider,
            model=winner.model,
        )

    def _load_profile_vectors(self) -> tuple[_ProfileVector, ...]:
        """Load cached profile vectors or refresh changed profile embeddings."""

        if self._embedding_client is None:
            return ()
        vectors: list[_ProfileVector] = []
        for profile in self._profiles:
            cached = (
                self._profile_repository.get_profile_embedding(
                    profile.collection,
                    profile.profile_name,
                )
                if self._profile_repository is not None
                else None
            )
            embedding: list[float] | None = None
            provider: str | None = None
            model: str | None = None
            if cached and cached.get("content_hash") == profile.content_hash:
                candidate = cached.get("embedding")
                if isinstance(candidate, list | tuple):
                    embedding = [float(value) for value in candidate]
                    provider = _optional_str(cached.get("provider"))
                    model = _optional_str(cached.get("model"))
            if embedding is None:
                embedding = [
                    float(value)
                    for value in self._embedding_client.embed(profile.profile_text)
                ]
                provider = getattr(self._embedding_client, "provider", None)
                model = getattr(self._embedding_client, "model", None)
                if self._profile_repository is not None:
                    self._profile_repository.upsert_profile_embedding(
                        collection=profile.collection,
                        profile_name=profile.profile_name,
                        profile_text=profile.profile_text,
                        content_hash=profile.content_hash,
                        embedding=embedding,
                        provider=provider,
                        model=model,
                    )
            vectors.append(
                _ProfileVector(
                    profile=profile,
                    embedding=tuple(embedding),
                    provider=provider,
                    model=model,
                )
            )
        return tuple(vectors)

    def _default_route(self, *, reason: str) -> IntentRoute:
        """Return the configured safe default route."""

        return IntentRoute(
            collection=self._default_collection,
            collections=(self._default_collection,),
            domain=None,
            category=None,
            intent=None,
            domain_intent="default_query",
            complexity="unknown",
            retrieval_strategy="hybrid",
            confidence=0.5,
            method="default",
            reason=reason,
            fallback_used=True,
        )


def load_intent_rules(path: str | Path) -> tuple[IntentRule, ...]:
    """Load routing rules from tree YAML and flatten them for execution."""

    data = _load_yaml_mapping(path)
    routers = data.get("routers")
    if isinstance(routers, Mapping):
        return _flatten_intent_router_tree(routers)
    routes = data.get("routes")
    if isinstance(routes, list) and routes:
        return _load_legacy_intent_routes(routes)
    raise ValueError("intent route config must contain non-empty routers mapping")


def _flatten_intent_router_tree(routers: Mapping[str, Any]) -> tuple[IntentRule, ...]:
    """Flatten ``routers.domain.categories.category.intents.intent`` rules."""

    rules: list[IntentRule] = []
    for domain_name, domain_payload in routers.items():
        domain = _non_blank(domain_name, "router.domain")
        if not isinstance(domain_payload, Mapping):
            raise ValueError("intent router domain entries must be mappings")
        categories = domain_payload.get("categories")
        if not isinstance(categories, Mapping) or not categories:
            raise ValueError("intent router domain must contain categories mapping")
        for category_name, category_payload in categories.items():
            category = _non_blank(category_name, "router.category")
            if not isinstance(category_payload, Mapping):
                raise ValueError("intent router category entries must be mappings")
            intents = category_payload.get("intents")
            if not isinstance(intents, Mapping) or not intents:
                raise ValueError("intent router category must contain intents mapping")
            for intent_name, intent_payload in intents.items():
                intent = _non_blank(intent_name, "router.intent")
                if not isinstance(intent_payload, Mapping):
                    raise ValueError("intent router intent entries must be mappings")
                rules.append(_intent_rule_from_tree_node(domain, category, intent, intent_payload))
    if not rules:
        raise ValueError("intent router config must contain at least one intent")
    return tuple(rules)


def _intent_rule_from_tree_node(
    domain: str,
    category: str,
    intent: str,
    payload: Mapping[str, Any],
) -> IntentRule:
    """Build one executable rule from a tree leaf node."""

    match = payload.get("match") or {}
    if not isinstance(match, Mapping):
        raise ValueError("intent route match must be a mapping")
    domain_intent = f"{domain}.{category}.{intent}"
    return IntentRule(
        name=str(payload.get("name") or domain_intent.replace(".", "_")),
        collection=_non_blank(payload.get("collection"), "route.collection"),
        domain=domain,
        category=category,
        intent=intent,
        domain_intent=domain_intent,
        priority=_validate_int(payload.get("priority"), "route.priority"),
        confidence=_validate_threshold(payload.get("confidence"), "route.confidence"),
        any_terms=_string_tuple(match.get("any")),
        all_terms=_string_tuple(match.get("all")),
        regex_patterns=_string_tuple(match.get("regex")),
        rag_enabled=_validate_bool(payload.get("rag_enabled", True), "route.rag_enabled"),
        action=str(payload.get("action") or "retrieve"),
    )


def _load_legacy_intent_routes(routes: list[Any]) -> tuple[IntentRule, ...]:
    """Load the previous flat ``routes`` format for compatibility tests."""

    rules: list[IntentRule] = []
    for route in routes:
        if not isinstance(route, Mapping):
            raise ValueError("intent route entries must be mappings")
        match = route.get("match") or {}
        if not isinstance(match, Mapping):
            raise ValueError("intent route match must be a mapping")
        domain_intent = _non_blank(route.get("domain_intent"), "route.domain_intent")
        parts = domain_intent.split(".")
        domain = parts[0] if len(parts) == 3 else "legacy"
        category = parts[1] if len(parts) == 3 else "default"
        intent = parts[2] if len(parts) == 3 else domain_intent
        rules.append(
            IntentRule(
                name=_non_blank(route.get("name"), "route.name"),
                collection=_non_blank(route.get("collection"), "route.collection"),
                domain=domain,
                category=category,
                intent=intent,
                domain_intent=domain_intent,
                priority=_validate_int(route.get("priority"), "route.priority"),
                confidence=_validate_threshold(route.get("confidence"), "route.confidence"),
                any_terms=_string_tuple(match.get("any")),
                all_terms=_string_tuple(match.get("all")),
                regex_patterns=_string_tuple(match.get("regex")),
                rag_enabled=_validate_bool(route.get("rag_enabled", True), "route.rag_enabled"),
                action=str(route.get("action") or "retrieve"),
            )
        )
    return tuple(rules)


def load_collection_profiles(path: str | Path) -> tuple[CollectionProfile, ...]:
    """Load collection semantic profiles from a versioned YAML document."""

    data = _load_yaml_mapping(path)
    profiles = data.get("profiles")
    if not isinstance(profiles, Mapping) or not profiles:
        raise ValueError("collection profile config must contain profiles mapping")
    loaded: list[CollectionProfile] = []
    for collection, payload in profiles.items():
        if not isinstance(payload, Mapping):
            raise ValueError("collection profile entries must be mappings")
        loaded.append(
            CollectionProfile(
                collection=_non_blank(collection, "profile.collection"),
                profile_name=str(payload.get("profile_name") or "default"),
                description=_non_blank(payload.get("description"), "profile.description"),
                examples=_string_tuple(payload.get("examples")),
            )
        )
    return tuple(loaded)


def _load_yaml_mapping(path: str | Path) -> Mapping[str, Any]:
    """Read a YAML file and require a top-level mapping."""

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("YAML document must contain a mapping")
    return data


def _normalize_for_matching(query: str) -> str:
    """Normalize whitespace and case for deterministic local matching."""

    return " ".join(str(query).casefold().split())


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return cosine similarity for two dense vectors."""

    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _validate_bool(value: Any, field_name: str) -> bool:
    """Validate a boolean configuration field."""

    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _validate_threshold(value: Any, field_name: str) -> float:
    """Validate a confidence threshold or base confidence value."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a number")
    normalized = float(value)
    if not 0 <= normalized <= 1:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return normalized


def _validate_int(value: Any, field_name: str) -> int:
    """Validate an integer configuration field."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _string_tuple(value: Any) -> tuple[str, ...]:
    """Convert an optional YAML string list to a non-blank tuple."""

    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError("configured terms must be a list")
    return tuple(_non_blank(item, "configured term") for item in value)


def _non_blank(value: Any, field_name: str) -> str:
    """Return a trimmed non-blank string configuration value."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    """Return a non-empty string or ``None`` for optional provider metadata."""

    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None
