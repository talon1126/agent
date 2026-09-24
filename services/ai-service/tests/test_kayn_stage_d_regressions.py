"""End-to-end regressions for the failed Kayn A-D single-turn scenarios."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.routers.AImodel.evaluation import (
    AiModelEvaluationResult,
    evaluate_chat_non_streaming,
)
from app.routers.AImodel.goal_orchestrator import ShoppingGoalOrchestrator
from app.routers.AImodel.goal_repository import InMemoryShoppingGoalRepository
from app.routers.AImodel.memory import NoopAiModelMemoryStore
from app.routers.AImodel.schemas import AiModelChatRequest
from m1_replay_support import STOCK_BY_ITEM, product_client as _product_client


def _run(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    *,
    page_context: dict[str, Any] | None = None,
    reviews_by_item: dict[str, list[dict[str, Any]]] | None = None,
    use_fixture_inventory: bool = False,
    snapshot_currency: str = "CNY",
    failure_paths: set[str] | None = None,
) -> tuple[AiModelEvaluationResult, list[str]]:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    calls: list[str] = []
    memory = NoopAiModelMemoryStore()
    repository = InMemoryShoppingGoalRepository(
        conversation_owner=memory.get_conversation_owner
    )
    orchestrator = ShoppingGoalOrchestrator(repository, memory, model_extractor=None)
    request = AiModelChatRequest.model_validate(
        {
            "user_id": 701,
            "message": message,
            "request_version": "v2",
            "page_context": page_context,
        }
    )
    result = evaluate_chat_non_streaming(
        request,
        mock_api_url="http://mock-api",
        http_client=_product_client(
            calls,
            reviews_by_item=reviews_by_item,
            use_fixture_inventory=use_fixture_inventory,
            snapshot_currency=snapshot_currency,
            failure_paths=failure_paths,
        ),
        memory_store=memory,
        goal_orchestrator=orchestrator,
    )
    return result, calls


@pytest.mark.parametrize(
    ("scenario_id", "message", "page_context", "slot_key"),
    [
        (
            "shop_vague_earbuds_use",
            "想买一副无线耳机。",
            {"page_type": "none"},
            "usage_scenario",
        ),
        (
            "shop_vague_breakfast_food",
            "推荐一些适合早餐的东西。",
            {"page_type": "none"},
            "category",
        ),
        (
            "shop_vague_office_restock",
            "办公室要补点耗材。",
            {
                "page_type": "search",
                "search_query": "办公耗材",
                "candidate_refs": [
                    {"item_id": "item_office_pen"},
                    {"item_id": "item_copy_paper"},
                ],
            },
            "quantity",
        ),
        (
            "shop_vague_beverage_gift",
            "想买一箱饮料送人。",
            {
                "page_type": "search",
                "search_query": "饮料",
                "candidate_refs": [
                    {"item_id": "item_water_spring"},
                    {"item_id": "item_cola_zero"},
                ],
            },
            "recipient_preference",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("shop_") else None,
)
def test_kayn_vague_need_asks_the_blocking_question_before_tools(
    monkeypatch: pytest.MonkeyPatch,
    scenario_id: str,
    message: str,
    page_context: dict[str, Any],
    slot_key: str,
) -> None:
    result, calls = _run(monkeypatch, message, page_context=page_context)

    assert result.metadata["response_type"] == "clarification", scenario_id
    assert any(
        slot_key in option["value"]
        for option in result.metadata["payload"]["options"]
    ), scenario_id
    assert result.toolCalls == [], scenario_id
    assert calls == [], scenario_id


@pytest.mark.parametrize(
    ("scenario_id", "message", "candidate_ids", "expected_ids"),
    [
        (
            "shop_hard_earbuds_budget_60",
            "无线耳机预算不超过 60。",
            ["item_wireless_earbuds"],
            ["item_wireless_earbuds"],
        ),
        (
            "shop_hard_xiaomi_air_fryer",
            "只看小米空气炸锅。",
            ["item_xiaomi_air_fryer_6_5l"],
            ["item_xiaomi_air_fryer_6_5l"],
        ),
        (
            "shop_hard_exclude_xiaomi",
            "电子产品不要小米品牌。",
            [
                "item_wireless_earbuds",
                "item_smart_tv_43",
                "item_xiaomi_electric_kettle_2",
            ],
            ["item_wireless_earbuds", "item_smart_tv_43"],
        ),
    ],
)
def test_kayn_hard_constraints_return_only_scoped_eligible_products(
    monkeypatch: pytest.MonkeyPatch,
    scenario_id: str,
    message: str,
    candidate_ids: list[str],
    expected_ids: list[str],
) -> None:
    result, calls = _run(
        monkeypatch,
        message,
        page_context={
            "page_type": "search",
            "candidate_refs": [{"item_id": item_id} for item_id in candidate_ids],
        },
    )

    products = result.metadata["payload"].get("products", [])
    actual_ids = [item["item_id"] for item in products]
    assert result.metadata["response_type"] == "product_list", scenario_id
    assert actual_ids == expected_ids, scenario_id
    assert calls == ["/search", "/products/snapshots"], scenario_id
    assert {call["name"] for call in result.toolCalls} == {
        "product_search",
        "product_snapshot",
    }


def test_kayn_stroller_inclusive_budget_reaches_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls = _run(
        monkeypatch,
        "婴儿车预算最高 89，可以等于 89。",
        page_context={
            "page_type": "product",
            "current_item_id": "item_baby_stroller",
        },
    )

    assert result.metadata["response_type"] == "recommendation"
    assert result.metadata["payload"]["candidates"][0]["item_id"] == "item_baby_stroller"
    assert calls == ["/products/snapshots"]


@pytest.mark.parametrize(
    ("scenario_id", "message", "page_context", "response_type"),
    [
        (
            "shop_none_unknown_earbud_brand",
            "只要不存在的 NovaSound 品牌无线耳机。",
            {
                "page_type": "search",
                "search_query": "无线耳机",
                "candidate_refs": [{"item_id": "item_wireless_earbuds"}],
            },
            "fallback",
        ),
        (
            "shop_none_electronics_under_5",
            "找 5 元以内的电子产品。",
            {"page_type": "search", "search_query": "电子产品"},
            "fallback",
        ),
        (
            "shop_none_tv_under_100",
            "要 4K 电视，预算不超过 100。",
            {
                "page_type": "search",
                "candidate_refs": [{"item_id": "item_smart_tv_43"}],
            },
            "fallback",
        ),
        (
            "shop_none_delivery_one_hour",
            "这辆婴儿车必须一小时内送到。",
            {
                "page_type": "product",
                "current_item_id": "item_baby_stroller",
            },
            "fallback",
        ),
        (
            "shop_conflict_tv_budget",
            "要这台 43 英寸电视，但预算不能超过 50。",
            {
                "page_type": "product",
                "current_item_id": "item_smart_tv_43",
            },
            "clarification",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("shop_") else None,
)
def test_kayn_no_candidate_and_conflict_scenarios_never_leak_unrelated_products(
    monkeypatch: pytest.MonkeyPatch,
    scenario_id: str,
    message: str,
    page_context: dict[str, Any],
    response_type: str,
) -> None:
    result, _calls = _run(monkeypatch, message, page_context=page_context)

    assert result.metadata["response_type"] == response_type, scenario_id
    payload = result.metadata["payload"]
    assert not payload.get("products"), scenario_id
    assert not payload.get("candidates"), scenario_id
    assert "硬约束" in result.output or "超出预算" in result.output, scenario_id


@pytest.mark.parametrize(
    ("scenario_id", "message", "candidate_ids", "answer_terms"),
    [
        (
            "shop_compare_earbuds_tv",
            "比较候选中的耳机和电视，只列真实参数。",
            ["item_wireless_earbuds", "item_smart_tv_43"],
            ["59.99", "249.0"],
        ),
        (
            "shop_compare_xiaomi_kitchen",
            "对比小米空气炸锅和电水壶的价格与用途。",
            ["item_xiaomi_air_fryer_6_5l", "item_xiaomi_electric_kettle_2"],
            ["89.99", "34.99"],
        ),
        (
            "shop_compare_dairy",
            "纯牛奶和原味酸奶的规格、价格有什么差别？",
            ["item_milk_pure", "item_yogurt_plain"],
            ["250ml*24盒", "180g*12杯"],
        ),
        (
            "shop_compare_xiaomi_cleaning",
            "比较小米空气净化器和扫拖机器人，不要混淆功能。",
            ["item_xiaomi_air_purifier_4", "item_xiaomi_robot_vacuum_x20_plus"],
            ["179.99", "399.99"],
        ),
    ],
)
def test_kayn_fact_comparison_uses_scoped_products_without_review_tool(
    monkeypatch: pytest.MonkeyPatch,
    scenario_id: str,
    message: str,
    candidate_ids: list[str],
    answer_terms: list[str],
) -> None:
    result, calls = _run(
        monkeypatch,
        message,
        page_context={
            "page_type": "search",
            "candidate_refs": [{"item_id": item_id} for item_id in candidate_ids],
        },
    )

    payload = result.metadata["payload"]
    assert result.metadata["response_type"] == "comparison", scenario_id
    assert {item["item_id"] for item in payload["products"]} == set(candidate_ids)
    assert all(term in result.output for term in answer_terms), scenario_id
    assert "/items/reviews/batch" not in calls, scenario_id
    assert "product_reviews" not in {call["name"] for call in result.toolCalls}


def test_m1_comparison_states_the_lower_verified_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _calls = _run(
        monkeypatch,
        "纯牛奶和原味酸奶的规格、价格有什么差别？",
        page_context={
            "page_type": "search",
            "candidate_refs": [
                {"item_id": "item_milk_pure"},
                {"item_id": "item_yogurt_plain"},
            ],
        },
        use_fixture_inventory=True,
    )

    assert result.metadata["response_type"] == "comparison"
    assert "纯牛奶更低" in result.output
    assert "CNY" in result.output


def test_m1_product_list_names_verified_products_and_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _calls = _run(
        monkeypatch,
        "找中性笔，预算不超过 30 元。",
        page_context={
            "page_type": "search",
            "candidate_refs": [{"item_id": "item_office_pen"}],
        },
        use_fixture_inventory=True,
    )

    assert result.metadata["response_type"] == "product_list", result.metadata
    assert "中性笔" in result.output
    assert "12.9 CNY" in result.output


def test_kayn_read_only_comparison_keeps_products_with_unknown_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_ids = ["item_wireless_earbuds", "item_smart_tv_43"]
    assert all(item_id not in STOCK_BY_ITEM for item_id in candidate_ids)

    result, calls = _run(
        monkeypatch,
        "比较候选中的耳机和电视，只列真实参数。",
        page_context={
            "page_type": "search",
            "candidate_refs": [{"item_id": item_id} for item_id in candidate_ids],
        },
        use_fixture_inventory=True,
    )

    assert result.metadata["response_type"] == "comparison", result.metadata
    assert {item["item_id"] for item in result.metadata["payload"]["products"]} == set(
        candidate_ids
    )
    assert "59.99" in result.output and "249.0" in result.output
    assert "库存" not in result.output
    assert calls == ["/search", "/products/snapshots"]


def test_kayn_comparison_with_explicit_quantity_requires_known_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls = _run(
        monkeypatch,
        "比较候选中的耳机和电视，我要买 2 件。",
        page_context={
            "page_type": "search",
            "candidate_refs": [
                {"item_id": "item_wireless_earbuds"},
                {"item_id": "item_smart_tv_43"},
            ],
        },
        use_fixture_inventory=True,
    )

    assert result.metadata["response_type"] == "fallback", result.metadata
    assert result.metadata["payload"]["reason_code"] == "plan_safe_fallback_compare"
    assert not result.metadata["payload"].get("products")
    assert "数量" in result.output
    assert calls.count("/products/snapshots") >= 1


def test_kayn_natural_language_comparison_without_page_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls = _run(
        monkeypatch,
        "纯牛奶和原味酸奶的规格、价格有什么差别？",
        use_fixture_inventory=True,
    )

    assert result.metadata["response_type"] == "comparison", result.metadata
    assert {item["item_id"] for item in result.metadata["payload"]["products"]} == {
        "item_milk_pure",
        "item_yogurt_plain",
    }
    assert calls == ["/search", "/products/snapshots"]


def test_kayn_recommendation_with_unknown_stock_does_not_claim_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _calls = _run(
        monkeypatch,
        "推荐这辆婴儿车，预算最多 89。",
        page_context={
            "page_type": "product",
            "current_item_id": "item_baby_stroller",
        },
        use_fixture_inventory=True,
    )

    assert result.metadata["response_type"] == "fallback", result.metadata
    assert result.metadata["payload"]["reason_code"] == "required_fact_unavailable"
    assert not result.metadata["payload"].get("candidates")


def test_kayn_requested_review_topic_survives_an_empty_review_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls = _run(
        monkeypatch,
        "总结这辆婴儿车评论中的便携性反馈。",
        page_context={
            "page_type": "product",
            "current_item_id": "item_baby_stroller",
        },
    )

    assert result.metadata["response_type"] == "answer"
    assert "便携性" in result.output
    assert "没有可用于总结" in result.output
    assert set(calls) == {"/products/snapshots", "/items/reviews/batch"}
    assert any('"requested_topics":["便携性"]' in context for context in result.context)


def test_kayn_requested_review_topic_filters_real_review_insights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_at = datetime.now(UTC).isoformat()
    contents = [
        (5, "折叠方便，外出携带很轻便"),
        (5, "收车快捷，便携性很好"),
        (2, "折叠后仍然太重，不够便携"),
        (2, "上下楼携带费力，便携性较差"),
    ]
    reviews = [
        {
            "id": index,
            "item_id": "item_baby_stroller",
            "rating": rating,
            "title": "便携性反馈",
            "content": content,
            "created_at": observed_at,
            "updated_at": observed_at,
        }
        for index, (rating, content) in enumerate(contents, start=1)
    ]

    result, _calls = _run(
        monkeypatch,
        "总结这辆婴儿车评论中的便携性反馈。",
        page_context={
            "page_type": "product",
            "current_item_id": "item_baby_stroller",
        },
        reviews_by_item={"item_baby_stroller": reviews},
    )

    assert result.metadata["response_type"] == "answer"
    assert "4 条评论" in result.output
    assert "便携性" in result.output
    assert "明显分歧" in result.output


def test_m1_recommendation_explains_verified_product_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _calls = _run(
        monkeypatch,
        "推荐这款电水壶，预算不超过 40 元。",
        page_context={
            "page_type": "product",
            "current_item_id": "item_xiaomi_electric_kettle_2",
        },
        use_fixture_inventory=True,
    )

    assert result.metadata["response_type"] == "recommendation", result.metadata
    assert "Xiaomi Electric Kettle 2" in result.output
    assert "34.99" in result.output
    assert "CNY" in result.output
    assert "规格" in result.output
    assert result.metadata["payload"]["recommendations"][0]["evidence_ids"]


def test_m1_total_budget_filters_quantity_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _calls = _run(
        monkeypatch,
        "买 2 盒中性笔，总价不超过 20 元。",
        page_context={"page_type": "product", "current_item_id": "item_office_pen"},
        use_fixture_inventory=True,
    )

    assert result.metadata["response_type"] in {"clarification", "fallback"}
    assert not result.metadata["payload"].get("candidates")
    assert not result.metadata["payload"].get("products")


def test_m1_budget_rejects_price_in_another_currency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _calls = _run(
        monkeypatch,
        "推荐这款电水壶，预算不超过 40 元。",
        page_context={
            "page_type": "product",
            "current_item_id": "item_xiaomi_electric_kettle_2",
        },
        use_fixture_inventory=True,
        snapshot_currency="USD",
    )

    assert result.metadata["response_type"] == "fallback"
    assert result.metadata["payload"]["reason_code"] == "currency_mismatch"
    assert "币种" in result.output


def test_m1_snapshot_failure_has_recoverable_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls = _run(
        monkeypatch,
        "推荐这款电水壶，预算不超过 40 元。",
        page_context={
            "page_type": "product",
            "current_item_id": "item_xiaomi_electric_kettle_2",
        },
        failure_paths={"/products/snapshots"},
    )

    assert result.metadata["response_type"] == "fallback"
    assert not result.metadata["payload"].get("candidates")
    assert "重试" in result.output
    assert "/products/snapshots" in calls


def test_m1_retry_replays_saved_goal_without_page_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    calls: list[str] = []
    memory = NoopAiModelMemoryStore()
    repository = InMemoryShoppingGoalRepository(
        conversation_owner=memory.get_conversation_owner
    )
    orchestrator = ShoppingGoalOrchestrator(repository, memory, model_extractor=None)
    client = _product_client(
        calls,
        use_fixture_inventory=True,
        search_item_ids={"item_wireless_earbuds"},
    )
    first = evaluate_chat_non_streaming(
        AiModelChatRequest.model_validate(
            {
                "user_id": 701,
                "message": "找无线耳机，预算不超过 60 元。",
                "request_version": "v2",
                "page_context": {
                    "page_type": "product",
                    "current_item_id": "item_wireless_earbuds",
                },
            }
        ),
        mock_api_url="http://mock-api",
        http_client=client,
        memory_store=memory,
        goal_orchestrator=orchestrator,
    )
    assert first.metadata["response_type"] == "fallback"
    calls.clear()
    second = evaluate_chat_non_streaming(
        AiModelChatRequest.model_validate(
            {
                "user_id": 701,
                "conversation_id": first.metadata["conversation_id"],
                "message": "重试，列出商品。",
                "request_version": "v2",
                "page_context": {"page_type": "none"},
            }
        ),
        mock_api_url="http://mock-api",
        http_client=client,
        memory_store=memory,
        goal_orchestrator=orchestrator,
    )

    assert second.metadata["response_type"] == "fallback", second.metadata
    assert second.metadata["payload"]["reason_code"] == "required_fact_unavailable"
    assert "/search" in calls and "/products/snapshots" in calls
    assert "库存" in second.output
