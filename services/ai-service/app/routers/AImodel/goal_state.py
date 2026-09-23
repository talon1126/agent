"""Deterministic state merging and conflict detection for shopping goals."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .goal_extractor import (
    DeltaAction,
    GoalDelta,
    GoalItemKind,
    GoalRemoveMutation,
    GoalValueItem,
    GoalValueMutation,
)
from .shopping_goal import (
    Constraint,
    DecisionStage,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    Preference,
    ShoppingGoal,
)


SemanticKey = tuple[GoalField, str | None]


class CandidateStatus(StrEnum):
    """Search result state supplied by the deterministic caller."""

    UNCHANGED = "unchanged"
    NOT_SEARCHED = "not_searched"
    EMPTY = "empty"
    AVAILABLE = "available"
    SELECTED = "selected"


class GoalConflictCode(StrEnum):
    """Stable machine codes consumed by the clarification policy."""

    BUDGET_RANGE_REVERSED = "budget_range_reversed"
    BRAND_INCLUDED_AND_EXCLUDED = "brand_included_and_excluded"
    SPECIFICATION_MUTUALLY_EXCLUSIVE = "specification_mutually_exclusive"
    DELIVERY_DEADLINE_IN_PAST = "delivery_deadline_in_past"
    CONFIRMATION_MISMATCH = "confirmation_mismatch"


class MergeEventOutcome(StrEnum):
    """Describe how one delta operation affected the authoritative state."""

    APPLIED = "applied"
    NO_CHANGE = "no_change"
    IGNORED_LOWER_PRIORITY = "ignored_lower_priority"
    CONFLICT = "conflict"


class GoalMergeRejected(ValueError):
    """Raised when an untrusted delta cannot be merged safely."""


class GoalConflict(BaseModel):
    """A contradiction kept outside ShoppingGoal until the user resolves it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: GoalConflictCode
    field: GoalField
    attribute: str | None = None
    values: tuple[GoalValueItem, ...] = Field(min_length=1, max_length=8)
    sources: tuple[GoalEvidence, ...] = Field(min_length=1, max_length=8)
    clarification_topic: str = Field(min_length=1, max_length=128)


class GoalChangeEvent(BaseModel):
    """Audit one requested mutation without adding persistence concerns."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: DeltaAction
    field: GoalField
    attribute: str | None = None
    source_turn: int = Field(ge=1)
    before: tuple[GoalValueItem, ...] = Field(default_factory=tuple, max_length=8)
    after: tuple[GoalValueItem, ...] = Field(default_factory=tuple, max_length=8)
    outcome: MergeEventOutcome
    conflict_code: GoalConflictCode | None = None


class GoalMergeResult(BaseModel):
    """The next valid snapshot plus conflicts and an appendable audit trail."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: ShoppingGoal
    conflicts: tuple[GoalConflict, ...] = Field(default_factory=tuple, max_length=16)
    events: tuple[GoalChangeEvent, ...] = Field(default_factory=tuple, max_length=64)


_COLLECTION_BY_KIND: dict[GoalItemKind, str] = {
    "hard": "hard_constraints",
    "soft": "preferences",
    "exclude": "exclusions",
    "unknown": "open_slots",
}
_KIND_BY_COLLECTION = {value: key for key, value in _COLLECTION_BY_KIND.items()}
_KNOWN_KINDS = frozenset({"hard", "soft", "exclude"})
_REQUIRED_FIELDS = frozenset({GoalField.CATEGORY})
_CLARIFICATION_TOPIC = {
    GoalConflictCode.BUDGET_RANGE_REVERSED: "预算范围",
    GoalConflictCode.BRAND_INCLUDED_AND_EXCLUDED: "品牌取舍",
    GoalConflictCode.SPECIFICATION_MUTUALLY_EXCLUSIVE: "规格取舍",
    GoalConflictCode.DELIVERY_DEADLINE_IN_PAST: "配送时间",
    GoalConflictCode.CONFIRMATION_MISMATCH: "确认目标",
}


