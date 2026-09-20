"""Turn-scoped, evidence-backed extraction of shopping-goal mutations."""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    ValidationError,
    model_validator,
)

from app.routers.AImodel.schemas import AiModelPageContext
from app.routers.AImodel.shopping_goal import (
    Constraint,
    Exclusion,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    OpenSlot,
    Preference,
)


MAX_DELTA_OPERATIONS = 64
MAX_MODEL_SUGGESTIONS = 32
MAX_EXTRACTION_TEXT = 8_000

ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
GoalItemKind = Literal["hard", "soft", "exclude", "unknown"]
GoalValueItem = Annotated[
    Constraint | Preference | Exclusion | OpenSlot,
    Field(discriminator="kind"),
]


class DeltaAction(StrEnum):
    """Describe how B3 must apply one turn-scoped mutation."""

    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"
    CONFIRM = "confirm"


class GoalValueMutation(BaseModel):
    """Add, replace, or confirm one fully validated B1 goal item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal[
        DeltaAction.ADD,
        DeltaAction.REPLACE,
        DeltaAction.CONFIRM,
    ]
    item: GoalValueItem
    source_span: ShortText

    @model_validator(mode="after")
    def validate_source_span(self) -> Self:
        evidence = self.item.evidence
        if (
            evidence.source_type
            in {
                GoalSourceType.USER_TURN,
                GoalSourceType.PAGE_CONTEXT,
            }
            and self.source_span != evidence.quote
        ):
            raise ValueError("rule source_span must equal its evidence quote")
        if (
            evidence.source_type is GoalSourceType.MODEL_INFERENCE
            and evidence.quote is not None
        ):
            raise ValueError("model inference must not store source_span as a quote")
        return self


class GoalRemoveMutation(BaseModel):
    """Remove selected semantic kinds without reconstructing historical values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal[DeltaAction.REMOVE] = DeltaAction.REMOVE
    field: GoalField
    attribute: ShortText | None = None
    target_kinds: tuple[GoalItemKind, ...] = Field(min_length=1, max_length=4)
    evidence: GoalEvidence

    @model_validator(mode="after")
    def validate_selector(self) -> Self:
        if len(self.target_kinds) != len(set(self.target_kinds)):
            raise ValueError("remove target_kinds must be unique")
        if self.field is GoalField.SPECIFICATION and self.attribute is None:
            raise ValueError("specification removal requires an attribute")
        if self.field is not GoalField.SPECIFICATION and self.attribute is not None:
            raise ValueError("attribute is only valid for specification removal")
        if self.evidence.source_type is not GoalSourceType.USER_TURN:
            raise ValueError("remove operations require explicit user evidence")
        return self


GoalMutation = Annotated[
    GoalValueMutation | GoalRemoveMutation,
    Field(discriminator="action"),
]


class GoalExtractionTrace(BaseModel):
    """Bounded observability summary with no prompt or provider response."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    duration_ms: float = Field(ge=0)
    rule_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    model_added_fields: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=32,
        alias="model_fields",
        serialization_alias="model_fields",
    )
    rejected_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    model_status: Literal[
        "not_requested",
        "success",
        "invalid",
        "error",
        "timeout",
    ] = "not_requested"
    model_error_code: Literal["model_error", "model_timeout"] | None = None

    @property
    def model_fields(self) -> tuple[str, ...]:
        return self.model_added_fields


class GoalDelta(BaseModel):
    """Immutable description of only what the current turn changed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    source_turn: Annotated[StrictInt, Field(ge=1)]
    operations: tuple[GoalMutation, ...] = Field(
        default_factory=tuple,
        max_length=MAX_DELTA_OPERATIONS,
    )
    trace: GoalExtractionTrace

    @model_validator(mode="after")
    def validate_turn_lineage_and_duplicates(self) -> Self:
        keys: set[tuple[Any, ...]] = set()
        for operation in self.operations:
            if isinstance(operation, GoalValueMutation):
                evidence = operation.item.evidence
                key = (
                    operation.action,
                    operation.item.kind,
                    operation.item.semantic_key,
                )
            else:
                evidence = operation.evidence
                key = (
                    operation.action,
                    operation.field,
                    operation.attribute.casefold() if operation.attribute else None,
                    operation.target_kinds,
                )
            if evidence.source_turn != self.source_turn:
                raise ValueError("operation evidence must match delta source_turn")
            if key in keys:
                raise ValueError("duplicate goal mutation")
            keys.add(key)
        return self


