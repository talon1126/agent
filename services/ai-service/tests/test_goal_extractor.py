"""Unit tests for deterministic and model-assisted goal extraction."""

from __future__ import annotations

import json
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.routers.AImodel.goal_extractor import (
    DeltaAction,
    GoalDelta,
    GoalExtractionTrace,
    GoalRemoveMutation,
    GoalValueMutation,
    evaluate_goal_extraction_fixture,
    extract_goal_delta,
)
from app.routers.AImodel.shopping_goal import GoalField, GoalSourceType


NOW = datetime.fromisoformat("2026-09-20T10:00:00+08:00")
ROOT = Path(__file__).resolve().parents[3]


def extract(text: str, **kwargs):
    return extract_goal_delta(
        text,
        source_turn=kwargs.pop("source_turn", 1),
        reference_time=NOW,
        **kwargs,
    )


def values(result: GoalDelta) -> list[GoalValueMutation]:
    return [item for item in result.operations if isinstance(item, GoalValueMutation)]


def item(result: GoalDelta, field: GoalField):
    return next(value.item for value in values(result) if value.item.field is field)


def test_empty_and_oversized_input_are_rejected() -> None:
    with pytest.raises(ValueError, match="blank"):
        extract("   ")
    with pytest.raises(ValueError, match="exceeds"):
        extract("a" * 8001)


@pytest.mark.parametrize("source_turn", [0, -1, True, 1.5])
def test_source_turn_must_be_a_positive_strict_integer(source_turn: object) -> None:
    with pytest.raises(ValueError, match="source_turn"):
        extract_goal_delta("预算 10", source_turn=source_turn)  # type: ignore[arg-type]


def test_reference_time_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        extract_goal_delta(
            "明天送到",
            source_turn=1,
            reference_time=datetime(2026, 9, 20),
        )


def test_model_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        extract("预算 10", model_timeout_seconds=0)


def test_no_goal_language_returns_empty_delta() -> None:
    result = extract("你好")
    assert result.operations == ()
    assert result.trace.rule_fields == ()


def test_decimal_commas_are_normalized() -> None:
    assert item(extract("预算 3,000 以内"), GoalField.BUDGET_MAX).value == Decimal(
        "3000"
    )


def test_budget_range_uses_one_shared_exact_span() -> None:
    result = extract("预算大约 3000-5000 元")
    minimum = item(result, GoalField.BUDGET_MIN)
    maximum = item(result, GoalField.BUDGET_MAX)
    assert minimum.evidence.quote == maximum.evidence.quote == "3000-5000"


def test_budget_correction_prevents_stale_old_value() -> None:
    result = extract("预算不是 300，是 500")
    assert len(values(result)) == 1
    assert values(result)[0].action is DeltaAction.REPLACE
    assert values(result)[0].item.value == Decimal("500")


def test_quantity_and_capacity_corrections_keep_only_new_value() -> None:
    quantity = values(extract("不是两台，是一台"))
    capacity = [
        value
        for value in values(extract("容量不是6.5L，是5L"))
        if value.item.field is GoalField.SPECIFICATION
    ]
    assert len(quantity) == 1
    assert quantity[0].action is DeltaAction.REPLACE
    assert quantity[0].item.value == 1
    assert len(capacity) == 1
    assert capacity[0].action is DeltaAction.REPLACE
    assert capacity[0].item.value == "5L"


def test_natural_quantity_correction_drops_negated_old_value() -> None:
    result = values(extract("我不需要两台，只买一台"))
    quantities = [value for value in result if value.item.field is GoalField.QUANTITY]
    assert len(quantities) == 1
    assert quantities[0].action is DeltaAction.REPLACE
    assert quantities[0].item.value == 1


def test_replacement_action_does_not_bleed_into_new_fields() -> None:
    result = extract("预算改成 500，再加小米偏好")
    actions = {value.item.field: value.action for value in values(result)}
    assert actions[GoalField.BUDGET_MAX] is DeltaAction.REPLACE
    assert actions[GoalField.BRAND] is DeltaAction.ADD


def test_confirmation_action_does_not_bleed_into_new_quantity() -> None:
    result = extract("是，预算上限仍然是500，再买两盒")
    actions = {value.item.field: value.action for value in values(result)}
    assert actions[GoalField.BUDGET_MAX] is DeltaAction.CONFIRM
    assert actions[GoalField.QUANTITY] is DeltaAction.ADD


def test_spec_replacement_is_bound_to_its_attribute_clause() -> None:
    result = values(extract("容量改成5L，另外加43英寸屏幕"))
    actions = {
        value.item.attribute: value.action
        for value in result
        if value.item.field is GoalField.SPECIFICATION
    }
    assert actions == {
        "capacity": DeltaAction.REPLACE,
        "screen_size": DeltaAction.ADD,
    }


def test_brand_plain_mention_is_soft_but_only_is_hard() -> None:
    soft = item(extract("想看看小米手机"), GoalField.BRAND)
    hard = item(extract("只要小米手机"), GoalField.BRAND)
    assert soft.kind == "soft"
    assert hard.kind == "hard"