def _semantic_key(field: GoalField, attribute: str | None = None) -> SemanticKey:
    return field, attribute.casefold() if attribute else None


def _collection_snapshot(
    collections: dict[str, list[GoalValueItem]], key: SemanticKey
) -> tuple[GoalValueItem, ...]:
    return tuple(
        item
        for collection_name in (
            "hard_constraints",
            "preferences",
            "exclusions",
            "open_slots",
        )
        for item in collections[collection_name]
        if item.semantic_key == key
    )


def _replace_key(
    collection: list[GoalValueItem],
    key: SemanticKey,
    replacement: GoalValueItem | None,
) -> None:
    first_index = next(
        (index for index, item in enumerate(collection) if item.semantic_key == key),
        len(collection),
    )
    collection[:] = [item for item in collection if item.semantic_key != key]
    if replacement is not None:
        collection.insert(first_index, replacement)


def _remove_key_from_kinds(
    collections: dict[str, list[GoalValueItem]],
    key: SemanticKey,
    kinds: Sequence[GoalItemKind],
) -> None:
    for kind in kinds:
        _replace_key(collections[_COLLECTION_BY_KIND[kind]], key, None)


def _source_rank(evidence: GoalEvidence, *, historical: bool) -> int:
    if historical:
        return 200
    source = evidence.source_type
    if source is GoalSourceType.USER_TURN:
        return 400
    if source is GoalSourceType.PAGE_CONTEXT:
        return 300
    if source is GoalSourceType.MODEL_INFERENCE:
        return 100
    return 0


def _normalized_value(value: Any) -> Any:
    if isinstance(value, str):
        return "".join(value.split()).casefold()
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _item_value(item: GoalValueItem) -> Any:
    return item.question if item.kind == "unknown" else item.value


def _same_semantic_value(left: GoalValueItem, right: GoalValueItem) -> bool:
    return (
        left.kind == right.kind
        and left.semantic_key == right.semantic_key
        and _normalized_value(_item_value(left))
        == _normalized_value(_item_value(right))
    )


def _validate_merge_inputs(
    current: ShoppingGoal,
    delta: GoalDelta,
    long_term_preferences: Sequence[Preference],
    reference_time: datetime | None,
) -> tuple[ShoppingGoal, GoalDelta, tuple[Preference, ...]]:
    if reference_time is not None and (
        reference_time.tzinfo is None or reference_time.utcoffset() is None
    ):
        raise GoalMergeRejected("reference_time must include a timezone")

    for operation in getattr(delta, "operations", ()):  # forged models bypass Pydantic
        if isinstance(operation, GoalValueMutation) and isinstance(
            operation.item, Constraint
        ):
            evidence = getattr(operation.item, "evidence", None)
            if not isinstance(evidence, GoalEvidence):
                raise GoalMergeRejected("hard constraint requires evidence")
            if evidence.source_type not in {
                GoalSourceType.USER_TURN,
                GoalSourceType.PAGE_CONTEXT,
            }:
                raise GoalMergeRejected(
                    "hard constraint requires evidence from user_turn or page_context"
                )

    try:
        validated_current = ShoppingGoal.model_validate(
            current.model_dump(mode="python")
        )
        validated_delta = GoalDelta.model_validate(delta.model_dump(mode="python"))
        validated_long_term = tuple(
            Preference.model_validate(item.model_dump(mode="python"))
            for item in long_term_preferences
        )
    except (AttributeError, TypeError, ValidationError) as exc:
        raise GoalMergeRejected("invalid goal merge input") from exc
    return validated_current, validated_delta, validated_long_term


def _reference_time_from_delta(
    delta: GoalDelta, explicit_reference_time: datetime | None
) -> datetime | None:
    if explicit_reference_time is not None:
        return explicit_reference_time
    timestamps = []
    for operation in delta.operations:
        evidence = (
            operation.item.evidence
            if isinstance(operation, GoalValueMutation)
            else operation.evidence
        )
        timestamps.append(evidence.updated_at)
    return max(timestamps) if timestamps else None


