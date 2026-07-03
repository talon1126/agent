"""Route AImodel user turns to the correct tool action before Agent execution.

This module belongs to the caller-side AImodel orchestration layer. It mirrors
RAG's tree-shaped intent configuration format, but it does not import RAG
internals or query the RAG subsystem. Its job is to turn one user message into a
small, trace-safe routing decision that says whether AImodel should use RAG,
product APIs, web search, a direct answer, or a refusal path.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class AImodelIntentRule:
    """Represent one configured AImodel intent routing rule.

    Args:
        name: Stable rule identifier used by tests and diagnostics.
        domain: Top-level business domain, for example ``support`` or ``chat``.
        category: Domain-local category such as ``presale`` or ``aftersales``.
        intent: Leaf intent name under the category.
        action: Tool action selected by the rule. Supported first-release values
            are ``rag``, ``product_api``, ``web``, ``direct``, and ``refuse``.
        collection: Optional RAG collection. It must be set when ``action`` is
            ``rag`` and should be ``None`` for non-RAG actions.
        priority: Human-controlled conflict weight used as a small tie-breaker.
        confidence: Base confidence assigned when the rule matches.
        any_terms: Terms where any match contributes a weak signal.
        all_terms: Terms that must all appear to contribute a stronger signal.
        regex_patterns: Regular expressions for sentence-shaped intent signals.
        rag_enabled: Whether this route is allowed to call RAG.
    """

    name: str
    domain: str
    category: str
    intent: str
    action: str
    collection: str | None
    priority: int
    confidence: float
    any_terms: tuple[str, ...] = ()
    all_terms: tuple[str, ...] = ()
    regex_patterns: tuple[str, ...] = ()
    rag_enabled: bool = True

    @property
    def domain_intent(self) -> str:
        """Return the compact ``domain.category.intent`` label."""

        return f"{self.domain}.{self.category}.{self.intent}"

    def compiled_regex(self) -> tuple[re.Pattern[str], ...]:
        """Compile configured regular expressions on demand."""

        return tuple(re.compile(pattern, flags=re.IGNORECASE) for pattern in self.regex_patterns)


@dataclass(frozen=True, slots=True)
class AImodelIntentRoute:
    """Represent the tool-routing result consumed by AImodel service code.

    Args:
        action: Selected execution path: ``rag``, ``product_api``, ``web``,
            ``direct``, or ``refuse``.
        collection: Primary target RAG collection for ``rag`` actions.
        collections: Ordered RAG collections selected from high-confidence
            candidates. Multi-collection routes let RAG execute parallel
            retrieval without hardcoding cross-domain YAML routes.
        domain: Top-level configured intent domain.
        category: Domain-local category.
        intent: Leaf intent name.
        confidence: Final route confidence in the inclusive range ``0..1``.
        reason: Short diagnostic explanation safe to log or inspect in tests.
        matched_rule: Winning rule name, when a configured rule matched.
        matched_terms: Terms that contributed to the rule match.
        matched_regex: Regex patterns that contributed to the rule match.
        fallback_used: Whether default routing was used.
        rag_enabled: Whether this route may call RAG.
    """

    action: str
    collection: str | None
    domain: str | None
    category: str | None
    intent: str | None
    confidence: float
    reason: str
    collections: tuple[str, ...] = ()
    matched_rule: str | None = None
    matched_terms: tuple[str, ...] = ()
    matched_regex: tuple[str, ...] = ()
    fallback_used: bool = False
    rag_enabled: bool = True

    @property
    def domain_intent(self) -> str | None:
        """Return ``domain.category.intent`` when the route came from a rule."""

        if self.domain and self.category and self.intent:
            return f"{self.domain}.{self.category}.{self.intent}"
        return None


@dataclass(frozen=True, slots=True)
class _RuleMatch:
    """Internal score payload for one matched AImodel intent rule."""

    rule: AImodelIntentRule
    score: float
    matched_terms: tuple[str, ...]
    matched_regex: tuple[str, ...]


class AImodelIntentRouter:
    """Select AImodel's next tool action for one user message.

    The router deliberately stays lightweight for G10. It executes deterministic
    tree-configured rules and then returns a safe default RAG route when no rule
    matches. Semantic profiles and LLM fallback can be added behind this same
    class without changing the service call site.
    """

    def __init__(
        self,
        *,
        rules: Sequence[AImodelIntentRule],
        default_collection: str,
        rule_threshold: float = 0.75,
    ) -> None:
        """Create a router from preloaded rules.

        Args:
            rules: Rules loaded from ``intent_routes.yaml``.
            default_collection: Collection used by the safe fallback RAG route.
            rule_threshold: Minimum score required to accept a rule route.

        Raises:
            ValueError: If thresholds or default collection are invalid.
        """

        self._rules = tuple(rules)
        self._default_collection = _non_blank(default_collection, "default_collection")
        self._rule_threshold = _validate_threshold(rule_threshold, "rule_threshold")

    def route(self, message: str) -> AImodelIntentRoute:
        """Return the selected action and optional RAG collection.

        Args:
            message: Current user message from ``AiModelChatRequest``.

        Returns:
            A route object that AImodel service can use before invoking tools.
        """

        route, _candidates = self.route_with_candidates(message)
        return route

    def route_with_candidates(
        self,
        message: str,
        *,
        limit: int = 3,
    ) -> tuple[AImodelIntentRoute, list[dict[str, Any]]]:
        """Return the selected route plus top candidate diagnostics.

        Args:
            message: Current user message from ``AiModelChatRequest``.
            limit: Maximum number of candidate rows to return for trace events.

        Returns:
            A tuple containing the selected route and score-sorted candidate
            summaries. Candidates are intended for observability events, while
            the route itself remains the compact execution result.
        """

        normalized_message = _normalize_for_matching(message)
        matches = [
            match
            for rule in self._rules
            if (match := self._score_rule_match(rule, normalized_message))
        ]
        ranked_matches = sorted(
            matches,
            key=lambda match: (match.score, match.rule.priority, match.rule.name),
            reverse=True,
        )
        candidates = _top_candidate_summaries(
            ranked_matches,
            rules=self._rules,
            limit=limit,
        )
        if not ranked_matches:
            return self._default_route("no_rule_matched"), []
        winner = ranked_matches[0]
        if winner.score < self._rule_threshold:
            return self._default_route("rule_score_below_threshold"), candidates
        return self._route_from_match(winner, candidates), candidates

    def _route_from_match(
        self,
        match: _RuleMatch,
        candidates: Sequence[dict[str, Any]],
    ) -> AImodelIntentRoute:
        """Convert a winning rule match into a public route object."""

        rule = match.rule
        selected_collections = select_rag_collections_by_score(candidates)
        primary_collection = rule.collection if rule.action == "rag" else None
        if primary_collection and primary_collection not in selected_collections:
            selected_collections = (primary_collection, *selected_collections)
        return AImodelIntentRoute(
            action=rule.action,
            collection=primary_collection,
            collections=selected_collections if rule.action == "rag" else (),
            domain=rule.domain,
            category=rule.category,
            intent=rule.intent,
            confidence=round(min(0.99, match.score), 4),
            reason=f"matched rule {rule.name}",
            matched_rule=rule.name,
            matched_terms=match.matched_terms,
            matched_regex=match.matched_regex,
            rag_enabled=rule.rag_enabled and rule.action == "rag",
        )

    def _score_rule_match(
        self,
        rule: AImodelIntentRule,
        normalized_message: str,
    ) -> _RuleMatch | None:
        """Score one configured rule against the normalized user message."""

        matched_any = tuple(
            term for term in rule.any_terms if term and term in normalized_message
        )
        all_group_matched = bool(rule.all_terms) and all(
            term in normalized_message for term in rule.all_terms
        )
        matched_regex = tuple(
            pattern.pattern
            for pattern in rule.compiled_regex()
            if pattern.search(normalized_message)
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

    def _default_route(self, reason: str) -> AImodelIntentRoute:
        """Return the safe fallback route for internal knowledge retrieval."""

        return AImodelIntentRoute(
            action="rag",
            collection=self._default_collection,
            collections=(self._default_collection,),
            domain=None,
            category=None,
            intent=None,
            confidence=0.5,
            reason=reason,
            fallback_used=True,
            rag_enabled=True,
        )


def default_intent_routes_path() -> Path:
    """Return the bundled AImodel intent route configuration path."""

    return Path(__file__).with_name("intent_routes.yaml")


def load_aimodel_intent_rules(path: str | Path) -> tuple[AImodelIntentRule, ...]:
    """Load tree-shaped AImodel intent rules from YAML.

    Args:
        path: Filesystem path to a YAML document containing ``routers``.

    Returns:
        Flattened executable intent rules.

    Raises:
        ValueError: If the YAML shape or any rule field is invalid.
    """

    data = _load_yaml_mapping(path)
    routers = data.get("routers")
    if not isinstance(routers, Mapping) or not routers:
        raise ValueError("AImodel intent route config must contain routers mapping")
    return _flatten_intent_router_tree(routers)


def load_aimodel_intent_routes(path: str | Path) -> tuple[AImodelIntentRule, ...]:
    """Load tree-shaped AImodel intent routes from YAML.

    This alias keeps the public function name aligned with the development
    specification while reusing the rule loader implementation.
    """

    return load_aimodel_intent_rules(path)


@lru_cache(maxsize=1)
def load_default_aimodel_intent_router() -> AImodelIntentRouter:
    """Build and cache the default AImodel intent router."""

    return AImodelIntentRouter(
        rules=load_aimodel_intent_routes(default_intent_routes_path()),
        default_collection="shopping_guides",
    )


def _flatten_intent_router_tree(routers: Mapping[str, Any]) -> tuple[AImodelIntentRule, ...]:
    """Flatten ``routers.domain.categories.category.intents.intent`` rules."""

    rules: list[AImodelIntentRule] = []
    for domain_name, domain_payload in routers.items():
        domain = _non_blank(domain_name, "router.domain")
        if not isinstance(domain_payload, Mapping):
            raise ValueError("AImodel intent router domain entries must be mappings")
        categories = domain_payload.get("categories")
        if not isinstance(categories, Mapping) or not categories:
            raise ValueError("AImodel intent router domain must contain categories mapping")
        for category_name, category_payload in categories.items():
            category = _non_blank(category_name, "router.category")
            if not isinstance(category_payload, Mapping):
                raise ValueError("AImodel intent router category entries must be mappings")
            intents = category_payload.get("intents")
            if not isinstance(intents, Mapping) or not intents:
                raise ValueError("AImodel intent router category must contain intents mapping")
            for intent_name, intent_payload in intents.items():
                intent = _non_blank(intent_name, "router.intent")
                if not isinstance(intent_payload, Mapping):
                    raise ValueError("AImodel intent router intent entries must be mappings")
                rules.append(_intent_rule_from_tree_node(domain, category, intent, intent_payload))
    if not rules:
        raise ValueError("AImodel intent router config must contain at least one intent")
    return tuple(rules)


def _intent_rule_from_tree_node(
    domain: str,
    category: str,
    intent: str,
    payload: Mapping[str, Any],
) -> AImodelIntentRule:
    """Build one executable AImodel rule from a tree leaf node."""

    action = str(payload.get("action") or "rag").strip()
    collection = _optional_str(payload.get("collection"))
    if action == "rag" and collection is None:
        raise ValueError("AImodel rag intent route must define collection")
    match = payload.get("match") or {}
    if not isinstance(match, Mapping):
        raise ValueError("AImodel intent route match must be a mapping")
    return AImodelIntentRule(
        name=str(payload.get("name") or f"{domain}_{category}_{intent}"),
        domain=domain,
        category=category,
        intent=intent,
        action=action,
        collection=collection,
        priority=_validate_int(payload.get("priority"), "route.priority"),
        confidence=_validate_threshold(payload.get("confidence"), "route.confidence"),
        any_terms=_string_tuple(match.get("any")),
        all_terms=_string_tuple(match.get("all")),
        regex_patterns=_string_tuple(match.get("regex")),
        rag_enabled=_validate_bool(payload.get("rag_enabled", action == "rag"), "route.rag_enabled"),
    )


def select_rag_collections_by_score(
    candidates: Sequence[Mapping[str, Any]],
    *,
    min_score: float = 0.75,
    delta: float = 0.08,
    max_collections: int = 3,
) -> tuple[str, ...]:
    """Select RAG collections from scored intent candidates.

    Args:
        candidates: Score-sorted candidate summaries returned by
            ``route_with_candidates``.
        min_score: Absolute lower bound for accepting a collection.
        delta: Relative window below the winning candidate score.
        max_collections: Maximum number of unique collections to return.

    Returns:
        Ordered unique collection names. The winning high-confidence collection
        stays first, while close secondary RAG candidates can trigger RAG-side
        parallel retrieval.
    """

    if max_collections <= 0:
        return ()
    rag_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("action") == "rag"
        and isinstance(candidate.get("collection"), str)
        and str(candidate.get("collection")).strip()
    ]
    if not rag_candidates:
        return ()
    winner_score = _candidate_score(rag_candidates[0])
    threshold = max(float(min_score), winner_score - float(delta))
    selected: list[str] = []
    for candidate in rag_candidates:
        if _candidate_score(candidate) < threshold:
            continue
        collection = str(candidate.get("collection") or "").strip()
        if collection and collection not in selected:
            selected.append(collection)
        if len(selected) >= max_collections:
            break
    return tuple(selected)


def _candidate_score(candidate: Mapping[str, Any]) -> float:
    """Return a numeric candidate score with invalid values treated as zero."""

    try:
        return float(candidate.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _top_candidate_summaries(
    ranked_matches: Sequence[_RuleMatch],
    *,
    rules: Sequence[AImodelIntentRule],
    limit: int,
) -> list[dict[str, Any]]:
    """Return top intent candidates and fill missing rows with low scores."""

    safe_limit = max(int(limit), 0)
    summaries = [_candidate_summary(match) for match in ranked_matches[:safe_limit]]
    if len(summaries) >= safe_limit:
        return summaries
    matched_rule_names = {match.rule.name for match in ranked_matches}
    unmatched_rules = sorted(
        (rule for rule in rules if rule.name not in matched_rule_names),
        key=lambda rule: (rule.priority, rule.name),
        reverse=True,
    )
    for rule in unmatched_rules[: safe_limit - len(summaries)]:
        summaries.append(_unmatched_candidate_summary(rule))
    return summaries



def _candidate_summary(match: _RuleMatch) -> dict[str, Any]:
    """Return a trace-safe summary for one matched intent candidate."""

    rule = match.rule
    return {
        "domain": rule.domain,
        "category": rule.category,
        "intent": rule.intent,
        "domain_intent": rule.domain_intent,
        "action": rule.action,
        "collection": rule.collection if rule.action == "rag" else None,
        "score": round(min(0.99, match.score), 4),
        "base_confidence": rule.confidence,
        "priority": rule.priority,
        "matched_rule": rule.name,
        "matched_terms": list(match.matched_terms),
        "matched_regex": list(match.matched_regex),
    }


def _unmatched_candidate_summary(rule: AImodelIntentRule) -> dict[str, Any]:
    """Return a low-score candidate for non-matching rules."""

    return {
        "domain": rule.domain,
        "category": rule.category,
        "intent": rule.intent,
        "domain_intent": rule.domain_intent,
        "action": rule.action,
        "collection": rule.collection if rule.action == "rag" else None,
        "score": round(rule.priority / 10000, 4),
        "base_confidence": rule.confidence,
        "priority": rule.priority,
        "matched_rule": rule.name,
        "matched_terms": [],
        "matched_regex": [],
    }


def _load_yaml_mapping(path: str | Path) -> Mapping[str, Any]:
    """Read a YAML file and require a top-level mapping document."""

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("AImodel intent route YAML document must contain a mapping")
    return data


def _normalize_for_matching(message: str) -> str:
    """Normalize case and whitespace for deterministic rule matching."""

    return " ".join(str(message).casefold().split())


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
    """Convert an optional YAML list to a non-blank string tuple."""

    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError("AImodel configured terms must be a list")
    return tuple(_non_blank(item, "configured term") for item in value)


def _non_blank(value: Any, field_name: str) -> str:
    """Return a trimmed non-blank string configuration value."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    """Return a stripped string or ``None`` for absent optional config."""

    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None
