"""Turn-scoped, evidence-backed extraction of shopping-goal mutations."""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
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
    MAX_BUDGET,
    MAX_EVIDENCE_QUOTE_LENGTH,
    MAX_GOAL_TEXT_LENGTH,
    MAX_QUANTITY,
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
    (re.compile(r"牛奶|酸奶|乳制品|奶制品"), "dairy"),
    (re.compile(r"饮料|矿泉水|可乐"), "beverage"),
)
_BRANDS: dict[str, str] = {
    "小米": "Xiaomi",
    "xiaomi": "Xiaomi",
    "苹果": "Apple",
    "apple": "Apple",
    "华为": "Huawei",
    "huawei": "Huawei",
    "索尼": "Sony",
    "sony": "Sony",
    "海尔": "Haier",
    "haier": "Haier",
    "美的": "Midea",
    "midea": "Midea",
}
_BRAND_TEXT = "|".join(re.escape(name) for name in _BRANDS)
_BRAND_EXCLUSION = re.compile(
    rf"(?P<span>(?:不要|排除|不考虑|不买)\s*(?P<brand>{_BRAND_TEXT}))",
    re.IGNORECASE,
)
_BRAND_EXCLUSION_POSTFIX = re.compile(
    rf"(?P<span>(?P<brand>{_BRAND_TEXT})\s*(?:不要|排除|不考虑|不买)(?:了)?)"
    r"(?=\s*[,，.。;；!！?？\r\n]|\s*$)",
    re.IGNORECASE,
)
_BRAND_HARD_INCLUDE = re.compile(
    rf"(?P<span>(?:只看|只要|认准|必须(?:选择)?|就要)\s*(?P<brand>{_BRAND_TEXT}))",
    re.IGNORECASE,
)
_BRAND_ANY = re.compile(rf"(?P<brand>{_BRAND_TEXT})", re.IGNORECASE)
_BRAND_WITHDRAW = re.compile(r"品牌无所谓|撤销品牌偏好|取消品牌(?:偏好|限制)")
_BUDGET_CORRECTION = re.compile(
    r"(?P<span>预算(?:上限)?\s*(?:不是\s*[+\-]?[\d,.]+\s*[,，;；]?\s*是|"
    r"改成|调整为|改为)\s*(?P<value>[+\-]?[\d,.]+)(?:\s*元)?"
    r"(?:\s*(?:以内|以下))?)"
)
_BUDGET_RANGE = re.compile(
    r"预算[^\d]{0,8}(?P<span>(?P<minimum>[+\-]?[\d,.]+)\s*"
    r"(?:到|至|[-~—])\s*(?P<maximum>[+\-]?[\d,.]+))(?:\s*元)?"
)
_BUDGET_MAX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?P<span>(?:预算|总价)?\s*(?:不能超过|不超过|最高|至多|上限)"
        r"(?:\s*(?:仍然)?(?:是|为))?\s*(?P<value>[+\-]?[\d,.]+))"
        r"(?:\s*元)?"
    ),
    re.compile(
        r"(?P<span>(?:预算|总价)\s*(?:是|为|大约)?\s*"
        r"(?P<value>[+\-]?[\d,.]+)"
        r"(?:\s*元)?\s*(?:以内|以下|封顶))"
    ),
    re.compile(
        r"(?P<span>预算(?:上限)?\s*(?:仍然)?(?:是|为)?\s*"
        r"(?P<value>[+\-]?[\d,.]+))(?:\s*元)?"
    ),
)
_BUDGET_MIN = re.compile(
    r"(?P<span>(?:(?:预算|价格)\s*(?:不能低于|不低于|最低|至少)|最低预算)\s*"
    r"(?P<value>[+\-]?[\d,.]+))(?:\s*元)?"
)
_RULE_NUMERIC_TOKEN = r"(?:[+\-]\s*)?[\d.,]+"
_CHINESE_INTEGER_TOKEN = r"[零〇一二两三四五六七八九十百千万]{1,8}"
_QUANTITY = re.compile(
    rf"(?P<span>(?P<value>{_RULE_NUMERIC_TOKEN}|{_CHINESE_INTEGER_TOKEN})"
    r"\s*(?P<unit>个|件|台|部|箱|盒|副|辆))"
)
_QUANTITY_CORRECTION = re.compile(
    rf"(?P<span>(?:(?:我)?(?:不需要|不要|不是|不买)\s*"
    rf"(?:{_RULE_NUMERIC_TOKEN}|{_CHINESE_INTEGER_TOKEN})\s*"
    r"(?:个|件|台|部|箱|盒|副|辆)|"
    rf"(?:{_RULE_NUMERIC_TOKEN}|{_CHINESE_INTEGER_TOKEN})\s*"
    r"(?:个|件|台|部|箱|盒|副|辆)"
    r"\s*(?:不要|不需要)(?:了)?)\s*(?:[,，;；]|然后)?\s*"
    r"(?:只?买|只?要|是|改成|改为)\s*"
    rf"(?P<value>{_RULE_NUMERIC_TOKEN}|{_CHINESE_INTEGER_TOKEN})\s*"
    r"(?P<unit>个|件|台|部|箱|盒|副|辆))"
)
_CAPACITY_SPEC = re.compile(
    rf"(?P<span>容量[^+\-\d.,]{{0,8}}?(?P<minimum>至少|不低于)?\s*"
    rf"(?P<value>{_RULE_NUMERIC_TOKEN})\s*(?P<unit>L|l|升))"
)
_CAPACITY_CORRECTION = re.compile(
    rf"(?P<span>容量\s*(?:不需要|不要|不是)\s*{_RULE_NUMERIC_TOKEN}\s*"
    r"(?:L|l|升)"
    r"\s*[,，;；]?\s*(?:然后|但(?:是)?|可是|不过)?\s*"
    rf"(?:只?要|是|改成|改为)\s*(?P<value>{_RULE_NUMERIC_TOKEN})\s*"
    r"(?P<unit>L|l|升))"
)
_CAPACITY_VALUE_CORRECTION = re.compile(
    rf"(?P<span>容量\s*(?:从|由)?\s*(?P<old_value>{_RULE_NUMERIC_TOKEN})"
    r"\s*(?P<old_unit>L|l|升)"
    rf"\s*(?:改成|改为|调整为|换成)\s*(?P<value>{_RULE_NUMERIC_TOKEN})"
    r"\s*(?P<unit>L|l|升))"
)
_CAPACITY_FOLLOWUP_CORRECTION = re.compile(
    rf"\s*[,，.。;；!！?？、\r\n]?\s*(?:(?:又|再|然后)\s*)?(?:容量\s*)?"
    rf"(?:(?:又|再)\s*)?(?:改成|改为|调整为|换成)\s*"
    rf"(?P<value>{_RULE_NUMERIC_TOKEN})\s*(?P<unit>L|l|升)"
)
_AREA_SPEC = re.compile(
    rf"(?P<span>(?:适合\s*)?(?P<value>{_RULE_NUMERIC_TOKEN})\s*"
    r"(?:平方米|平米))"
)
_SCREEN_SPEC = re.compile(rf"(?P<span>(?P<value>{_RULE_NUMERIC_TOKEN})\s*英寸)")
_DELIVERY_HOURS = re.compile(
    rf"(?P<span>(?P<hours>{_RULE_NUMERIC_TOKEN}|{_CHINESE_INTEGER_TOKEN})\s*"
    r"(?:个)?小时内)"
)
_DAY_PERIOD_TOKEN = r"(?:凌晨|早上|上午|中午|下午|傍晚|晚上)"
_DELIVERY_DAY = re.compile(
    rf"(?P<span>(?P<day>今天|明天|后天)(?:\s*(?P<period>{_DAY_PERIOD_TOKEN})?"
    rf"\s*(?P<hour>{_RULE_NUMERIC_TOKEN}|{_CHINESE_INTEGER_TOKEN})\s*点"
    rf"(?:\s*(?P<half>半)|\s*(?P<minute>{_RULE_NUMERIC_TOKEN}|"
    rf"{_CHINESE_INTEGER_TOKEN})\s*分?)?)?)"
    rf"(?!\s*(?:{_DAY_PERIOD_TOKEN})?\s*(?:{_RULE_NUMERIC_TOKEN}|"
    rf"{_CHINESE_INTEGER_TOKEN})\s*(?:点|[:：])|\s*(?:半|"
    rf"{_RULE_NUMERIC_TOKEN}|{_CHINESE_INTEGER_TOKEN})\s*分?)"
)
_UNSUPPORTED_DELIVERY_CLOCK = re.compile(
    rf"(?:今天|明天|后天)\s*(?:{_DAY_PERIOD_TOKEN})?\s*"
    rf"(?:{_RULE_NUMERIC_TOKEN}|{_CHINESE_INTEGER_TOKEN})\s*[:：]"
)
_VALID_BUDGET_NUMBER = re.compile(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?")
_MAX_CAPACITY = Decimal("100000")
_MAX_ROOM_AREA = Decimal("1000000")
_MAX_SCREEN_SIZE = 1000
_SPEC_CORRECTION_LINK = re.compile(r"改成|改为|调整为|换成|而不是|不是.*是")
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
    r"^\s*(?:不要|不买|排除|不考虑)(?:了)?"
    r"(?=\s*[,，.。;；!！?？\r\n]|\s*$)"
)
_DELIVERY_INTENT = re.compile(r"不能晚于|送达|送到|配送|到货|收到")
_CLAUSE_BOUNDARY = re.compile(
    r"[,，.。;；!！?？\r\n]+|另外|同时|并且|而且|但是|可是|不过|然后|"
    r"接着|随后|之后|接下来|但|却|再(?=来|添|选|加|买|要)|并(?=加|买|要)"
)
_AFFIRMATIVE_CLAUSES = {"是", "对", "没错", "确认"}
_NEGATION_BEFORE_SPAN = re.compile(
    r"(?:不用|不要|不是|不必|不需要|不买|排除|不考虑|取消)\s*$"
)
_NEGATION_AFTER_SPAN = re.compile(
    r"^\s*(?:不用|不要|不是|不必|不需要|不买|排除|不考虑|取消)"
)
_DEICTIC_ABANDONMENT = re.compile(r"先不说|不考虑|算了|不用|不看")
_DEICTIC_FOLLOWUP_ABANDONMENT = re.compile(
    r"^(?:\s*(?:[,，.。;；!！?？\r\n]+|但是|可是|不过|然后|接着|随后|"
    r"之后|接下来|但))*\s*"
    r"(?:先不说|不考虑|算了|不用|不看)"
)
_DELIVERY_CORRECTION_ACCEPTANCE = re.compile(
    r"(?:也行|可以|改成|改为|换成|调整为|就按|那就)"
)
_CHINESE_NUMBER = {
    "零": 0,
    "〇": 0,
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


def _clause_context(text: str, start: int, end: int) -> tuple[str, str]:
    boundaries = list(_CLAUSE_BOUNDARY.finditer(text))
    previous_boundary = None
    next_boundary = None
    for boundary in boundaries:
        if boundary.end() <= start:
            previous_boundary = boundary
        elif boundary.start() >= end:
            next_boundary = boundary
            break
    clause_start = previous_boundary.end() if previous_boundary else 0
    clause_end = next_boundary.start() if next_boundary else len(text)
    previous_clause = ""
    if previous_boundary:
        earlier = [
            item for item in boundaries if item.end() <= previous_boundary.start()
        ]
        previous_start = earlier[-1].end() if earlier else 0
        previous_clause = text[previous_start : previous_boundary.start()].strip()
    return text[clause_start:clause_end].strip(), previous_clause


def _span_is_negated(text: str, start: int, end: int) -> bool:
    clause, _ = _clause_context(text, start, end)
    relative_start = clause.find(text[start:end])
    if relative_start < 0:
        return False
    relative_end = relative_start + len(text[start:end])
    return (
        _NEGATION_BEFORE_SPAN.search(clause[:relative_start]) is not None
        or _NEGATION_AFTER_SPAN.search(clause[relative_end:]) is not None
    )


def _action_for(
    text: str,
    field: GoalField,
    *,
    span_start: int,
    span_end: int,
) -> DeltaAction:
    clause, previous_clause = _clause_context(text, span_start, span_end)
    replacement = _FIELD_REPLACEMENT_PATTERNS.get(field)
    if replacement is not None and replacement.search(clause):
        return DeltaAction.REPLACE
    confirmation = _FIELD_CONFIRMATION_PATTERNS.get(field)
    answer_pattern = _FIELD_CONFIRMATION_ANSWER_PATTERNS.get(field)
    explicit_answer = (
        (
            _CONFIRMATION_PREFIX.search(clause) is not None
            or previous_clause in _AFFIRMATIVE_CLAUSES
        )
        and answer_pattern is not None
        and answer_pattern.search(clause) is not None
    )
    if explicit_answer or (confirmation is not None and confirmation.search(clause)):
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


def _bounded_integer(raw: str, *, minimum: int, maximum: int) -> int | None:
    if raw.isdigit():
        if len(raw) > len(str(maximum)):
            return None
        value = int(raw)
    else:
        value = _parse_chinese_integer(raw)
        if value is None:
            return None
    return value if minimum <= value <= maximum else None


def _parse_chinese_integer(raw: str) -> int | None:
    direct = _CHINESE_NUMBER.get(raw)
    if direct is not None:
        return direct
    if raw.count("十") != 1 or any(unit in raw for unit in "百千万"):
        return None
    tens, ones = raw.split("十")
    if len(tens) > 1 or len(ones) > 1:
        return None
    tens_value = 1 if not tens else _CHINESE_NUMBER.get(tens)
    ones_value = 0 if not ones else _CHINESE_NUMBER.get(ones)
    if tens_value in {None, 0} or ones_value is None:
        return None
    return tens_value * 10 + ones_value


def _bounded_budget(raw: str) -> Decimal | None:
    if (
        len(raw) > MAX_EVIDENCE_QUOTE_LENGTH
        or _VALID_BUDGET_NUMBER.fullmatch(raw) is None
    ):
        return None
    try:
        value = _decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and 0 <= value <= MAX_BUDGET else None


def _bounded_positive_decimal(raw: str, *, maximum: Decimal) -> Decimal | None:
    if len(raw) > MAX_GOAL_TEXT_LENGTH or _VALID_BUDGET_NUMBER.fullmatch(raw) is None:
        return None
    try:
        value = _decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and 0 < value <= maximum else None


def _select_spec_candidate(
    text: str,
    pattern: re.Pattern[str],
    *,
    normalize: Callable[[re.Match[str]], str | None],
    rejected_fields: list[str],
) -> tuple[re.Match[str], str, DeltaAction] | None:
    selected: tuple[re.Match[str], str, DeltaAction] | None = None
    previous: re.Match[str] | None = None
    for candidate in pattern.finditer(text):
        linked_correction = previous is not None and _SPEC_CORRECTION_LINK.search(
            text[previous.end("span") : candidate.start("span")]
        )
        value = normalize(candidate)
        if value is None or len(candidate.group("span")) > MAX_EVIDENCE_QUOTE_LENGTH:
            rejected_fields.append(GoalField.SPECIFICATION.value)
            if linked_correction:
                selected = None
        else:
            internal_prefix = text[candidate.start("span") : candidate.start("value")]
            is_negated = _span_is_negated(
                text,
                candidate.start("span"),
                candidate.end("span"),
            ) or _NEGATION_BEFORE_SPAN.search(internal_prefix)
            if is_negated:
                previous = candidate
                continue
            action = (
                DeltaAction.REPLACE
                if linked_correction
                else _action_for(
                    text,
                    GoalField.SPECIFICATION,
                    span_start=candidate.start("span"),
                    span_end=candidate.end("span"),
                )
            )
            selected = candidate, value, action
        previous = candidate
    return selected


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
    found_explicit = False
    for pattern, category_id in _CATEGORY_PATTERNS:
        for match in pattern.finditer(text):
            found_explicit = True
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
                continue
            _append_value(
                operations,
                action=_action_for(
                    text,
                    GoalField.CATEGORY,
                    span_start=match.start(),
                    span_end=match.end(),
                ),
                item=_constraint(
                    GoalField.CATEGORY,
                    category_id,
                    quote=match.group(0),
                    source_turn=source_turn,
                    observed_at=observed_at,
                ),
            )

    if found_explicit:
        return

    deictic = _DEICTIC_REFERENCE.search(text)
    if page_context is None or not page_context.search_query or deictic is None:
        return
    deictic_clause, _ = _clause_context(text, deictic.start(), deictic.end())
    if _DEICTIC_ABANDONMENT.search(
        deictic_clause
    ) or _DEICTIC_FOLLOWUP_ABANDONMENT.search(text[deictic.end() :]):
        return
    for pattern, category_id in _CATEGORY_PATTERNS:
        match = pattern.search(page_context.search_query)
        if match:
            _append_value(
                operations,
                action=DeltaAction.ADD,
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
    rejected_fields: list[str],
) -> None:
    correction = _BUDGET_CORRECTION.search(text)
    if correction:
        value = _bounded_budget(correction.group("value"))
        if value is None or len(correction.group("span")) > MAX_EVIDENCE_QUOTE_LENGTH:
            rejected_fields.append(GoalField.BUDGET_MAX.value)
            return
        _append_value(
            operations,
            action=DeltaAction.REPLACE,
            item=_constraint(
                GoalField.BUDGET_MAX,
                value,
                quote=correction.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )
        return

    budget_range = _BUDGET_RANGE.search(text)
    if budget_range:
        minimum_value = _bounded_budget(budget_range.group("minimum"))
        maximum_value = _bounded_budget(budget_range.group("maximum"))
        invalid_range = (
            minimum_value is None
            or maximum_value is None
            or minimum_value > maximum_value
            or len(budget_range.group("span")) > MAX_EVIDENCE_QUOTE_LENGTH
        )
        if invalid_range:
            rejected_fields.extend(
                [GoalField.BUDGET_MIN.value, GoalField.BUDGET_MAX.value]
            )
            return
        for field, value in (
            (GoalField.BUDGET_MIN, minimum_value),
            (GoalField.BUDGET_MAX, maximum_value),
        ):
            _append_value(
                operations,
                action=_action_for(
                    text,
                    field,
                    span_start=budget_range.start("span"),
                    span_end=budget_range.end("span"),
                ),
                item=_constraint(
                    field,
                    value,
                    quote=budget_range.group("span"),
                    source_turn=source_turn,
                    observed_at=observed_at,
                ),
            )
        return

    minimum = _BUDGET_MIN.search(text)
    if minimum and not _span_is_negated(
        text, minimum.start("span"), minimum.end("span")
    ):
        value = _bounded_budget(minimum.group("value"))
        if value is None or len(minimum.group("span")) > MAX_EVIDENCE_QUOTE_LENGTH:
            rejected_fields.append(GoalField.BUDGET_MIN.value)
        else:
            _append_value(
                operations,
                action=_action_for(
                    text,
                    GoalField.BUDGET_MIN,
                    span_start=minimum.start("span"),
                    span_end=minimum.end("span"),
                ),
                item=_constraint(
                    GoalField.BUDGET_MIN,
                    value,
                    quote=minimum.group("span"),
                    source_turn=source_turn,
                    observed_at=observed_at,
                ),
            )

    for pattern in _BUDGET_MAX_PATTERNS:
        maximum = pattern.search(text)
        if maximum and not _span_is_negated(
            text, maximum.start("span"), maximum.end("span")
        ):
            value = _bounded_budget(maximum.group("value"))
            if value is None or len(maximum.group("span")) > MAX_EVIDENCE_QUOTE_LENGTH:
                rejected_fields.append(GoalField.BUDGET_MAX.value)
                return
            _append_value(
                operations,
                action=_action_for(
                    text,
                    GoalField.BUDGET_MAX,
                    span_start=maximum.start("span"),
                    span_end=maximum.end("span"),
                ),
                item=_constraint(
                    GoalField.BUDGET_MAX,
                    value,
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
        brand = _BRANDS[match.group("brand").casefold()]
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

    for match in _BRAND_EXCLUSION_POSTFIX.finditer(text):
        if any(start <= match.start("brand") < end for start, end in consumed_spans):
            continue
        consumed_spans.append(match.span("brand"))
        brand = _BRANDS[match.group("brand").casefold()]
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
            action=_action_for(
                text,
                GoalField.BRAND,
                span_start=match.start("span"),
                span_end=match.end("span"),
            ),
            item=_constraint(
                GoalField.BRAND,
                _BRANDS[match.group("brand").casefold()],
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
            action=_action_for(
                text,
                GoalField.BRAND,
                span_start=match.start("brand"),
                span_end=match.end("brand"),
            ),
            item=Preference(
                field=GoalField.BRAND,
                value=_BRANDS[match.group("brand").casefold()],
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
    rejected_fields: list[str],
) -> None:
    quantity_correction = _QUANTITY_CORRECTION.search(text)
    quantity = None
    quantity_value = None
    rejected_quantity = False
    if quantity_correction is None:
        for candidate in _QUANTITY.finditer(text):
            candidate_value = _bounded_integer(
                candidate.group("value"),
                minimum=1,
                maximum=MAX_QUANTITY,
            )
            if candidate_value is None:
                rejected_fields.append(GoalField.QUANTITY.value)
                continue
            if _span_is_negated(
                text,
                candidate.start("span"),
                candidate.end("span"),
            ):
                rejected_quantity = True
                continue
            quantity = candidate
            quantity_value = candidate_value
    if quantity_correction:
        raw_value = quantity_correction.group("value")
        value = _bounded_integer(raw_value, minimum=1, maximum=MAX_QUANTITY)
        if (
            value is None
            or len(quantity_correction.group("span")) > MAX_EVIDENCE_QUOTE_LENGTH
        ):
            rejected_fields.append(GoalField.QUANTITY.value)
        else:
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
    elif quantity is not None and quantity_value is not None:
        _append_value(
            operations,
            action=(
                DeltaAction.REPLACE
                if rejected_quantity
                else _action_for(
                    text,
                    GoalField.QUANTITY,
                    span_start=quantity.start("span"),
                    span_end=quantity.end("span"),
                )
            ),
            item=_constraint(
                GoalField.QUANTITY,
                quantity_value,
                quote=quantity.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )

    capacity_correction = _CAPACITY_CORRECTION.search(
        text
    ) or _CAPACITY_VALUE_CORRECTION.search(text)
    if capacity_correction:
        old_value = capacity_correction.groupdict().get("old_value")
        if (
            old_value is not None
            and _bounded_positive_decimal(
                old_value,
                maximum=_MAX_CAPACITY,
            )
            is None
        ):
            rejected_fields.append(GoalField.SPECIFICATION.value)

        selected_correction = capacity_correction
        selected_numeric_value = _bounded_positive_decimal(
            selected_correction.group("value"),
            maximum=_MAX_CAPACITY,
        )
        selected_quote = selected_correction.group("span")
        if (
            selected_numeric_value is None
            or len(selected_quote) > MAX_EVIDENCE_QUOTE_LENGTH
        ):
            rejected_fields.append(GoalField.SPECIFICATION.value)
            selected_correction = None

        cursor = capacity_correction.end()
        while followup := _CAPACITY_FOLLOWUP_CORRECTION.match(text, cursor):
            cursor = followup.end()
            followup_value = _bounded_positive_decimal(
                followup.group("value"),
                maximum=_MAX_CAPACITY,
            )
            if (
                followup_value is None
                or len(followup.group(0).strip()) > MAX_EVIDENCE_QUOTE_LENGTH
            ):
                rejected_fields.append(GoalField.SPECIFICATION.value)
                selected_correction = None
                continue
            selected_correction = followup
            selected_numeric_value = followup_value
            selected_quote = followup.group(0).strip()

        if selected_correction is not None and selected_numeric_value is not None:
            unit = "L" if selected_correction.group("unit").lower() == "l" else "升"
            value = f"{selected_numeric_value}{unit}"
            _append_value(
                operations,
                action=DeltaAction.REPLACE,
                item=_constraint(
                    GoalField.SPECIFICATION,
                    value,
                    attribute="capacity",
                    quote=selected_quote,
                    source_turn=source_turn,
                    observed_at=observed_at,
                ),
            )

    else:

        def normalize_capacity(candidate: re.Match[str]) -> str | None:
            numeric_value = _bounded_positive_decimal(
                candidate.group("value"),
                maximum=_MAX_CAPACITY,
            )
            if numeric_value is None:
                return None
            prefix = "至少 " if candidate.group("minimum") else ""
            unit = "L" if candidate.group("unit").lower() == "l" else "升"
            return f"{prefix}{numeric_value}{unit}"

        selected_capacity = _select_spec_candidate(
            text,
            _CAPACITY_SPEC,
            normalize=normalize_capacity,
            rejected_fields=rejected_fields,
        )
        if selected_capacity is not None:
            capacity, value, action = selected_capacity
            _append_value(
                operations,
                action=action,
                item=_constraint(
                    GoalField.SPECIFICATION,
                    value,
                    attribute="capacity",
                    quote=capacity.group("span"),
                    source_turn=source_turn,
                    observed_at=observed_at,
                ),
            )

    def normalize_area(candidate: re.Match[str]) -> str | None:
        numeric_value = _bounded_positive_decimal(
            candidate.group("value"),
            maximum=_MAX_ROOM_AREA,
        )
        return f"至少 {numeric_value} 平方米" if numeric_value is not None else None

    selected_area = _select_spec_candidate(
        text,
        _AREA_SPEC,
        normalize=normalize_area,
        rejected_fields=rejected_fields,
    )
    if selected_area is not None:
        area, value, action = selected_area
        _append_value(
            operations,
            action=action,
            item=_constraint(
                GoalField.SPECIFICATION,
                value,
                attribute="room_area",
                quote=area.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )

    def normalize_screen(candidate: re.Match[str]) -> str | None:
        numeric_value = _bounded_integer(
            candidate.group("value"),
            minimum=1,
            maximum=_MAX_SCREEN_SIZE,
        )
        return f"{numeric_value} 英寸" if numeric_value is not None else None

    selected_screen = _select_spec_candidate(
        text,
        _SCREEN_SPEC,
        normalize=normalize_screen,
        rejected_fields=rejected_fields,
    )
    if selected_screen is not None:
        screen, value, action = selected_screen
        _append_value(
            operations,
            action=action,
            item=_constraint(
                GoalField.SPECIFICATION,
                value,
                attribute="screen_size",
                quote=screen.group("span"),
                source_turn=source_turn,
                observed_at=observed_at,
            ),
        )

    for pattern, normalized in _SCENARIO_RULES:
        scenario = pattern.search(text)
        if scenario and not _span_is_negated(text, scenario.start(), scenario.end()):
            _append_value(
                operations,
                action=_action_for(
                    text,
                    GoalField.USAGE_SCENARIO,
                    span_start=scenario.start(),
                    span_end=scenario.end(),
                ),
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
    if cheap and not _span_is_negated(text, cheap.start(), cheap.end()):
        _append_value(
            operations,
            action=_action_for(
                text,
                GoalField.FREEFORM_PREFERENCE,
                span_start=cheap.start(),
                span_end=cheap.end(),
            ),
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
    rejected_fields: list[str],
) -> None:
    if not _DELIVERY_INTENT.search(text):
        return

    candidates = [
        *(("hours", candidate) for candidate in _DELIVERY_HOURS.finditer(text)),
        *(("day", candidate) for candidate in _DELIVERY_DAY.finditer(text)),
    ]
    candidates.sort(key=lambda item: item[1].start("span"))
    for unsupported_clock in _UNSUPPORTED_DELIVERY_CLOCK.finditer(text):
        clause, _ = _clause_context(
            text,
            unsupported_clock.start(),
            unsupported_clock.end(),
        )
        if _DELIVERY_INTENT.search(clause):
            rejected_fields.append(GoalField.DELIVERY_DEADLINE.value)

    rejected_deadline = False
    selected = None
    selected_deadline = None
    selected_replaces_rejected = False
    for candidate_kind, candidate in candidates:
        clause, _ = _clause_context(
            text,
            candidate.start("span"),
            candidate.end("span"),
        )
        has_local_intent = _DELIVERY_INTENT.search(clause) is not None
        if has_local_intent and _span_is_negated(
            text,
            candidate.start("span"),
            candidate.end("span"),
        ):
            rejected_deadline = True
            continue
        is_explicit_correction = (
            rejected_deadline
            and _DELIVERY_CORRECTION_ACCEPTANCE.search(clause) is not None
        )
        if not has_local_intent and not is_explicit_correction:
            continue

        if candidate_kind == "hours":
            count = _bounded_integer(
                candidate.group("hours"),
                minimum=1,
                maximum=999_999_999,
            )
            try:
                deadline = (
                    observed_at + timedelta(hours=count) if count is not None else None
                )
            except (OverflowError, ValueError):
                deadline = None
        else:
            raw_hour = candidate.group("hour")
            period = candidate.group("period")
            raw_minute = candidate.group("minute")
            hour = (
                _bounded_integer(
                    raw_hour,
                    minimum=1 if period else 0,
                    maximum=12 if period else 23,
                )
                if raw_hour is not None
                else 23
            )
            if raw_hour is None:
                minute = 59
                second = 59
            else:
                minute = (
                    30
                    if candidate.group("half") is not None
                    else (
                        _bounded_integer(raw_minute, minimum=0, maximum=59)
                        if raw_minute is not None
                        else 0
                    )
                )
                second = 0
            if hour is None or minute is None:
                deadline = None
            else:
                if period in {"中午", "下午", "傍晚", "晚上"} and hour < 12:
                    hour += 12
                elif period == "凌晨" and hour == 12:
                    hour = 0
                offset = {"今天": 0, "明天": 1, "后天": 2}[candidate.group("day")]
                deadline_date = (observed_at + timedelta(days=offset)).date()
                deadline = datetime.combine(
                    deadline_date,
                    datetime.min.time(),
                    observed_at.tzinfo,
                ).replace(hour=hour, minute=minute, second=second)

        if deadline is None:
            rejected_fields.append(GoalField.DELIVERY_DEADLINE.value)
            continue
        selected = candidate
        selected_deadline = deadline
        selected_replaces_rejected = rejected_deadline

    if selected is None or selected_deadline is None:
        return
    _append_value(
        operations,
        action=(
            DeltaAction.REPLACE
            if selected_replaces_rejected
            else _action_for(
                text,
                GoalField.DELIVERY_DEADLINE,
                span_start=selected.start("span"),
                span_end=selected.end("span"),
            )
        ),
        item=_constraint(
            GoalField.DELIVERY_DEADLINE,
            selected_deadline,
            quote=selected.group("span"),
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
) -> tuple[list[GoalMutation], list[str]]:
    operations: list[GoalMutation] = []
    rejected_fields: list[str] = []
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
        rejected_fields=rejected_fields,
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
        rejected_fields=rejected_fields,
    )
    _extract_delivery(
        text,
        source_turn=source_turn,
        observed_at=observed_at,
        operations=operations,
        rejected_fields=rejected_fields,
    )
    return operations, rejected_fields


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

    rule_operations, rule_rejected_fields = _rule_extract(
        normalized_text,
        source_turn=source_turn,
        page_context=context,
        observed_at=observed_at,
    )
    operations = list(rule_operations)
    model_fields: list[str] = []
    rejected_fields = list(rule_rejected_fields)
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
            additions, model_fields, model_rejected_fields = _extract_model_operations(
                raw_response,
                text=normalized_text,
                source_turn=source_turn,
                observed_at=observed_at,
                existing=operations,
            )
            operations.extend(additions)
            rejected_fields.extend(model_rejected_fields)
            model_status = "invalid" if model_rejected_fields else "success"
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
