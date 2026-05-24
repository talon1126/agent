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


def test_update_inventory_table_view_routes_to_sync_without_clarification() -> None:
    route = route_warehouse_intent("帮我更新一下香港仓库存表格视图")

    assert route.status == "matched"
    assert route.intent == "sync_inventory_table"
    assert route.executor == "warehouse_inventory_table_sync"
    assert route.slots["warehouse"] == "wh_hk_1"
    assert route.clarification_question is None


def test_update_high_risk_inventory_routes_to_filtered_sync() -> None:
    route = route_warehouse_intent("帮我更新下香港高风险库存")

    assert route.status == "matched"
    assert route.intent == "sync_inventory_table"
    assert route.executor == "warehouse_inventory_table_sync"
    assert route.slots["warehouse"] == "wh_hk_1"
    assert route.slots["risk_level"] == "high"
    assert route.clarification_question is None


def test_update_category_location_and_expiry_inventory_routes_to_filtered_sync() -> None:
    route = route_warehouse_intent("帮我更新深圳仓A1库位乳制品临期库存")

    assert route.status == "matched"
    assert route.intent == "sync_inventory_table"
    assert route.executor == "warehouse_inventory_table_sync"
    assert route.slots["warehouse"] == "wh_sz_1"
    assert route.slots["location_code"] == "A1"
    assert route.slots["category"] == "dairy"
    assert route.slots["expiry_risk"] == "expiring_soon"
    assert route.clarification_question is None


def test_falls_back_to_agent_for_unclear_warehouse_request() -> None:
    route = route_warehouse_intent("帮我分析一下最近仓库情况")

    assert route.status == "fallback"
    assert route.intent == "unknown"
    assert route.executor == "warehouse_agent"
    assert route.confidence < 0.65
    assert route.clarification_question is None


def test_routes_inventory_query_request() -> None:
    route = route_warehouse_intent("查一下 item_vinda_tissue 在香港仓的库存")

    assert route.status == "matched"
    assert route.intent == "query_inventory"
    assert route.executor == "warehouse_inventory_tool"
    assert route.slots["warehouse"] == "wh_hk_1"
    assert route.slots["item_id"] == "item_vinda_tissue"
