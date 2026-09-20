"""Frozen acceptance contract for B2 turn-scoped shopping-goal extraction."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.goal_extractor import (  # noqa: E402
    DeltaAction,
    GoalExtractionTrace,
    GoalRemoveMutation,
    GoalValueMutation,
    ModelExtractionRequest,
    evaluate_goal_extraction_fixture,
    extract_goal_delta,
)
from app.routers.AImodel.schemas import AiModelPageContext  # noqa: E402
from app.routers.AImodel.shopping_goal import (  # noqa: E402
    Constraint,
    Exclusion,
    GoalField,
    GoalSourceType,
    Preference,
)


REFERENCE_TIME = datetime.fromisoformat("2026-09-20T10:00:00+08:00")
FIXTURE_PATH = ROOT / "fixtures" / "evals" / "goal_extraction_cases.json"
A2_FIXTURE_PATH = ROOT / "fixtures" / "evals" / "shopping_agent_scenarios.json"


def _extract(
    text: str,
    *,
    source_turn: int = 1,
    page_context: AiModelPageContext | None = None,
    model_extractor: Any = None,
):
    return extract_goal_delta(
        text,
        source_turn=source_turn,
        page_context=page_context,
        model_extractor=model_extractor,
        reference_time=REFERENCE_TIME,
        model_timeout_seconds=0.05,
    )


def _value_mutations(result: Any) -> list[GoalValueMutation]:
    return [
        operation
        for operation in result.operations
        if isinstance(operation, GoalValueMutation)
    ]


def _item_for(result: Any, field: GoalField) -> Any:
    return next(
        operation.item
        for operation in _value_mutations(result)
        if operation.item.field is field
    )


def test_budget_range_is_typed_and_traces_exact_source_span() -> None:
    result = _extract("预算 3000 到 5000 元")
    minimum = _item_for(result, GoalField.BUDGET_MIN)
    maximum = _item_for(result, GoalField.BUDGET_MAX)

    assert isinstance(minimum, Constraint)
    assert (minimum.value, maximum.value) == (Decimal("3000"), Decimal("5000"))
    assert minimum.evidence.source_type is GoalSourceType.USER_TURN
    assert minimum.evidence.source_turn == 1
    assert minimum.evidence.quote == "3000 到 5000"
    assert minimum.evidence.created_at.tzinfo is not None


@pytest.mark.parametrize(
    ("text", "field", "value"),
    [
        ("预算不超过 60", GoalField.BUDGET_MAX, Decimal("60")),
        ("最低预算 300", GoalField.BUDGET_MIN, Decimal("300")),
        ("总价 500 以内", GoalField.BUDGET_MAX, Decimal("500")),
    ],
)
def test_budget_boundaries_are_deterministic(
    text: str, field: GoalField, value: Decimal
) -> None:
    assert _item_for(_extract(text), field).value == value


def test_budget_correction_emits_one_replace_without_old_value() -> None:
    result = _extract("预算不是 300，是 500")
    operations = _value_mutations(result)

    assert len(operations) == 1
    assert operations[0].action is DeltaAction.REPLACE
    assert operations[0].item.field is GoalField.BUDGET_MAX
    assert operations[0].item.value == Decimal("500")


def test_unmentioned_fields_are_not_copied_from_page_context() -> None:
    context = AiModelPageContext(page_type="search", search_query="无线耳机")
    result = _extract("预算 500 以内", page_context=context)
    fields = {item.item.field for item in _value_mutations(result)}
    assert fields == {GoalField.BUDGET_MAX}


def test_deictic_turn_can_use_controlled_search_context() -> None:
    context = AiModelPageContext(page_type="search", search_query="空气炸锅")
    result = _extract("这款容量要 6.5L", page_context=context)
    category = _item_for(result, GoalField.CATEGORY)

    assert category.value == "electronics"
    assert category.evidence.source_type is GoalSourceType.PAGE_CONTEXT
    assert category.evidence.quote == "空气炸锅"


def test_explicit_category_brand_include_and_exclude_are_separate_types() -> None:
    result = _extract("只看小米空气炸锅，但不要华为")
    values = _value_mutations(result)

    assert _item_for(result, GoalField.CATEGORY).value == "electronics"
    brands = [
        operation.item
        for operation in values
        if operation.item.field is GoalField.BRAND
    ]
    assert any(
        isinstance(item, Constraint) and item.value == "Xiaomi" for item in brands
    )
    assert any(
        isinstance(item, Exclusion) and item.value == "Huawei" for item in brands
    )


def test_plain_language_brand_exclusion_is_hard_exclusion() -> None:
    brand = _item_for(_extract("手机不要苹果品牌"), GoalField.BRAND)
    assert isinstance(brand, Exclusion)
    assert brand.value == "Apple"
    assert brand.evidence.quote == "不要苹果"


@pytest.mark.parametrize("text", ["品牌无所谓", "撤销品牌偏好"])
def test_brand_withdrawal_emits_remove_operation(text: str) -> None:
    result = _extract(text)
    operation = result.operations[0]

    assert isinstance(operation, GoalRemoveMutation)
    assert operation.action is DeltaAction.REMOVE
    assert operation.field is GoalField.BRAND
    assert set(operation.target_kinds) == {"hard", "soft", "exclude"}
    assert operation.evidence.quote in text


def test_cheap_as_possible_is_a_soft_preference() -> None:
    preference = _item_for(_extract("越便宜越好"), GoalField.FREEFORM_PREFERENCE)
    assert isinstance(preference, Preference)
    assert preference.value == "越便宜越好"


@pytest.mark.parametrize(
    ("text", "field", "expected"),
    [
        ("买两盒中性笔", GoalField.QUANTITY, 2),
        ("空气炸锅容量必须至少 6.5L", GoalField.SPECIFICATION, "至少 6.5L"),
        ("客厅大约 40 平方米", GoalField.SPECIFICATION, "至少 40 平方米"),
        ("宿舍里打游戏用", GoalField.USAGE_SCENARIO, "宿舍打游戏"),
    ],
)
def test_quantity_specs_and_scenario_rules(
    text: str, field: GoalField, expected: object
) -> None:
    assert _item_for(_extract(text), field).value == expected


def test_relative_delivery_deadline_uses_injected_clock_and_timezone() -> None:
    item = _item_for(_extract("不能晚于明天 18 点"), GoalField.DELIVERY_DEADLINE)
    assert item.value.isoformat() == "2026-09-21T18:00:00+08:00"
    assert item.evidence.quote == "明天 18 点"


def test_explicit_confirmation_uses_confirm_action() -> None:
    result = _extract("是，预算上限仍然是 50")
    operation = _value_mutations(result)[0]
    assert operation.action is DeltaAction.CONFIRM
    assert operation.item.value == Decimal("50")


class _FakeModel:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[ModelExtractionRequest] = []

    def __call__(self, request: ModelExtractionRequest) -> object:
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


def test_model_can_add_only_schema_validated_soft_preferences() -> None:
    model = _FakeModel(
        {
            "suggestions": [
                {
                    "kind": "soft",
                    "field": "freeform_preference",
                    "value": "适合长时间佩戴",
                    "source_span": "长时间戴着舒服",
                    "confidence": 0.72,
                }
            ]
        }
    )
    result = _extract("希望长时间戴着舒服", source_turn=4, model_extractor=model)
    item = _item_for(result, GoalField.FREEFORM_PREFERENCE)

    assert isinstance(item, Preference)
    assert item.evidence.source_type is GoalSourceType.MODEL_INFERENCE
    assert item.evidence.source_turn == 4
    assert item.evidence.quote is None
    assert result.trace.model_fields == ("freeform_preference",)
    assert model.requests[0].text == "希望长时间戴着舒服"


def test_invalid_or_ungrounded_model_output_is_rejected_without_losing_rules() -> None:
    model = _FakeModel(
        {
            "suggestions": [
                {
                    "kind": "hard",
                    "field": "brand",
                    "value": "Huawei",
                    "source_span": "未在文本出现",
                    "confidence": 0.99,
                }
            ]
        }
    )
    result = _extract("预算 500 以内", model_extractor=model)

    assert _item_for(result, GoalField.BUDGET_MAX).value == Decimal("500")
    assert result.trace.model_status == "invalid"
    assert result.trace.rejected_fields == ("brand",)


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (RuntimeError("secret internal response"), "error", "model_error"),
        (TimeoutError("provider timeout with token"), "timeout", "model_timeout"),
    ],
)
def test_model_failure_returns_rules_and_sanitized_trace(
    error: Exception, status: str, code: str
) -> None:
    result = _extract("预算 500 以内", model_extractor=_FakeModel(error=error))

    assert _item_for(result, GoalField.BUDGET_MAX).value == Decimal("500")
    assert result.trace.model_status == status
    assert result.trace.model_error_code == code
    assert "secret" not in result.model_dump_json()
    assert "token" not in result.model_dump_json()


def test_trace_is_bounded_machine_readable_summary() -> None:
    result = _extract("小米手机预算 3000 以内")
    assert isinstance(result.trace, GoalExtractionTrace)
    assert result.trace.duration_ms >= 0
    assert set(result.trace.rule_fields) == {"category", "brand", "budget_max"}
    assert result.trace.model_status == "not_requested"
    assert result.trace.model_fields == ()
    assert result.trace.rejected_fields == ()


def test_evaluation_fixture_is_linked_to_real_a2_scenarios() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    a2_fixture = json.loads(A2_FIXTURE_PATH.read_text(encoding="utf-8"))
    scenario_ids = {item["scenario_id"] for item in a2_fixture["scenarios"]}

    assert fixture["schema_version"] == 1
    assert fixture["metadata"]["source_fixture"].endswith(
        "shopping_agent_scenarios.json"
    )
    assert len(fixture["cases"]) >= 12
    assert all(case["source_scenario_id"] in scenario_ids for case in fixture["cases"])
    assert {
        expected["field"] for case in fixture["cases"] for expected in case["expected"]
    } >= {
        "category",
        "budget_max",
        "brand",
        "specification",
        "delivery_deadline",
        "quantity",
    }


def test_evaluation_report_has_comparable_field_level_metrics() -> None:
    report = evaluate_goal_extraction_fixture(FIXTURE_PATH)
    payload = report.model_dump(mode="json")

    assert payload["schema_version"] == "v1"
    assert payload["fixture_version"] == "1.0.0"
    assert payload["case_count"] == 12
    assert payload["micro"]["expected"] >= 16
    assert payload["micro"]["precision"] == pytest.approx(1.0)
    assert payload["micro"]["recall"] == pytest.approx(1.0)
    assert set(payload["fields"]) >= {
        "category",
        "budget_max",
        "brand",
        "specification",
        "delivery_deadline",
        "quantity",
    }
    for metrics in payload["fields"].values():
        assert set(metrics) == {
            "expected",
            "predicted",
            "correct",
            "precision",
            "recall",
        }
