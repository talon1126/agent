"""Deterministic, configuration-backed clarification policy for shopping goals."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .goal_state import CandidateStatus, GoalConflict, GoalConflictCode
from .schemas import AiModelClarificationOption, AiModelClarificationPayload
from .shopping_goal import (
    Constraint,
    Exclusion,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    OpenSlot,
    ShoppingGoal,
)


SlotKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
OptionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]


class ClarificationReason(StrEnum):
    """Stable reason codes for downstream routing and evaluation."""

    BLOCKING_CONFLICT = "blocking_conflict"
    MISSING_CATEGORY = "missing_category"
    HIGH_IMPACT_SLOT = "high_impact_slot"
    GENERAL_PREFERENCE = "general_preference"
    NO_CLARIFICATION_NEEDED = "no_clarification_needed"
    SUPPRESSED_UNCERTAINTY = "suppressed_uncertainty"
    NO_CANDIDATES = "no_candidates"


class ClarificationOptionSeed(BaseModel):
    """A configured or catalog-backed option before A4 projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: OptionText
    value: OptionText


class ClarificationHistory(BaseModel):
    """Conversation-local suppression state supplied by the caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answered_slot_keys: tuple[SlotKey, ...] = Field(
        default_factory=tuple, max_length=64
    )
    skipped_slot_keys: tuple[SlotKey, ...] = Field(default_factory=tuple, max_length=64)
    skipped_conflict_fingerprints: tuple[SlotKey, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    recent_slot_keys: tuple[SlotKey, ...] = Field(default_factory=tuple, max_length=64)

    @model_validator(mode="after")
    def validate_history_keys(self) -> Self:
        for field_name in (
            "answered_slot_keys",
            "skipped_slot_keys",
            "skipped_conflict_fingerprints",
            "recent_slot_keys",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        return self


class ClarificationDecision(BaseModel):
    """One clarification at most, or an explicit decision to proceed/fallback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: SlotKey
    should_ask: bool
    may_proceed: bool
    recommend_with_uncertainty: bool = False
    reason: ClarificationReason
    slot_key: SlotKey | None = None
    conflict_fingerprint: SlotKey | None = None
    payload: AiModelClarificationPayload | None = None
    critical_unknowns: tuple[SlotKey, ...] = Field(
        default_factory=tuple,
        max_length=16,
    )

    @model_validator(mode="after")
    def validate_decision_shape(self) -> Self:
        if self.should_ask:
            if self.slot_key is None or self.payload is None:
                raise ValueError("clarification requires one slot_key and payload")
            if self.may_proceed or self.recommend_with_uncertainty:
                raise ValueError("an active clarification cannot proceed")
        elif self.slot_key is not None or self.payload is not None:
            raise ValueError("non-clarification decisions cannot carry a question")
        if self.conflict_fingerprint is not None and (
            not self.should_ask
            or self.reason is not ClarificationReason.BLOCKING_CONFLICT
        ):
            raise ValueError("conflict fingerprint requires a blocking clarification")
        if self.recommend_with_uncertainty and not self.may_proceed:
            raise ValueError("uncertain recommendation requires may_proceed")
        return self