def _seed_long_term_preferences(
    collections: dict[str, list[GoalValueItem]],
    preferences: Sequence[Preference],
) -> None:
    occupied = {
        item.semantic_key for collection in collections.values() for item in collection
    }
    for preference in preferences:
        if preference.semantic_key not in occupied:
            collections["preferences"].append(preference)
            occupied.add(preference.semantic_key)


def _apply_value_mutation(
    collections: dict[str, list[GoalValueItem]],
    operation: GoalValueMutation,
    source_turn: int,
    winner_rank: dict[tuple[str, SemanticKey], int],
) -> GoalChangeEvent:
    item = operation.item
    key = item.semantic_key
    before = _collection_snapshot(collections, key)
    same_kind = next((value for value in before if value.kind == item.kind), None)

    rank_key = (item.kind, key)
    incoming_rank = _source_rank(item.evidence, historical=False)
    existing_rank = winner_rank.get(rank_key, 200 if same_kind is not None else -1)
    if incoming_rank < existing_rank:
        return GoalChangeEvent(
            action=operation.action,
            field=item.field,
            attribute=item.attribute,
            source_turn=source_turn,
            before=before,
            after=before,
            outcome=MergeEventOutcome.IGNORED_LOWER_PRIORITY,
        )

    if operation.action is DeltaAction.CONFIRM and same_kind is not None:
        if not _same_semantic_value(same_kind, item):
            return GoalChangeEvent(
                action=operation.action,
                field=item.field,
                attribute=item.attribute,
                source_turn=source_turn,
                before=before,
                after=(item,),
                outcome=MergeEventOutcome.CONFLICT,
                conflict_code=GoalConflictCode.CONFIRMATION_MISMATCH,
            )

    if item.kind == "soft" and any(value.kind == "hard" for value in before):
        return GoalChangeEvent(
            action=operation.action,
            field=item.field,
            attribute=item.attribute,
            source_turn=source_turn,
            before=before,
            after=before,
            outcome=MergeEventOutcome.IGNORED_LOWER_PRIORITY,
        )
    if item.kind == "unknown" and any(value.kind in _KNOWN_KINDS for value in before):
        return GoalChangeEvent(
            action=operation.action,
            field=item.field,
            attribute=item.attribute,
            source_turn=source_turn,
            before=before,
            after=before,
            outcome=MergeEventOutcome.IGNORED_LOWER_PRIORITY,
        )

    if same_kind == item:
        return GoalChangeEvent(
            action=operation.action,
            field=item.field,
            attribute=item.attribute,
            source_turn=source_turn,
            before=before,
            after=before,
            outcome=MergeEventOutcome.NO_CHANGE,
        )

    if item.kind == "hard":
        _remove_key_from_kinds(collections, key, ("soft", "unknown"))
    elif item.kind == "exclude":
        _remove_key_from_kinds(collections, key, ("soft", "unknown"))
    elif item.kind == "soft":
        _remove_key_from_kinds(collections, key, ("unknown",))
    elif item.kind == "unknown":
        _remove_key_from_kinds(collections, key, ("unknown",))

    collection_name = _COLLECTION_BY_KIND[item.kind]
    _replace_key(collections[collection_name], key, item)
    winner_rank[rank_key] = incoming_rank
    after = _collection_snapshot(collections, key)
    return GoalChangeEvent(
        action=operation.action,
        field=item.field,
        attribute=item.attribute,
        source_turn=source_turn,
        before=before,
        after=after,
        outcome=MergeEventOutcome.APPLIED,
    )


def _apply_remove_mutation(
    collections: dict[str, list[GoalValueItem]],
    operation: GoalRemoveMutation,
    source_turn: int,
    winner_rank: dict[tuple[str, SemanticKey], int],
) -> GoalChangeEvent:
    key = _semantic_key(operation.field, operation.attribute)
    before = tuple(
        item
        for item in _collection_snapshot(collections, key)
        if item.kind in operation.target_kinds
    )
    _remove_key_from_kinds(collections, key, operation.target_kinds)
    removal_rank = _source_rank(operation.evidence, historical=False)
    for kind in operation.target_kinds:
        rank_key = (kind, key)
        winner_rank[rank_key] = max(winner_rank.get(rank_key, -1), removal_rank)
    outcome = MergeEventOutcome.APPLIED if before else MergeEventOutcome.NO_CHANGE
    return GoalChangeEvent(
        action=operation.action,
        field=operation.field,
        attribute=operation.attribute,
        source_turn=source_turn,
        before=before,
        after=(),
        outcome=outcome,
    )