def test_brand_exclusion_does_not_duplicate_soft_preference() -> None:
    result = extract("手机不要小米")
    brands = [
        value.item for value in values(result) if value.item.field is GoalField.BRAND
    ]
    assert len(brands) == 1
    assert brands[0].kind == "exclude"


def test_postfix_brand_and_category_negation_never_become_positive() -> None:
    brand = item(extract("苹果不要"), GoalField.BRAND)
    category = item(extract("不要手机"), GoalField.CATEGORY)
    assert brand.kind == "exclude"
    assert category.kind == "exclude"


def test_postfix_negation_is_scoped_to_its_clause() -> None:
    brand_result = values(extract("苹果不要，华为可以"))
    brands = {
        value.item.value: value.item.kind
        for value in brand_result
        if value.item.field is GoalField.BRAND
    }
    category_result = values(extract("手机不要，电视可以"))
    categories = [
        value.item
        for value in category_result
        if value.item.field is GoalField.CATEGORY
    ]
    assert brands == {"Apple": "exclude", "Huawei": "soft"}
    assert any(
        entry.kind == "exclude" and entry.value == "手机" for entry in categories
    )
    assert any(
        entry.kind == "hard" and entry.value == "electronics" for entry in categories
    )


def test_brand_remove_carries_only_current_turn_evidence() -> None:
    result = extract("取消品牌限制", source_turn=7)
    operation = result.operations[0]
    assert isinstance(operation, GoalRemoveMutation)
    assert operation.evidence.source_turn == 7
    assert operation.evidence.source_type is GoalSourceType.USER_TURN


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("一", 1), ("两", 2), ("十", 10), ("12", 12)],
)
def test_quantity_supports_common_chinese_and_arabic_numbers(
    raw: str, expected: int
) -> None:
    assert item(extract(f"买{raw}盒中性笔"), GoalField.QUANTITY).value == expected


def test_capacity_area_and_screen_specs_have_distinct_attributes() -> None:
    result = extract("空气炸锅容量至少 6.5L，放在 40 平方米客厅，看 43 英寸电视")
    specs = {
        value.item.attribute: value.item.value
        for value in values(result)
        if value.item.field is GoalField.SPECIFICATION
    }
    assert specs == {
        "capacity": "至少 6.5L",
        "room_area": "至少 40 平方米",
        "screen_size": "43 英寸",
    }


def test_delivery_without_hour_uses_end_of_day() -> None:
    deadline = item(extract("后天送到"), GoalField.DELIVERY_DEADLINE).value
    assert deadline.isoformat() == "2026-09-22T23:59:59+08:00"


def test_deictic_context_is_validated_before_use() -> None:
    result = extract(
        "这款容量 6.5L",
        page_context={"page_type": "search", "search_query": "空气炸锅"},
    )
    assert item(result, GoalField.CATEGORY).evidence.source_type is (
        GoalSourceType.PAGE_CONTEXT
    )
    with pytest.raises(ValidationError):
        extract(
            "这款怎么样",
            page_context={"page_type": "search", "inventory": 20},
        )


def test_dismissed_deictic_reference_does_not_inject_page_category() -> None:
    result = extract(
        "这个先不说，预算500以内",
        page_context={"page_type": "search", "search_query": "无线耳机"},
    )
    assert {value.item.field for value in values(result)} == {GoalField.BUDGET_MAX}

    result = extract(
        "这款怎么样先不说，预算500以内",
        page_context={"page_type": "search", "search_query": "空气炸锅"},
    )
    assert {value.item.field for value in values(result)} == {GoalField.BUDGET_MAX}


def test_calendar_word_without_delivery_intent_is_ignored() -> None:
    result = extract("明天再聊")
    assert result.operations == ()


def test_negated_delivery_date_is_replaced_by_valid_later_date() -> None:
    result = values(extract("不用明天送到，后天也行"))
    deadlines = [
        value for value in result if value.item.field is GoalField.DELIVERY_DEADLINE
    ]
    assert len(deadlines) == 1
    assert deadlines[0].action is DeltaAction.REPLACE
    assert deadlines[0].item.value.isoformat() == "2026-09-22T23:59:59+08:00"


class FakeModel:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    def __call__(self, request):
        if self.error:
            raise self.error
        return self.response


class SlowModel:
    def __call__(self, request):
        time.sleep(0.05)
        return {"suggestions": []}


def test_non_mapping_model_response_is_invalid() -> None:
    result = extract("预算 10", model_extractor=FakeModel("not-json"))
    assert result.trace.model_status == "invalid"
    assert result.trace.rejected_fields == ("model_output",)


def test_wall_clock_model_timeout_returns_rule_result() -> None:
    result = extract(
        "预算 10 以内",
        model_extractor=SlowModel(),
        model_timeout_seconds=0.001,
    )
    assert result.trace.model_status == "timeout"
    assert result.trace.model_error_code == "model_timeout"
    assert item(result, GoalField.BUDGET_MAX).value == Decimal("10")