class ModelExtractionRequest(BaseModel):
    """Bounded request supplied to an optional structured model adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: Annotated[
        str, StringConstraints(min_length=1, max_length=MAX_EXTRACTION_TEXT)
    ]
    source_turn: Annotated[StrictInt, Field(ge=1)]
    page_context: AiModelPageContext | None = None
    rule_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=64)


class _ModelSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["soft", "unknown"]
    field: GoalField
    value: Any | None = None
    attribute: ShortText | None = None
    question: ShortText | None = None
    source_span: ShortText
    confidence: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind == "soft" and self.value is None:
            raise ValueError("soft suggestion requires value")
        if self.kind == "unknown" and self.question is None:
            raise ValueError("unknown suggestion requires question")
        return self


class GoalExtractionFieldMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected: int = Field(ge=0)
    predicted: int = Field(ge=0)
    correct: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)


class GoalExtractionEvaluationReport(BaseModel):
    """Machine-comparable field metrics for an immutable extraction fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    fixture_name: str
    fixture_version: str
    case_count: int = Field(ge=1)
    fields: dict[str, GoalExtractionFieldMetrics]
    micro: GoalExtractionFieldMetrics


_CATEGORY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"无线耳机|耳机|空气炸锅|空气净化器|电视|手机|电子产品|"
            r"扫拖机器人|电水壶"
        ),
        "electronics",
    ),
    (re.compile(r"婴儿车|母婴"), "baby_kids"),
    (re.compile(r"中性笔|办公耗材|复印纸|办公用品"), "office_supply"),
    (re.compile(r"饮料|牛奶|酸奶"), "beverage"),
)
_BRANDS: dict[str, str] = {
    "小米": "Xiaomi",
    "苹果": "Apple",
    "华为": "Huawei",
    "索尼": "Sony",
    "海尔": "Haier",
    "美的": "Midea",
}
_BRAND_TEXT = "|".join(re.escape(name) for name in _BRANDS)
_BRAND_EXCLUSION = re.compile(
    rf"(?P<span>(?:不要|排除|不考虑|不买)\s*(?P<brand>{_BRAND_TEXT}))"
)
_BRAND_EXCLUSION_POSTFIX = re.compile(
    rf"(?P<span>(?P<brand>{_BRAND_TEXT})\s*(?:不要|排除|不考虑|不买)(?:了)?)"
    r"(?=\s*[，。！？]?\s*$)"
)
_BRAND_HARD_INCLUDE = re.compile(
    rf"(?P<span>(?:只看|只要|认准|必须(?:选择)?|就要)\s*(?P<brand>{_BRAND_TEXT}))"
)
_BRAND_ANY = re.compile(rf"(?P<brand>{_BRAND_TEXT})")
_BRAND_WITHDRAW = re.compile(r"品牌无所谓|撤销品牌偏好|取消品牌(?:偏好|限制)")
_BUDGET_CORRECTION = re.compile(
    r"(?P<span>预算(?:上限)?\s*(?:不是\s*[\d,.]+\s*[,，]?\s*是|"
    r"改成|调整为|改为)\s*(?P<value>[\d,.]+)(?:\s*元)?(?:\s*(?:以内|以下))?)"
)
_BUDGET_RANGE = re.compile(
    r"预算[^\d]{0,8}(?P<span>(?P<minimum>[\d,.]+)\s*(?:到|至|[-~—])\s*"
    r"(?P<maximum>[\d,.]+))(?:\s*元)?"
)
_BUDGET_MAX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?P<span>(?:预算|总价)?\s*(?:不能超过|不超过|最高|至多|上限)"
        r"(?:\s*(?:仍然)?(?:是|为))?\s*(?P<value>[\d,.]+))(?:\s*元)?"
    ),
    re.compile(
        r"(?P<span>(?:预算|总价)\s*(?:是|为|大约)?\s*(?P<value>[\d,.]+)"
        r"(?:\s*元)?\s*(?:以内|以下|封顶))"
    ),
    re.compile(
        r"(?P<span>预算(?:上限)?\s*(?:仍然)?(?:是|为)?\s*"
        r"(?P<value>[\d,.]+))(?:\s*元)?"
    ),
)
_BUDGET_MIN = re.compile(
    r"(?P<span>(?:(?:预算|价格)\s*(?:不能低于|不低于|最低|至少)|最低预算)\s*"
    r"(?P<value>[\d,.]+))(?:\s*元)?"
)
_QUANTITY = re.compile(
    r"(?P<span>(?P<value>\d{1,3}|[一二两三四五六七八九十])\s*"
    r"(?P<unit>个|件|台|部|箱|盒|副|辆))"
)
_QUANTITY_CORRECTION = re.compile(
    r"(?P<span>不是\s*(?:\d{1,3}|[一二两三四五六七八九十])\s*"
    r"(?:个|件|台|部|箱|盒|副|辆)\s*[,，]?\s*(?:是|改成|改为)\s*"
    r"(?P<value>\d{1,3}|[一二两三四五六七八九十])\s*"
    r"(?P<unit>个|件|台|部|箱|盒|副|辆))"
)
_CAPACITY_SPEC = re.compile(
    r"(?P<span>容量[^\d]{0,8}?(?P<minimum>至少|不低于)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>L|l|升))"
)
_CAPACITY_CORRECTION = re.compile(
    r"(?P<span>容量\s*不是\s*\d+(?:\.\d+)?\s*(?:L|l|升)\s*[,，]?\s*"
    r"(?:是|改成|改为)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>L|l|升))"
)
_AREA_SPEC = re.compile(
    r"(?P<span>(?:适合\s*)?(?P<value>\d+(?:\.\d+)?)\s*(?:平方米|平米))"
)
_SCREEN_SPEC = re.compile(r"(?P<span>(?P<value>\d{2,3})\s*英寸)")
_DELIVERY_HOURS = re.compile(
    r"(?P<span>(?P<hours>\d+|一|两|二|三|四|五|六|七|八|九|十)\s*"
    r"(?:个)?小时内)"
)
_DELIVERY_DAY = re.compile(
    r"(?P<span>(?P<day>今天|明天|后天)(?:\s*(?P<hour>\d{1,2})\s*点)?)"
)
_CHEAP_PREFERENCE = re.compile(r"越便宜越好|尽量便宜|价格越低越好")
_SCENARIO_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"宿舍(?:里)?打游戏"), "宿舍打游戏"),
    (re.compile(r"办公室(?:里)?(?:使用|用)"), "办公室使用"),
    (re.compile(r"通勤(?:使用|用)?"), "通勤"),
    (re.compile(r"送人|送礼"), "送礼"),
)
_DEICTIC_REFERENCE = re.compile(
    r"(?:这款|这台|这辆|这个|该款|它)(?:的)?"
    r"(?:容量|规格|尺寸|价格|适用|怎么样|能买吗|好不好)"
)
_CONFIRMATION_PREFIX = re.compile(r"^(?:是[,，。]?|确认)")
_FIELD_REPLACEMENT_PATTERNS: dict[GoalField, re.Pattern[str]] = {
    GoalField.CATEGORY: re.compile(r"(?:品类|类别|商品)\S{0,8}(?:改成|改为|调整为)"),
    GoalField.USAGE_SCENARIO: re.compile(
        r"(?:场景|用途|使用)\S{0,8}(?:改成|改为|调整为)"
    ),
    GoalField.BUDGET_MIN: re.compile(
        r"(?:预算|价格|总价).*(?:改成|改为|调整为|不是.+是)"
    ),
    GoalField.BUDGET_MAX: re.compile(
        r"(?:预算|价格|总价).*(?:改成|改为|调整为|不是.+是)"
    ),
    GoalField.BRAND: re.compile(r"(?:品牌|牌子)\S{0,8}(?:改成|改为|调整为)"),
    GoalField.SPECIFICATION: re.compile(
        r"(?:规格|容量|面积|尺寸|内存|存储)\S{0,8}(?:改成|改为|调整为)"
    ),
    GoalField.DELIVERY_DEADLINE: re.compile(
        r"(?:配送|送达|时间)\S{0,8}(?:改成|改为|调整为)"
    ),
    GoalField.QUANTITY: re.compile(r"(?:数量|件数)\S{0,8}(?:改成|改为|调整为)"),
}
_FIELD_CONFIRMATION_PATTERNS: dict[GoalField, re.Pattern[str]] = {
    GoalField.BUDGET_MIN: re.compile(r"(?:预算|价格|总价).*(?:仍然|还是)"),
    GoalField.BUDGET_MAX: re.compile(r"(?:预算|价格|总价).*(?:仍然|还是)"),
    GoalField.BRAND: re.compile(r"(?:品牌|牌子).*(?:仍然|还是)"),
    GoalField.SPECIFICATION: re.compile(r"(?:规格|容量|面积|尺寸).*(?:仍然|还是)"),
    GoalField.DELIVERY_DEADLINE: re.compile(r"(?:配送|送达|时间).*(?:仍然|还是)"),
    GoalField.QUANTITY: re.compile(r"(?:数量|件数).*(?:仍然|还是)"),
}
_FIELD_CONFIRMATION_ANSWER_PATTERNS: dict[GoalField, re.Pattern[str]] = {
    GoalField.CATEGORY: re.compile(r"品类|类别|商品"),
    GoalField.USAGE_SCENARIO: re.compile(r"场景|用途|使用"),
    GoalField.BUDGET_MIN: re.compile(r"预算|价格|总价"),
    GoalField.BUDGET_MAX: re.compile(r"预算|价格|总价"),
    GoalField.BRAND: re.compile(r"品牌|牌子"),
    GoalField.SPECIFICATION: re.compile(r"规格|容量|面积|尺寸|内存|存储"),
    GoalField.DELIVERY_DEADLINE: re.compile(r"不能晚于|送达|送到|配送|到货|收到"),
    GoalField.QUANTITY: re.compile(r"数量|件数"),
}
_CATEGORY_NEGATION_PREFIX = re.compile(r"(?:不要|不买|排除|不考虑)\s*$")
_CATEGORY_NEGATION_SUFFIX = re.compile(
    r"^\s*(?:不要|不买|排除|不考虑)(?:了)?\s*[，。！？]?\s*$"
)
_DELIVERY_INTENT = re.compile(r"不能晚于|送达|送到|配送|到货|收到")
_CHINESE_NUMBER = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _action_for(text: str, field: GoalField) -> DeltaAction:
    replacement = _FIELD_REPLACEMENT_PATTERNS.get(field)
    if replacement is not None and replacement.search(text):
        return DeltaAction.REPLACE
    confirmation = _FIELD_CONFIRMATION_PATTERNS.get(field)
    answer_pattern = _FIELD_CONFIRMATION_ANSWER_PATTERNS.get(field)
    explicit_answer = (
        _CONFIRMATION_PREFIX.search(text) is not None
        and answer_pattern is not None
        and answer_pattern.search(text) is not None
    )
    if explicit_answer or (confirmation is not None and confirmation.search(text)):
        return DeltaAction.CONFIRM
    return DeltaAction.ADD