def _operation_rank(operation: GoalValueMutation | GoalRemoveMutation) -> int:
    evidence = (
        operation.item.evidence
        if isinstance(operation, GoalValueMutation)
        else operation.evidence
    )
    return _source_rank(evidence, historical=False)


def _conflict(
    code: GoalConflictCode,
    field: GoalField,
    values: Sequence[GoalValueItem],
    *,
    attribute: str | None = None,
) -> GoalConflict:
    unique_values: list[GoalValueItem] = []
    for item in values:
        if item not in unique_values:
            unique_values.append(item)
    return GoalConflict(
        code=code,
        field=field,
        attribute=attribute,
        values=tuple(unique_values),
        sources=tuple(item.evidence for item in unique_values),
        clarification_topic=_CLARIFICATION_TOPIC[code],
    )


def _detect_conflicts(
    collections: dict[str, list[GoalValueItem]],
    events: Sequence[GoalChangeEvent],
    reference_time: datetime | None,
) -> tuple[GoalConflict, ...]:
    hard = collections["hard_constraints"]
    excluded = collections["exclusions"]
    conflicts: list[GoalConflict] = []

    minimum = next((item for item in hard if item.field is GoalField.BUDGET_MIN), None)
    maximum = next((item for item in hard if item.field is GoalField.BUDGET_MAX), None)
    if minimum is not None and maximum is not None and minimum.value > maximum.value:
        conflicts.append(
            _conflict(
                GoalConflictCode.BUDGET_RANGE_REVERSED,
                GoalField.BUDGET_MAX,
                (minimum, maximum),
            )
        )

    included_brands = {
        _normalized_value(item.value): item
        for item in hard
        if item.field is GoalField.BRAND
    }
    excluded_brands = {
        _normalized_value(item.value): item
        for item in excluded
        if item.field is GoalField.BRAND
    }
    for normalized in sorted(included_brands.keys() & excluded_brands.keys(), key=str):
        conflicts.append(
            _conflict(
                GoalConflictCode.BRAND_INCLUDED_AND_EXCLUDED,
                GoalField.BRAND,
                (included_brands[normalized], excluded_brands[normalized]),
            )
        )

    included_specs = {
        (item.semantic_key, _normalized_value(item.value)): item
        for item in hard
        if item.field is GoalField.SPECIFICATION
    }
    excluded_specs = {
        (item.semantic_key, _normalized_value(item.value)): item
        for item in excluded
        if item.field is GoalField.SPECIFICATION
    }
    for normalized in sorted(
        included_specs.keys() & excluded_specs.keys(), key=lambda value: str(value)
    ):
        included = included_specs[normalized]
        conflicts.append(
            _conflict(
                GoalConflictCode.SPECIFICATION_MUTUALLY_EXCLUSIVE,
                GoalField.SPECIFICATION,
                (included, excluded_specs[normalized]),
                attribute=included.attribute,
            )
        )

    if reference_time is not None:
        for deadline in hard:
            if (
                deadline.field is GoalField.DELIVERY_DEADLINE
                and deadline.value < reference_time
            ):
                conflicts.append(
                    _conflict(
                        GoalConflictCode.DELIVERY_DEADLINE_IN_PAST,
                        GoalField.DELIVERY_DEADLINE,
                        (deadline,),
                    )
                )

    for event in events:
        if event.conflict_code is GoalConflictCode.CONFIRMATION_MISMATCH:
            values = (*event.before, *event.after)
            conflicts.append(
                _conflict(
                    GoalConflictCode.CONFIRMATION_MISMATCH,
                    event.field,
                    values,
                    attribute=event.attribute,
                )
            )

    return tuple(conflicts)