def test_too_many_model_suggestions_are_rejected_as_one_invalid_output() -> None:
    suggestion = {
        "kind": "soft",
        "field": "freeform_preference",
        "value": "轻便",
        "source_span": "轻便",
        "confidence": 0.5,
    }
    result = extract(
        "轻便",
        model_extractor=FakeModel({"suggestions": [suggestion] * 33}),
    )
    assert result.trace.model_status == "invalid"
    assert result.trace.rejected_fields == ("model_output",)


def test_invalid_model_field_name_is_not_copied_into_trace() -> None:
    result = extract(
        "预算 10",
        model_extractor=FakeModel(
            {
                "suggestions": [
                    {
                        "kind": "soft",
                        "field": "secret_provider_payload",
                        "value": "private response",
                        "source_span": "预算",
                        "confidence": 0.9,
                    }
                ]
            }
        ),
    )
    assert result.trace.rejected_fields == ("model_output",)
    assert "secret_provider_payload" not in result.model_dump_json()
    assert "private response" not in result.model_dump_json()


def test_model_cannot_duplicate_rule_semantic_field() -> None:
    result = extract(
        "越便宜越好",
        model_extractor=FakeModel(
            {
                "suggestions": [
                    {
                        "kind": "soft",
                        "field": "freeform_preference",
                        "value": "低价",
                        "source_span": "越便宜越好",
                        "confidence": 0.6,
                    }
                ]
            }
        ),
    )
    assert result.trace.model_status == "invalid"
    assert result.trace.rejected_fields == ("freeform_preference",)
    assert item(result, GoalField.FREEFORM_PREFERENCE).value == "越便宜越好"


def test_model_unknown_becomes_b1_open_slot() -> None:
    result = extract(
        "买个耳机但还没想好预算",
        model_extractor=FakeModel(
            {
                "suggestions": [
                    {
                        "kind": "unknown",
                        "field": "budget_max",
                        "question": "最高预算是多少？",
                        "source_span": "还没想好预算",
                        "confidence": 0.8,
                    }
                ]
            }
        ),
    )
    assert item(result, GoalField.BUDGET_MAX).kind == "unknown"


def test_model_source_span_survives_delta_serialization_without_forged_quote() -> None:
    result = extract(
        "希望长时间戴着舒服",
        model_extractor=FakeModel(
            {
                "suggestions": [
                    {
                        "kind": "soft",
                        "field": "freeform_preference",
                        "value": "适合长时间佩戴",
                        "source_span": "长时间戴着舒服",
                        "confidence": 0.8,
                    }
                ]
            }
        ),
    )
    operation = values(result)[0]
    assert operation.source_span == "长时间戴着舒服"
    assert operation.item.evidence.quote is None
    assert "长时间戴着舒服" in result.model_dump_json()


def test_model_exception_does_not_retain_error_text() -> None:
    result = extract(
        "预算 20",
        model_extractor=FakeModel(error=RuntimeError("Bearer top-secret-token")),
    )
    assert result.trace.model_error_code == "model_error"
    assert "Bearer" not in result.model_dump_json()


def test_delta_rejects_mismatched_evidence_turn() -> None:
    result = extract("预算 20", source_turn=3)
    with pytest.raises(ValidationError, match="source_turn"):
        GoalDelta(source_turn=4, operations=result.operations, trace=result.trace)


def test_delta_json_round_trip_preserves_operation_subtypes() -> None:
    result = extract("撤销品牌偏好，预算改成 50")
    restored = GoalDelta.model_validate_json(result.model_dump_json())
    assert restored == result
    assert {type(operation) for operation in restored.operations} == {
        GoalRemoveMutation,
        GoalValueMutation,
    }


def test_trace_rejects_arbitrary_provider_error_fields() -> None:
    with pytest.raises(ValidationError):
        GoalExtractionTrace.model_validate(
            {
                "duration_ms": 1,
                "provider_response": "secret",
            }
        )


def test_evaluation_fixture_is_perfect_and_serializable() -> None:
    report = evaluate_goal_extraction_fixture(
        ROOT / "fixtures" / "evals" / "goal_extraction_cases.json"
    )
    assert report.micro.correct == report.micro.expected == report.micro.predicted
    assert report.model_validate_json(report.model_dump_json()) == report


def test_evaluation_does_not_cancel_errors_across_cases(tmp_path: Path) -> None:
    fixture = {
        "schema_version": 1,
        "metadata": {
            "name": "case-aware-regression",
            "version": "1.0.0",
            "reference_time": NOW.isoformat(),
        },
        "cases": [
            {
                "case_id": "missing_prediction",
                "source_turn": 1,
                "text": "你好",
                "page_context": {"page_type": "none"},
                "expected": [
                    {
                        "action": "add",
                        "kind": "hard",
                        "field": "budget_max",
                        "value": "50",
                    }
                ],
            },
            {
                "case_id": "wrong_extra_prediction",
                "source_turn": 1,
                "text": "预算50以内",
                "page_context": {"page_type": "none"},
                "expected": [],
            },
        ],
    }
    path = tmp_path / "case-aware.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    report = evaluate_goal_extraction_fixture(path)
    assert report.micro.correct == 0
    assert report.micro.precision == 0
    assert report.micro.recall == 0