def _evidence(
    *,
    source_type: GoalSourceType,
    source_turn: int,
    quote: str | None,
    confidence: float,
    observed_at: datetime,
) -> GoalEvidence:
    timestamp = (
        observed_at
        if observed_at.tzinfo is not None
        else observed_at.replace(tzinfo=UTC)
    )
    return GoalEvidence(
        source_type=source_type,
        source_turn=source_turn,
        quote=quote,
        confidence=confidence,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _constraint(
    field: GoalField,
    value: Any,
    *,
    quote: str,
    source_turn: int,
    observed_at: datetime,
    attribute: str | None = None,
    source_type: GoalSourceType = GoalSourceType.USER_TURN,
    confidence: float = 1.0,
) -> Constraint:
    return Constraint(
        field=field,
        value=value,
        attribute=attribute,
        evidence=_evidence(
            source_type=source_type,
            source_turn=source_turn,
            quote=quote,
            confidence=confidence,
            observed_at=observed_at,
        ),
    )


def _decimal(raw: str) -> Decimal:
    return Decimal(raw.replace(",", ""))


def _append_value(
    operations: list[GoalMutation],
    *,
    action: DeltaAction,
    item: GoalValueItem,
    source_span: str | None = None,
) -> None:
    key = (item.kind, item.semantic_key)
    for existing in operations:
        if isinstance(existing, GoalValueMutation):
            if (existing.item.kind, existing.item.semantic_key) == key:
                return
    resolved_span = source_span or item.evidence.quote
    if not resolved_span:
        raise ValueError("goal value mutation requires a source_span")
    operations.append(
        GoalValueMutation(
            action=action,
            item=item,
            source_span=resolved_span,
        )
    )


def _extract_categories(
    text: str,
    *,
    source_turn: int,
    page_context: AiModelPageContext | None,
    observed_at: datetime,
    operations: list[GoalMutation],
) -> None:
    for pattern, category_id in _CATEGORY_PATTERNS:
        match = pattern.search(text)
        if match:
            prefix = text[: match.start()]
            suffix = text[match.end() :]
            prefix_negation = _CATEGORY_NEGATION_PREFIX.search(prefix)
            suffix_negation = _CATEGORY_NEGATION_SUFFIX.search(suffix)
            if prefix_negation or suffix_negation:
                if prefix_negation:
                    quote = text[prefix_negation.start() : match.end()]
                else:
                    quote = text[match.start() : match.end() + suffix_negation.end()]
                _append_value(
                    operations,
                    action=DeltaAction.ADD,
                    item=Exclusion(
                        field=GoalField.CATEGORY,
                        value=match.group(0),
                        evidence=_evidence(
                            source_type=GoalSourceType.USER_TURN,
                            source_turn=source_turn,
                            quote=quote,
                            confidence=1.0,
                            observed_at=observed_at,
                        ),
                    ),
                )
                return
            _append_value(
                operations,
                action=_action_for(text, GoalField.CATEGORY),
                item=_constraint(
                    GoalField.CATEGORY,
                    category_id,
                    quote=match.group(0),
                    source_turn=source_turn,
                    observed_at=observed_at,
                ),
            )
            return

    if (
        page_context is None
        or not page_context.search_query
        or not _DEICTIC_REFERENCE.search(text)
    ):
        return
    for pattern, category_id in _CATEGORY_PATTERNS:
        match = pattern.search(page_context.search_query)
        if match:
            _append_value(
                operations,
                action=_action_for(text, GoalField.CATEGORY),
                item=_constraint(
                    GoalField.CATEGORY,
                    category_id,
                    quote=match.group(0),
                    source_turn=source_turn,
                    observed_at=observed_at,
                    source_type=GoalSourceType.PAGE_CONTEXT,
                    confidence=0.95,
                ),
            )
            return


def _extract_budget(
    text: str,
    *,
    source_turn: int,
    observed_at: datetime,
    operations: list[GoalMutation],
) -> None:
    correction = _BUDGET_CORRECTION.search(text)
    if correction:
        _append_value(
            operations,
            action=DeltaAction.REPLACE,
            item=_constraint(
                GoalField.BUDGET_MAX,
                _decimal(correction.group("value")),
                quote=correction.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )
        return

    budget_range = _BUDGET_RANGE.search(text)
    if budget_range:
        for field, group in (
            (GoalField.BUDGET_MIN, "minimum"),
            (GoalField.BUDGET_MAX, "maximum"),
        ):
            _append_value(
                operations,
                action=_action_for(text, field),
                item=_constraint(
                    field,
                    _decimal(budget_range.group(group)),
                    quote=budget_range.group("span"),
                    source_turn=source_turn,
                    observed_at=observed_at,
                ),
            )
        return

    minimum = _BUDGET_MIN.search(text)
    if minimum:
        _append_value(
            operations,
            action=_action_for(text, GoalField.BUDGET_MIN),
            item=_constraint(
                GoalField.BUDGET_MIN,
                _decimal(minimum.group("value")),
                quote=minimum.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )

    for pattern in _BUDGET_MAX_PATTERNS:
        maximum = pattern.search(text)
        if maximum:
            _append_value(
                operations,
                action=_action_for(text, GoalField.BUDGET_MAX),
                item=_constraint(
                    GoalField.BUDGET_MAX,
                    _decimal(maximum.group("value")),
                    quote=maximum.group("span"),
                    source_turn=source_turn,
                    observed_at=observed_at,
                ),
            )
            return


def _extract_brands(
    text: str,
    *,
    source_turn: int,
    observed_at: datetime,
    operations: list[GoalMutation],
) -> None:
    withdrawal = _BRAND_WITHDRAW.search(text)
    if withdrawal:
        operations.append(
            GoalRemoveMutation(
                field=GoalField.BRAND,
                target_kinds=("hard", "soft", "exclude"),
                evidence=_evidence(
                    source_type=GoalSourceType.USER_TURN,
                    source_turn=source_turn,
                    quote=withdrawal.group(0),
                    confidence=1.0,
                    observed_at=observed_at,
                ),
            )
        )

    consumed_spans: list[tuple[int, int]] = []
    for match in _BRAND_EXCLUSION.finditer(text):
        consumed_spans.append(match.span("brand"))
        brand = _BRANDS[match.group("brand")]
        _append_value(
            operations,
            action=_action_for(text, GoalField.BRAND),
            item=Exclusion(
                field=GoalField.BRAND,
                value=brand,
                evidence=_evidence(
                    source_type=GoalSourceType.USER_TURN,
                    source_turn=source_turn,
                    quote=match.group("span"),
                    confidence=1.0,
                    observed_at=observed_at,
                ),
            ),
        )

    for match in _BRAND_EXCLUSION_POSTFIX.finditer(text):
        if any(start <= match.start("brand") < end for start, end in consumed_spans):
            continue
        consumed_spans.append(match.span("brand"))
        brand = _BRANDS[match.group("brand")]
        _append_value(
            operations,
            action=DeltaAction.ADD,
            item=Exclusion(
                field=GoalField.BRAND,
                value=brand,
                evidence=_evidence(
                    source_type=GoalSourceType.USER_TURN,
                    source_turn=source_turn,
                    quote=match.group("span"),
                    confidence=1.0,
                    observed_at=observed_at,
                ),
            ),
        )

    for match in _BRAND_HARD_INCLUDE.finditer(text):
        if any(start <= match.start("brand") < end for start, end in consumed_spans):
            continue
        consumed_spans.append(match.span("brand"))
        _append_value(
            operations,
            action=_action_for(text, GoalField.BRAND),
            item=_constraint(
                GoalField.BRAND,
                _BRANDS[match.group("brand")],
                quote=match.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )

    for match in _BRAND_ANY.finditer(text):
        if any(start <= match.start("brand") < end for start, end in consumed_spans):
            continue
        _append_value(
            operations,
            action=_action_for(text, GoalField.BRAND),
            item=Preference(
                field=GoalField.BRAND,
                value=_BRANDS[match.group("brand")],
                evidence=_evidence(
                    source_type=GoalSourceType.USER_TURN,
                    source_turn=source_turn,
                    quote=match.group("brand"),
                    confidence=1.0,
                    observed_at=observed_at,
                ),
            ),
        )


def _extract_quantity_specs_and_scenario(
    text: str,
    *,
    source_turn: int,
    observed_at: datetime,
    operations: list[GoalMutation],
) -> None:
    quantity_correction = _QUANTITY_CORRECTION.search(text)
    quantity = None if quantity_correction else _QUANTITY.search(text)
    if quantity_correction:
        raw_value = quantity_correction.group("value")
        value = int(raw_value) if raw_value.isdigit() else _CHINESE_NUMBER[raw_value]
        _append_value(
            operations,
            action=DeltaAction.REPLACE,
            item=_constraint(
                GoalField.QUANTITY,
                value,
                quote=quantity_correction.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )
    elif quantity:
        raw_value = quantity.group("value")
        value = int(raw_value) if raw_value.isdigit() else _CHINESE_NUMBER[raw_value]
        _append_value(
            operations,
            action=_action_for(text, GoalField.QUANTITY),
            item=_constraint(
                GoalField.QUANTITY,
                value,
                quote=quantity.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )

    capacity_correction = _CAPACITY_CORRECTION.search(text)
    capacity = None if capacity_correction else _CAPACITY_SPEC.search(text)
    if capacity_correction:
        unit = "L" if capacity_correction.group("unit").lower() == "l" else "升"
        _append_value(
            operations,
            action=DeltaAction.REPLACE,
            item=_constraint(
                GoalField.SPECIFICATION,
                f"{capacity_correction.group('value')}{unit}",
                attribute="capacity",
                quote=capacity_correction.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )
    elif capacity:
        prefix = "至少 " if capacity.group("minimum") else ""
        unit = "L" if capacity.group("unit").lower() == "l" else "升"
        _append_value(
            operations,
            action=_action_for(text, GoalField.SPECIFICATION),
            item=_constraint(
                GoalField.SPECIFICATION,
                f"{prefix}{capacity.group('value')}{unit}",
                attribute="capacity",
                quote=capacity.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )

    area = _AREA_SPEC.search(text)
    if area:
        _append_value(
            operations,
            action=_action_for(text, GoalField.SPECIFICATION),
            item=_constraint(
                GoalField.SPECIFICATION,
                f"至少 {area.group('value')} 平方米",
                attribute="room_area",
                quote=area.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )

    screen = _SCREEN_SPEC.search(text)
    if screen:
        _append_value(
            operations,
            action=_action_for(text, GoalField.SPECIFICATION),
            item=_constraint(
                GoalField.SPECIFICATION,
                f"{screen.group('value')} 英寸",
                attribute="screen_size",
                quote=screen.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )

    for pattern, normalized in _SCENARIO_RULES:
        scenario = pattern.search(text)
        if scenario:
            _append_value(
                operations,
                action=_action_for(text, GoalField.USAGE_SCENARIO),
                item=_constraint(
                    GoalField.USAGE_SCENARIO,
                    normalized,
                    quote=scenario.group(0),
                    source_turn=source_turn,
                    observed_at=observed_at,
                ),
            )
            break

    cheap = _CHEAP_PREFERENCE.search(text)
    if cheap:
        _append_value(
            operations,
            action=_action_for(text, GoalField.FREEFORM_PREFERENCE),
            item=Preference(
                field=GoalField.FREEFORM_PREFERENCE,
                value=cheap.group(0),
                evidence=_evidence(
                    source_type=GoalSourceType.USER_TURN,
                    source_turn=source_turn,
                    quote=cheap.group(0),
                    confidence=1.0,
                    observed_at=observed_at,
                ),
            ),
        )


def _extract_delivery(
    text: str,
    *,
    source_turn: int,
    observed_at: datetime,
    operations: list[GoalMutation],
) -> None:
    if not _DELIVERY_INTENT.search(text):
        return
    hours = _DELIVERY_HOURS.search(text)
    if hours:
        raw_hours = hours.group("hours")
        count = int(raw_hours) if raw_hours.isdigit() else _CHINESE_NUMBER[raw_hours]
        deadline = observed_at + timedelta(hours=count)
        _append_value(
            operations,
            action=_action_for(text, GoalField.DELIVERY_DEADLINE),
            item=_constraint(
                GoalField.DELIVERY_DEADLINE,
                deadline,
                quote=hours.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )
        return

    day = _DELIVERY_DAY.search(text)
    if not day:
        return
    offset = {"今天": 0, "明天": 1, "后天": 2}[day.group("day")]
    deadline_date = (observed_at + timedelta(days=offset)).date()
    hour = int(day.group("hour")) if day.group("hour") else 23
    minute = 0 if day.group("hour") else 59
    second = 0 if day.group("hour") else 59
    deadline = datetime.combine(deadline_date, datetime.min.time(), observed_at.tzinfo)
    deadline = deadline.replace(hour=hour, minute=minute, second=second)
    _append_value(
        operations,
        action=_action_for(text, GoalField.DELIVERY_DEADLINE),
        item=_constraint(
            GoalField.DELIVERY_DEADLINE,
            deadline,
            quote=day.group("span"),
            source_turn=source_turn,
            observed_at=observed_at,
        ),
    )


def _rule_extract(
    text: str,
    *,
    source_turn: int,
    page_context: AiModelPageContext | None,
    observed_at: datetime,
) -> list[GoalMutation]:
    operations: list[GoalMutation] = []
    _extract_categories(
        text,
        source_turn=source_turn,
        page_context=page_context,
        observed_at=observed_at,
        operations=operations,
    )
    _extract_budget(
        text,
        source_turn=source_turn,
        observed_at=observed_at,
        operations=operations,
    )
    _extract_brands(
        text,
        source_turn=source_turn,
        observed_at=observed_at,
        operations=operations,
    )
    _extract_quantity_specs_and_scenario(
        text,
        source_turn=source_turn,
        observed_at=observed_at,
        operations=operations,
    )
    _extract_delivery(
        text,
        source_turn=source_turn,
        observed_at=observed_at,
        operations=operations,
    )
    return operations


def _operation_field(operation: GoalMutation) -> str:
    if isinstance(operation, GoalValueMutation):
        return operation.item.field.value
    return operation.field.value


def _invoke_model(
    model_extractor: Callable[[ModelExtractionRequest], object],
    request: ModelExtractionRequest,
    *,
    timeout_seconds: float,
) -> object:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="goal-extractor")
    future = executor.submit(model_extractor, request)
    try:
        return future.result(timeout=timeout_seconds)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _extract_model_operations(
    raw_response: object,
    *,
    text: str,
    source_turn: int,
    observed_at: datetime,
    existing: Sequence[GoalMutation],
) -> tuple[list[GoalMutation], list[str], list[str]]:
    if isinstance(raw_response, BaseModel):
        raw_response = raw_response.model_dump(mode="python")
    if not isinstance(raw_response, Mapping):
        return [], [], ["model_output"]
    raw_suggestions = raw_response.get("suggestions")
    if (
        not isinstance(raw_suggestions, list)
        or len(raw_suggestions) > MAX_MODEL_SUGGESTIONS
    ):
        return [], [], ["model_output"]

    operations: list[GoalMutation] = []
    accepted: list[str] = []
    rejected: list[str] = []
    existing_keys = {
        operation.item.semantic_key
        for operation in existing
        if isinstance(operation, GoalValueMutation)
    }
    for raw in raw_suggestions:
        raw_field = raw.get("field") if isinstance(raw, Mapping) else None
        try:
            rejected_name = GoalField(raw_field).value
        except (TypeError, ValueError):
            rejected_name = "model_output"
        try:
            suggestion = _ModelSuggestion.model_validate(raw)
            if suggestion.source_span not in text:
                raise ValueError("model source_span is not present in the current turn")
            evidence = _evidence(
                source_type=GoalSourceType.MODEL_INFERENCE,
                source_turn=source_turn,
                quote=None,
                confidence=suggestion.confidence,
                observed_at=observed_at,
            )
            if suggestion.kind == "soft":
                item: GoalValueItem = Preference(
                    field=suggestion.field,
                    value=suggestion.value,
                    attribute=suggestion.attribute,
                    evidence=evidence,
                )
            else:
                item = OpenSlot(
                    field=suggestion.field,
                    attribute=suggestion.attribute,
                    question=suggestion.question,
                    evidence=evidence,
                )
            if item.semantic_key in existing_keys:
                raise ValueError("model suggestion duplicates a rule field")
            operation = GoalValueMutation(
                action=DeltaAction.ADD,
                item=item,
                source_span=suggestion.source_span,
            )
        except (ValidationError, TypeError, ValueError):
            rejected.append(rejected_name)
            continue
        operations.append(operation)
        existing_keys.add(item.semantic_key)
        accepted.append(item.field.value)
    return operations, accepted, rejected


def extract_goal_delta(
    text: str,
    *,
    source_turn: int,
    page_context: AiModelPageContext | Mapping[str, Any] | None = None,
    model_extractor: Callable[[ModelExtractionRequest], object] | None = None,
    reference_time: datetime | None = None,
    model_timeout_seconds: float = 1.0,
) -> GoalDelta:
    """Extract current-turn mutations while keeping model failure non-fatal."""

    started = time.perf_counter()
    if not isinstance(text, str) or not text.strip():
        raise ValueError("extraction text cannot be blank")
    normalized_text = text.strip()
    if len(normalized_text) > MAX_EXTRACTION_TEXT:
        raise ValueError(f"extraction text exceeds {MAX_EXTRACTION_TEXT} characters")
    if (
        isinstance(source_turn, bool)
        or not isinstance(source_turn, int)
        or source_turn < 1
    ):
        raise ValueError("source_turn must be a positive integer")
    if model_timeout_seconds <= 0:
        raise ValueError("model_timeout_seconds must be positive")
    context = (
        AiModelPageContext.model_validate(page_context)
        if page_context is not None and not isinstance(page_context, AiModelPageContext)
        else page_context
    )
    observed_at = reference_time or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("reference_time must include a timezone")

    rule_operations = _rule_extract(
        normalized_text,
        source_turn=source_turn,
        page_context=context,
        observed_at=observed_at,
    )
    operations = list(rule_operations)
    model_fields: list[str] = []
    rejected_fields: list[str] = []
    model_status: str = "not_requested"
    model_error_code: str | None = None

    if model_extractor is not None:
        request = ModelExtractionRequest(
            text=normalized_text,
            source_turn=source_turn,
            page_context=context,
            rule_fields=_unique([_operation_field(item) for item in rule_operations]),
        )
        try:
            raw_response = _invoke_model(
                model_extractor,
                request,
                timeout_seconds=model_timeout_seconds,
            )
            additions, model_fields, rejected_fields = _extract_model_operations(
                raw_response,
                text=normalized_text,
                source_turn=source_turn,
                observed_at=observed_at,
                existing=operations,
            )
            operations.extend(additions)
            model_status = "invalid" if rejected_fields else "success"
        except TimeoutError:
            model_status = "timeout"
            model_error_code = "model_timeout"
        except Exception:  # The trace intentionally does not retain provider details.
            model_status = "error"
            model_error_code = "model_error"

    trace = GoalExtractionTrace(
        duration_ms=max((time.perf_counter() - started) * 1000, 0.0),
        rule_fields=_unique([_operation_field(item) for item in rule_operations]),
        model_fields=_unique(model_fields),
        rejected_fields=_unique(rejected_fields),
        model_status=model_status,
        model_error_code=model_error_code,
    )
    return GoalDelta(
        source_turn=source_turn,
        operations=tuple(operations),
        trace=trace,
    )


def _normalized_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _operation_token(operation: GoalMutation) -> tuple[str, str]:
    if isinstance(operation, GoalRemoveMutation):
        payload = {
            "action": operation.action.value,
            "field": operation.field.value,
            "attribute": operation.attribute,
            "target_kinds": list(operation.target_kinds),
        }
        return operation.field.value, json.dumps(
            payload, ensure_ascii=False, sort_keys=True
        )
    payload = {
        "action": operation.action.value,
        "kind": operation.item.kind,
        "field": operation.item.field.value,
        "attribute": operation.item.attribute,
        "value": _normalized_value(getattr(operation.item, "value", None)),
    }
    return operation.item.field.value, json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )


def _expected_token(raw: Mapping[str, Any]) -> tuple[str, str]:
    field = str(raw["field"])
    payload = dict(raw)
    payload.setdefault("attribute", None)
    if payload.get("action") == "remove":
        payload.pop("kind", None)
        payload.pop("value", None)
    return field, json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _metrics(expected: int, predicted: int, correct: int) -> GoalExtractionFieldMetrics:
    return GoalExtractionFieldMetrics(
        expected=expected,
        predicted=predicted,
        correct=correct,
        precision=correct / predicted if predicted else (1.0 if expected == 0 else 0.0),
        recall=correct / expected if expected else 1.0,
    )


def evaluate_goal_extraction_fixture(
    fixture_path: str | Path,
) -> GoalExtractionEvaluationReport:
    """Evaluate deterministic extraction against A2-linked B2 cases."""

    path = Path(fixture_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("unsupported extraction fixture schema_version")
    metadata = document.get("metadata")
    cases = document.get("cases")
    if not isinstance(metadata, dict) or not isinstance(cases, list) or not cases:
        raise ValueError("invalid extraction fixture")
    reference_time = datetime.fromisoformat(str(metadata["reference_time"]))

    expected_by_field: defaultdict[str, Counter[str]] = defaultdict(Counter)
    predicted_by_field: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("invalid extraction case")
        case_id = str(case.get("case_id") or "")
        if not case_id:
            raise ValueError("extraction case requires case_id")
        result = extract_goal_delta(
            str(case["text"]),
            source_turn=int(case["source_turn"]),
            page_context=case.get("page_context"),
            reference_time=reference_time,
        )
        for expected in case.get("expected", []):
            field, token = _expected_token(expected)
            expected_by_field[field][f"{case_id}\0{token}"] += 1
        for operation in result.operations:
            field, token = _operation_token(operation)
            predicted_by_field[field][f"{case_id}\0{token}"] += 1

    fields = sorted(set(expected_by_field) | set(predicted_by_field))
    metrics: dict[str, GoalExtractionFieldMetrics] = {}
    total_expected = total_predicted = total_correct = 0
    for field in fields:
        expected_count = sum(expected_by_field[field].values())
        predicted_count = sum(predicted_by_field[field].values())
        correct = sum((expected_by_field[field] & predicted_by_field[field]).values())
        metrics[field] = _metrics(expected_count, predicted_count, correct)
        total_expected += expected_count
        total_predicted += predicted_count
        total_correct += correct

    return GoalExtractionEvaluationReport(
        fixture_name=str(metadata["name"]),
        fixture_version=str(metadata["version"]),
        case_count=len(cases),
        fields=metrics,
        micro=_metrics(total_expected, total_predicted, total_correct),
    )
