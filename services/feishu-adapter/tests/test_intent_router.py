from app.intent_router import route_warehouse_intent


def test_routes_clear_inventory_view_request() -> None:
    route = route_warehouse_intent("帮我建一个香港仓高风险库存视图")

    assert route.status == "matched"
    assert route.intent == "create_inventory_view"
    assert route.executor == "warehouse_view_template"
    assert route.confidence >= 0.65
    assert route.slots["warehouse"] == "wh_hk_1"
    assert route.slots["risk_level"] == "high"
    assert route.clarification_question is None


def test_asks_for_clarification_when_scores_are_close() -> None:
    route = route_warehouse_intent("帮我更新一下香港仓库存表格视图")

    assert route.status == "clarification_required"
    assert route.executor == "clarification"
    assert [candidate["intent"] for candidate in route.candidates[:2]] == [
        "sync_inventory_table",
        "create_inventory_view",
    ]
    assert route.slots["warehouse"] == "wh_hk_1"
    assert "同步" in route.clarification_question
    assert "创建" in route.clarification_question


def test_falls_back_to_agent_for_unclear_warehouse_request() -> None:
    route = route_warehouse_intent("帮我分析一下最近仓库情况")

    assert route.status == "fallback"
    assert route.intent == "unknown"
    assert route.executor == "warehouse_agent"
    assert route.confidence < 0.65
    assert route.clarification_question is None


def test_routes_inventory_query_request() -> None:
    route = route_warehouse_intent("查一下 sku_bag_1 在香港仓的库存")

    assert route.status == "matched"
    assert route.intent == "query_inventory"
    assert route.executor == "warehouse_inventory_tool"
    assert route.slots["warehouse"] == "wh_hk_1"
    assert route.slots["sku"] == "sku_bag_1"