def _conflict_keys(conflict: GoalConflict) -> frozenset[SemanticKey]:
    if conflict.code is GoalConflictCode.BUDGET_RANGE_REVERSED:
        return frozenset(
            {
                _semantic_key(GoalField.BUDGET_MIN),
                _semantic_key(GoalField.BUDGET_MAX),
            }
        )
    return frozenset({_semantic_key(conflict.field, conflict.attribute)})


def _rollback_conflicts(
    collections: dict[str, list[GoalValueItem]],
    original: dict[str, list[GoalValueItem]],
    conflicts: Sequence[GoalConflict],
) -> frozenset[SemanticKey]:
    keys = frozenset(key for conflict in conflicts for key in _conflict_keys(conflict))
    for collection_name in collections:
        original_items = original[collection_name]
        replacements = {
            key: tuple(item for item in original_items if item.semantic_key == key)
            for key in keys
        }
        restored: list[GoalValueItem] = []
        emitted: set[SemanticKey] = set()
        for item in collections[collection_name]:
            if item.semantic_key not in keys:
                restored.append(item)
            elif item.semantic_key not in emitted:
                restored.extend(replacements[item.semantic_key])
                emitted.add(item.semantic_key)
        for item in original_items:
            if item.semantic_key in keys and item.semantic_key not in emitted:
                restored.extend(replacements[item.semantic_key])
                emitted.add(item.semantic_key)
        collections[collection_name][:] = restored
    return keys


def _mark_conflict_events(
    events: Sequence[GoalChangeEvent],
    conflicts: Sequence[GoalConflict],
    rolled_back_keys: frozenset[SemanticKey],
) -> tuple[GoalChangeEvent, ...]:
    code_by_key = {
        key: conflict.code for conflict in conflicts for key in _conflict_keys(conflict)
    }
    marked = []
    for event in events:
        key = _semantic_key(event.field, event.attribute)
        if key in rolled_back_keys and event.outcome is MergeEventOutcome.APPLIED:
            marked.append(
                event.model_copy(
                    update={
                        "outcome": MergeEventOutcome.CONFLICT,
                        "conflict_code": code_by_key[key],
                    }
                )
            )
        else:
            marked.append(event)
    return tuple(marked)


def _semantic_state(
    collections: dict[str, list[GoalValueItem]],
) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        sorted(
            (
                item.kind,
                item.field.value,
                item.attribute.casefold() if item.attribute else None,
                _normalized_value(_item_value(item)),
            )
            for collection in collections.values()
            for item in collection
        )
    )


def _target_stage(
    current: ShoppingGoal,
    collections: dict[str, list[GoalValueItem]],
    conflicts: Sequence[GoalConflict],
    candidate_status: CandidateStatus,
    meaning_changed: bool,
) -> tuple[DecisionStage, str | None]:
    if conflicts:
        codes = ",".join(sorted({conflict.code.value for conflict in conflicts}))
        return DecisionStage.CLARIFYING, f"goal_conflict:{codes}"

    known_required = {
        item.field
        for collection_name in (
            "hard_constraints",
            "preferences",
        )
        for item in collections[collection_name]
        if item.field in _REQUIRED_FIELDS
    }
    open_required = {
        item.field
        for item in collections["open_slots"]
        if item.field in _REQUIRED_FIELDS
    }
    missing = _REQUIRED_FIELDS - known_required
    if open_required:
        fields = ",".join(sorted(field.value for field in open_required))
        return DecisionStage.CLARIFYING, f"required_slot_open:{fields}"
    if missing:
        if current.decision_stage is DecisionStage.DISCOVERING:
            return DecisionStage.DISCOVERING, None
        fields = ",".join(sorted(field.value for field in missing))
        return DecisionStage.CLARIFYING, f"required_slot_missing:{fields}"

    if candidate_status is CandidateStatus.EMPTY:
        return DecisionStage.CLARIFYING, "candidate_set_empty"
    if candidate_status is CandidateStatus.AVAILABLE:
        return DecisionStage.COMPARING, None
    if candidate_status is CandidateStatus.SELECTED:
        return DecisionStage.DECIDED, None
    if (
        candidate_status is CandidateStatus.NOT_SEARCHED
        or meaning_changed
        or current.decision_stage is DecisionStage.DISCOVERING
    ):
        return DecisionStage.SEARCHING, None
    return current.decision_stage, current.stage_reason