class ClarificationEvaluationReport(BaseModel):
    """Machine-comparable metrics linked to all A2 shopping scenarios."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture_schema_version: int = Field(ge=1)
    case_count: int = Field(ge=1)
    expected_ask_count: int = Field(ge=0)
    expected_no_ask_count: int = Field(ge=0)
    actual_ask_count: int = Field(ge=0)
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    necessary_clarification_recall: float = Field(ge=0, le=1)
    meaningless_question_rate: float = Field(ge=0, le=1)
    slot_accuracy: float = Field(ge=0, le=1)


class _SlotAlias(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_key: SlotKey
    field: GoalField
    question_keywords: tuple[OptionText, ...] = Field(min_length=1, max_length=16)


class _ClarificationPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    policy_version: SlotKey
    recent_question_window: int = Field(ge=1, le=20)
    min_options: int = Field(ge=2, le=5)
    max_options: int = Field(ge=2, le=5)
    conflict_priority: dict[GoalConflictCode, int]
    slot_priority: dict[SlotKey, int]
    fallback_questions: dict[SlotKey, OptionText]
    option_catalog: dict[SlotKey, tuple[ClarificationOptionSeed, ...]]
    slot_aliases: tuple[_SlotAlias, ...] = ()

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.min_options > self.max_options:
            raise ValueError("min_options cannot exceed max_options")
        missing_conflicts = set(GoalConflictCode) - set(self.conflict_priority)
        if missing_conflicts:
            raise ValueError("every GoalConflictCode requires a priority")
        required_slots = {
            GoalField.CATEGORY.value,
            GoalField.BUDGET_MIN.value,
            GoalField.BUDGET_MAX.value,
            GoalField.BRAND.value,
            GoalField.USAGE_SCENARIO.value,
            GoalField.QUANTITY.value,
            GoalField.DELIVERY_DEADLINE.value,
            GoalField.SPECIFICATION.value,
            GoalField.FREEFORM_PREFERENCE.value,
        }
        if required_slots - set(self.slot_priority):
            raise ValueError("slot_priority is incomplete")
        if required_slots - set(self.fallback_questions):
            raise ValueError("fallback_questions is incomplete")
        for slot_key, options in self.option_catalog.items():
            values = [option.value for option in options]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate configured option value for {slot_key}")
            if len(options) >= self.max_options:
                raise ValueError(
                    f"configured options for {slot_key} must leave room for skip"
                )
        return self


@dataclass(frozen=True)
class _Topic:
    slot_key: str
    base_slot_key: str
    priority: int
    reason: ClarificationReason
    open_slot: OpenSlot | None = None
    conflict: GoalConflict | None = None
    conflict_fingerprint: str | None = None


QuestionRenderer = Callable[[str], str]


@lru_cache(maxsize=1)
def load_clarification_policy() -> _ClarificationPolicyConfig:
    """Load the versioned policy colocated with the runtime module."""

    path = Path(__file__).with_name("clarification_policy.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _ClarificationPolicyConfig.model_validate(raw)


def _has_positive_category(goal: ShoppingGoal) -> bool:
    return any(
        item.field is GoalField.CATEGORY
        for collection in (goal.hard_constraints, goal.preferences)
        for item in collection
    )


def _slot_key_for_open_slot(
    open_slot: OpenSlot,
    config: _ClarificationPolicyConfig,
) -> tuple[str, str]:
    base = open_slot.field.value
    if open_slot.field is GoalField.SPECIFICATION:
        return _specification_slot_key(open_slot.attribute or "关键规格"), base
    for alias in config.slot_aliases:
        if alias.field is not open_slot.field:
            continue
        if any(keyword in open_slot.question for keyword in alias.question_keywords):
            return alias.slot_key, base
    return base, base


def _conflict_slot_key(conflict: GoalConflict) -> tuple[str, str]:
    if conflict.code is GoalConflictCode.BUDGET_RANGE_REVERSED:
        return GoalField.BUDGET_MAX.value, GoalField.BUDGET_MAX.value
    if conflict.code is GoalConflictCode.BRAND_INCLUDED_AND_EXCLUDED:
        return GoalField.BRAND.value, GoalField.BRAND.value
    if conflict.code is GoalConflictCode.SPECIFICATION_MUTUALLY_EXCLUSIVE:
        suffix = conflict.attribute or "关键规格"
        return _specification_slot_key(suffix), GoalField.SPECIFICATION.value
    return conflict.field.value, conflict.field.value


def _specification_slot_key(attribute: str) -> str:
    normalized = attribute.casefold()
    prefix = f"{GoalField.SPECIFICATION.value}:"
    direct = f"{prefix}{normalized}"
    if len(direct) <= 128:
        return direct
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    available = 128 - len(prefix) - len(digest) - 1
    return f"{prefix}{normalized[:available]}:{digest}"


def _conflict_fingerprint(conflict: GoalConflict) -> str:
    payload = conflict.model_dump(mode="json")
    for key in ("values", "sources"):
        payload[key] = sorted(
            payload[key],
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"conflict-{hashlib.sha256(canonical.encode()).hexdigest()[:24]}"


def _conflict_topics(
    conflicts: Sequence[GoalConflict],
    config: _ClarificationPolicyConfig,
) -> list[_Topic]:
    topics = []
    for conflict in conflicts:
        slot_key, base = _conflict_slot_key(conflict)
        topics.append(
            _Topic(
                slot_key=slot_key,
                base_slot_key=base,
                priority=config.conflict_priority[conflict.code],
                reason=ClarificationReason.BLOCKING_CONFLICT,
                conflict=conflict,
                conflict_fingerprint=_conflict_fingerprint(conflict),
            )
        )
    return sorted(
        topics,
        key=lambda topic: (
            -topic.priority,
            topic.conflict.code.value if topic.conflict else "",
            topic.slot_key,
        ),
    )


def _open_topics(
    goal: ShoppingGoal,
    config: _ClarificationPolicyConfig,
) -> list[_Topic]:
    topics: dict[str, _Topic] = {}
    if not _has_positive_category(goal):
        slot_key = GoalField.CATEGORY.value
        topics[slot_key] = _Topic(
            slot_key=slot_key,
            base_slot_key=slot_key,
            priority=config.slot_priority[slot_key],
            reason=ClarificationReason.MISSING_CATEGORY,
        )

    high_impact_fields = {
        GoalField.CATEGORY,
        GoalField.BUDGET_MIN,
        GoalField.BUDGET_MAX,
        GoalField.BRAND,
        GoalField.USAGE_SCENARIO,
        GoalField.SPECIFICATION,
        GoalField.DELIVERY_DEADLINE,
        GoalField.QUANTITY,
    }
    for open_slot in goal.open_slots:
        slot_key, base = _slot_key_for_open_slot(open_slot, config)
        reason = (
            ClarificationReason.MISSING_CATEGORY
            if open_slot.field is GoalField.CATEGORY
            else ClarificationReason.HIGH_IMPACT_SLOT
            if open_slot.field in high_impact_fields
            else ClarificationReason.GENERAL_PREFERENCE
        )
        topic = _Topic(
            slot_key=slot_key,
            base_slot_key=base,
            priority=config.slot_priority.get(
                slot_key,
                config.slot_priority[base],
            ),
            reason=reason,
            open_slot=open_slot,
        )
        existing = topics.get(slot_key)
        if existing is None or topic.priority > existing.priority:
            topics[slot_key] = topic
    return sorted(topics.values(), key=lambda topic: (-topic.priority, topic.slot_key))


def _stable_scalar(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value).strip()


def _bounded_label(prefix: str, raw_value: str) -> str:
    combined = f"{prefix}{raw_value}"
    if len(combined) <= 512:
        return combined
    suffix = "..."
    return f"{prefix}{raw_value[: 512 - len(prefix) - len(suffix)]}{suffix}"


def _bounded_option_value(prefix: str, raw_value: str) -> str:
    combined = f"{prefix}{raw_value}"
    if len(combined) <= 512:
        return combined
    digest = hashlib.sha256(raw_value.encode()).hexdigest()
    return f"{prefix}sha256:{digest}"


def _conflict_option_seeds(topic: _Topic) -> tuple[ClarificationOptionSeed, ...]:
    conflict = topic.conflict
    if conflict is None:
        return ()
    seeds: list[ClarificationOptionSeed] = []
    for item in conflict.values:
        value = _stable_scalar(item.value if hasattr(item, "value") else item.question)
        if conflict.code is GoalConflictCode.BUDGET_RANGE_REVERSED:
            boundary = "最低预算" if item.field is GoalField.BUDGET_MIN else "最高预算"
            label = _bounded_label(f"保留{boundary} ", f"{value} 元")
            option_value = _bounded_option_value(
                f"resolve:{item.field.value}:",
                value,
            )
        elif conflict.code is GoalConflictCode.BRAND_INCLUDED_AND_EXCLUDED:
            action = "保留品牌要求" if item.kind == "hard" else "保留排除条件"
            label = _bounded_label(f"{action}“", f"{value}”")
            option_value = _bounded_option_value(
                f"resolve:brand:{item.kind}:",
                value,
            )
        elif conflict.code is GoalConflictCode.SPECIFICATION_MUTUALLY_EXCLUSIVE:
            action = "保留规格要求" if item.kind == "hard" else "保留排除条件"
            label = _bounded_label(f"{action}“", f"{value}”")
            option_value = _bounded_option_value(
                f"resolve:{topic.slot_key}:{item.kind}:",
                value,
            )
        elif conflict.code is GoalConflictCode.CONFIRMATION_MISMATCH:
            label = _bounded_label("采用“", f"{value}”")
            option_value = _bounded_option_value(
                f"resolve:{topic.slot_key}:",
                value,
            )
        else:
            continue
        seed = ClarificationOptionSeed(label=label, value=option_value)
        if seed.value not in {existing.value for existing in seeds}:
            seeds.append(seed)
    if not seeds:
        seeds.append(
            ClarificationOptionSeed(
                label="我来补充新的要求",
                value=f"input:{topic.slot_key}",
            )
        )
    return tuple(seeds)


def _known_specification_seeds(
    attribute: str,
    slot_key: str,
    known_attribute_options: Mapping[
        str,
        Sequence[ClarificationOptionSeed | Mapping[str, Any]],
    ],
) -> tuple[ClarificationOptionSeed, ...]:
    selected: Sequence[ClarificationOptionSeed | Mapping[str, Any]] = ()
    for name, options in known_attribute_options.items():
        if name.casefold() == attribute.casefold():
            selected = options
            break
    normalized: list[ClarificationOptionSeed] = []
    for raw in selected:
        seed = ClarificationOptionSeed.model_validate(raw)
        prefixed = ClarificationOptionSeed(
            label=seed.label,
            value=_bounded_option_value(f"{slot_key}:", seed.value),
        )
        if prefixed.value not in {item.value for item in normalized}:
            normalized.append(prefixed)
    return tuple(normalized)


def _topic_option_seeds(
    topic: _Topic,
    config: _ClarificationPolicyConfig,
    known_attribute_options: Mapping[
        str,
        Sequence[ClarificationOptionSeed | Mapping[str, Any]],
    ],
) -> tuple[ClarificationOptionSeed, ...]:
    if topic.conflict is not None:
        seeds = list(_conflict_option_seeds(topic))
    elif topic.base_slot_key == GoalField.SPECIFICATION.value:
        attribute = topic.open_slot.attribute if topic.open_slot else "关键规格"
        seeds = list(
            _known_specification_seeds(
                attribute,
                topic.slot_key,
                known_attribute_options,
            )
        )
        if not seeds:
            seeds.append(
                ClarificationOptionSeed(
                    label="我来补充具体要求",
                    value=f"input:{topic.slot_key}",
                )
            )
    else:
        seeds = list(
            config.option_catalog.get(
                topic.slot_key,
                config.option_catalog.get(topic.base_slot_key, ()),
            )
        )
        if not seeds:
            seeds.append(
                ClarificationOptionSeed(
                    label="我来补充具体要求",
                    value=f"input:{topic.slot_key}",
                )
            )

    skip = ClarificationOptionSeed(
        label="都可以 / 跳过",
        value=f"skip:{topic.slot_key}",
    )
    unique: list[ClarificationOptionSeed] = []
    for seed in (*seeds, skip):
        if seed.value not in {item.value for item in unique}:
            unique.append(seed)
    if len(unique) < config.min_options:
        unique.insert(
            0,
            ClarificationOptionSeed(
                label="我来补充具体要求",
                value=f"input:{topic.slot_key}",
            ),
        )
    if len(unique) > config.max_options:
        unique = unique[: config.max_options - 1] + [skip]
    return tuple(unique)


def _option_id(slot_key: str, value: str) -> str:
    digest = hashlib.sha256(f"{slot_key}\0{value}".encode()).hexdigest()[:16]
    return f"clarify-{digest}"


def _contains_internal_identifier(question: str) -> bool:
    lowered = question.casefold()
    internal_tokens = {field.value.casefold() for field in GoalField}
    internal_tokens.update({"goalfield", "slot_key", "conflict_code"})
    return any(token in lowered for token in internal_tokens)


def _question_for_topic(
    topic: _Topic,
    config: _ClarificationPolicyConfig,
    question_renderer: QuestionRenderer | None,
) -> str:
    fallback = config.fallback_questions.get(
        topic.slot_key,
        config.fallback_questions[topic.base_slot_key],
    )
    if question_renderer is None:
        return fallback
    try:
        rendered = question_renderer(topic.slot_key)
        normalized = rendered.strip()
        if not normalized or len(normalized) > 512:
            return fallback
        if _contains_internal_identifier(normalized):
            return fallback
        return normalized
    except Exception:
        return fallback


def _clarification_decision(
    topic: _Topic,
    config: _ClarificationPolicyConfig,
    known_attribute_options: Mapping[
        str,
        Sequence[ClarificationOptionSeed | Mapping[str, Any]],
    ],
    question_renderer: QuestionRenderer | None,
) -> ClarificationDecision:
    question = _question_for_topic(topic, config, question_renderer)
    seeds = _topic_option_seeds(topic, config, known_attribute_options)
    options = [
        AiModelClarificationOption(
            option_id=_option_id(topic.slot_key, seed.value),
            label=seed.label,
            value=seed.value,
        )
        for seed in seeds
    ]
    payload = AiModelClarificationPayload(answer=question, options=options)
    return ClarificationDecision(
        policy_version=config.policy_version,
        should_ask=True,
        may_proceed=False,
        reason=topic.reason,
        slot_key=topic.slot_key,
        conflict_fingerprint=topic.conflict_fingerprint,
        payload=payload,
    )


def select_clarification(
    goal: ShoppingGoal,
    *,
    conflicts: Sequence[GoalConflict] = (),
    history: ClarificationHistory | None = None,
    candidate_status: CandidateStatus = CandidateStatus.UNCHANGED,
    known_attribute_options: Mapping[
        str,
        Sequence[ClarificationOptionSeed | Mapping[str, Any]],
    ]
    | None = None,
    question_renderer: QuestionRenderer | None = None,
) -> ClarificationDecision:
    """Choose at most one decision-changing question without model or I/O calls."""

    config = load_clarification_policy()
    validated_goal = ShoppingGoal.model_validate(goal.model_dump(mode="python"))
    validated_conflicts = tuple(
        GoalConflict.model_validate(conflict.model_dump(mode="python"))
        for conflict in conflicts
    )
    history = ClarificationHistory.model_validate(
        (history or ClarificationHistory()).model_dump(mode="python")
    )
    candidate_status = CandidateStatus(candidate_status)
    known_attribute_options = known_attribute_options or {}

    conflict_topics = _conflict_topics(validated_conflicts, config)
    skipped_conflicts = set(history.skipped_conflict_fingerprints)
    skipped_conflict_topics = [
        topic
        for topic in conflict_topics
        if topic.conflict_fingerprint in skipped_conflicts
    ]
    active_conflicts = [
        topic
        for topic in conflict_topics
        if topic.conflict_fingerprint not in skipped_conflicts
    ]
    if active_conflicts:
        return _clarification_decision(
            active_conflicts[0],
            config,
            known_attribute_options,
            question_renderer,
        )

    topics = _open_topics(validated_goal, config)
    recent = set(history.recent_slot_keys[-config.recent_question_window :])
    answered = set(history.answered_slot_keys)
    skipped = set(history.skipped_slot_keys)
    eligible = [
        topic
        for topic in topics
        if topic.slot_key not in answered
        and topic.slot_key not in recent
        and topic.slot_key not in skipped
    ]
    if eligible:
        return _clarification_decision(
            eligible[0],
            config,
            known_attribute_options,
            question_renderer,
        )

    suppressed_unknowns = tuple(
        dict.fromkeys(topic.slot_key for topic in (*skipped_conflict_topics, *topics))
    )
    if suppressed_unknowns:
        may_proceed = candidate_status is not CandidateStatus.EMPTY
        return ClarificationDecision(
            policy_version=config.policy_version,
            should_ask=False,
            may_proceed=may_proceed,
            recommend_with_uncertainty=(
                may_proceed and candidate_status is CandidateStatus.AVAILABLE
            ),
            reason=ClarificationReason.SUPPRESSED_UNCERTAINTY,
            critical_unknowns=suppressed_unknowns,
        )

    if candidate_status is CandidateStatus.EMPTY:
        return ClarificationDecision(
            policy_version=config.policy_version,
            should_ask=False,
            may_proceed=False,
            reason=ClarificationReason.NO_CANDIDATES,
        )
    return ClarificationDecision(
        policy_version=config.policy_version,
        should_ask=False,
        may_proceed=True,
        reason=ClarificationReason.NO_CLARIFICATION_NEEDED,
    )


_EVAL_TIME = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def _evaluation_evidence(quote: str) -> GoalEvidence:
    return GoalEvidence(
        source_type=GoalSourceType.USER_TURN,
        source_turn=1,
        quote=quote,
        confidence=1,
        created_at=_EVAL_TIME,
        updated_at=_EVAL_TIME,
    )


def _evaluation_goal(policy_input: Mapping[str, Any]) -> ShoppingGoal:
    constraints = []
    if policy_input.get("category_known"):
        constraints.append(
            Constraint(
                field=GoalField.CATEGORY,
                value="electronics",
                evidence=_evaluation_evidence("已知品类"),
            )
        )
    open_slots = []
    for raw in policy_input.get("open_slots", []):
        field = GoalField(raw["field"])
        open_slots.append(
            OpenSlot(
                field=field,
                attribute=raw.get("attribute"),
                question=raw["question"],
                evidence=GoalEvidence(
                    source_type=GoalSourceType.SYSTEM_DEFAULT,
                    confidence=0,
                    created_at=_EVAL_TIME,
                    updated_at=_EVAL_TIME,
                ),
            )
        )
    return ShoppingGoal(
        hard_constraints=tuple(constraints),
        open_slots=tuple(open_slots),
    )


def _evaluation_conflict(code: GoalConflictCode) -> GoalConflict:
    if code is GoalConflictCode.BUDGET_RANGE_REVERSED:
        minimum = Constraint(
            field=GoalField.BUDGET_MIN,
            value=5000,
            evidence=_evaluation_evidence("至少五千"),
        )
        maximum = Constraint(
            field=GoalField.BUDGET_MAX,
            value=3000,
            evidence=_evaluation_evidence("最多三千"),
        )
        values = (minimum, maximum)
        field = GoalField.BUDGET_MAX
        topic = "预算范围"
    elif code is GoalConflictCode.BRAND_INCLUDED_AND_EXCLUDED:
        included = Constraint(
            field=GoalField.BRAND,
            value="Xiaomi",
            evidence=_evaluation_evidence("只要小米"),
        )
        excluded = Exclusion(
            field=GoalField.BRAND,
            value="Xiaomi",
            evidence=_evaluation_evidence("不要小米"),
        )
        values = (included, excluded)
        field = GoalField.BRAND
        topic = "品牌取舍"
    else:
        value = Constraint(
            field=GoalField.DELIVERY_DEADLINE,
            value=_EVAL_TIME,
            evidence=_evaluation_evidence("配送时间"),
        )
        values = (value,)
        field = GoalField.DELIVERY_DEADLINE
        topic = "配送时间"
    return GoalConflict(
        code=code,
        field=field,
        values=values,
        sources=tuple(item.evidence for item in values),
        clarification_topic=topic,
    )


def evaluate_clarification_fixture(
    fixture_path: str | Path,
) -> ClarificationEvaluationReport:
    """Evaluate B4 decisions against the A2-linked, frozen policy fixture."""

    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    cases = fixture["cases"]
    seen_ids: set[str] = set()
    true_positive = false_positive = false_negative = actual_ask = 0
    expected_ask = slot_correct = slot_expected = 0

    for case in cases:
        scenario_id = case["source_scenario_id"]
        if scenario_id in seen_ids:
            raise ValueError(f"duplicate clarification scenario: {scenario_id}")
        seen_ids.add(scenario_id)
        policy_input = case["policy_input"]
        conflicts = tuple(
            _evaluation_conflict(GoalConflictCode(code))
            for code in policy_input.get("conflict_codes", [])
        )
        decision = select_clarification(
            _evaluation_goal(policy_input),
            conflicts=conflicts,
            candidate_status=CandidateStatus(policy_input["candidate_status"]),
        )
        expected = bool(case["expected_should_ask"])
        expected_ask += int(expected)
        actual_ask += int(decision.should_ask)
        true_positive += int(expected and decision.should_ask)
        false_positive += int(not expected and decision.should_ask)
        false_negative += int(expected and not decision.should_ask)
        expected_slot = case.get("expected_slot_key")
        if expected_slot is not None:
            slot_expected += 1
            slot_correct += int(decision.slot_key == expected_slot)

    expected_no_ask = len(cases) - expected_ask
    recall = true_positive / expected_ask if expected_ask else 1.0
    pointless_rate = false_positive / expected_no_ask if expected_no_ask else 0.0
    slot_accuracy = slot_correct / slot_expected if slot_expected else 1.0
    return ClarificationEvaluationReport(
        fixture_schema_version=fixture["schema_version"],
        case_count=len(cases),
        expected_ask_count=expected_ask,
        expected_no_ask_count=expected_no_ask,
        actual_ask_count=actual_ask,
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        necessary_clarification_recall=recall,
        meaningless_question_rate=pointless_rate,
        slot_accuracy=slot_accuracy,
    )