def _build_goal(
    current: ShoppingGoal,
    collections: dict[str, list[GoalValueItem]],
    target_stage: DecisionStage,
    stage_reason: str | None,
) -> ShoppingGoal:
    if target_stage is not current.decision_stage:
        current.transition_to(
            target_stage,
            clarification_reason=stage_reason,
        )

    payload = current.model_dump(mode="python")
    for collection_name in _KIND_BY_COLLECTION:
        payload[collection_name] = tuple(collections[collection_name])
    payload["decision_stage"] = target_stage
    payload["stage_reason"] = (
        stage_reason if target_stage is DecisionStage.CLARIFYING else None
    )

    changed = any(
        payload[name] != getattr(current, name)
        for name in (
            "hard_constraints",
            "preferences",
            "exclusions",
            "open_slots",
            "decision_stage",
            "stage_reason",
        )
    )
    payload["revision"] = current.revision + int(changed)
    return ShoppingGoal.model_validate(payload)


def merge_goal_delta(
    current: ShoppingGoal,
    delta: GoalDelta,
    *,
    candidate_status: CandidateStatus = CandidateStatus.UNCHANGED,
    reference_time: datetime | None = None,
    long_term_preferences: Sequence[Preference] = (),
) -> GoalMergeResult:
    """Merge one validated turn without model, I/O, persistence, or wall-clock reads."""

    current, delta, long_term_preferences = _validate_merge_inputs(
        current,
        delta,
        long_term_preferences,
        reference_time,
    )
    try:
        candidate_status = CandidateStatus(candidate_status)
    except ValueError as exc:
        raise GoalMergeRejected(
            f"unknown candidate status: {candidate_status}"
        ) from exc

    collections: dict[str, list[GoalValueItem]] = {
        name: list(getattr(current, name)) for name in _KIND_BY_COLLECTION
    }
    current_semantics = _semantic_state(collections)
    _seed_long_term_preferences(collections, long_term_preferences)
    original = deepcopy(collections)

    winner_rank: dict[tuple[str, SemanticKey], int] = {}
    for collection_name, values in collections.items():
        kind = _KIND_BY_COLLECTION[collection_name]
        for item in values:
            winner_rank[(kind, item.semantic_key)] = _source_rank(
                item.evidence, historical=True
            )

    indexed_operations = sorted(
        enumerate(delta.operations),
        key=lambda indexed: (-_operation_rank(indexed[1]), indexed[0]),
    )
    events: list[GoalChangeEvent] = []
    for _, operation in indexed_operations:
        if isinstance(operation, GoalValueMutation):
            event = _apply_value_mutation(
                collections,
                operation,
                delta.source_turn,
                winner_rank,
            )
        else:
            event = _apply_remove_mutation(
                collections,
                operation,
                delta.source_turn,
                winner_rank,
            )
        events.append(event)

    effective_reference_time = _reference_time_from_delta(delta, reference_time)
    conflicts = _detect_conflicts(collections, events, effective_reference_time)
    rolled_back_keys = _rollback_conflicts(collections, original, conflicts)
    marked_events = _mark_conflict_events(events, conflicts, rolled_back_keys)

    meaning_changed = _semantic_state(collections) != current_semantics
    target_stage, stage_reason = _target_stage(
        current,
        collections,
        conflicts,
        candidate_status,
        meaning_changed,
    )
    goal = _build_goal(current, collections, target_stage, stage_reason)
    return GoalMergeResult(
        goal=goal,
        conflicts=conflicts,
        events=marked_events,
    )
