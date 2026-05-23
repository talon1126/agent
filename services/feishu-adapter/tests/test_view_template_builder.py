from app.view_template_builder import (
    load_warehouse_view_templates,
    match_warehouse_view_template,
    render_warehouse_view_plan,
)


def test_loads_initial_warehouse_view_templates() -> None:
    templates = load_warehouse_view_templates()

    template_ids = {template.template_id for template in templates}

    assert {
        "inventory_risk_view",
        "low_stock_view",
        "warehouse_exception_view",
        "replenishment_candidate_view",
        "fulfillment_block_view",
    }.issubset(template_ids)


def test_template_visible_fields_match_current_inventory_schema() -> None:
    supported_fields = {
        "SKU",
        "Product Name",
        "Warehouse",
        "Available",
        "Reserved",
        "Pending Orders",
        "Risk Level",
        "Open Exception Count",
        "Recommendation",
        "Last Synced At",
        "Sync Status",
        "Source Version",
    }

    unsupported_fields = {
        field
        for template in load_warehouse_view_templates()
        for field in template.visible_fields
        if field not in supported_fields
    }

    assert unsupported_fields == set()


def test_matches_chinese_high_risk_inventory_request() -> None:
    result = match_warehouse_view_template("帮我建一个香港仓高风险库存视图")

    assert result.matched is True
    assert result.template_id == "inventory_risk_view"
    assert result.slots["risk_level"] == "high"
    assert result.slots["warehouse"] == "wh_hk_1"
    assert result.view_name == "香港仓高风险库存"


def test_matches_english_low_stock_request() -> None:
    result = match_warehouse_view_template("Create a low stock warning view for Hong Kong warehouse")

    assert result.matched is True
    assert result.template_id == "low_stock_view"
    assert result.slots["warehouse"] == "wh_hk_1"


def test_unknown_template_returns_suggestions() -> None:
    result = match_warehouse_view_template("帮我建一个财务利润视图")

    assert result.matched is False
    assert result.error == "unknown_view_template"
    assert "高风险库存" in result.suggestions


def test_renders_inventory_risk_template_with_slots() -> None:
    plan = render_warehouse_view_plan(
        template_id="inventory_risk_view",
        view_name="香港仓高风险库存",
        slots={"risk_level": "high", "warehouse": "wh_hk_1"},
    )

    assert plan["table_name"] == "Warehouse Inventory Snapshot"
    assert plan["view_name"] == "香港仓高风险库存"
    assert plan["visible_fields"] == [
        "SKU",
        "Warehouse",
        "Available",
        "Risk Level",
        "Recommendation",
    ]
    assert {"field": "Risk Level", "operator": "is", "value": "high"} in plan[
        "filters"
    ]
    assert {"field": "Warehouse", "operator": "is", "value": "wh_hk_1"} in plan[
        "filters"
    ]
    assert plan["sorts"] == [{"field": "Available", "order": "asc"}]


def test_renders_available_threshold_slot() -> None:
    plan = render_warehouse_view_plan(
        template_id="low_stock_view",
        view_name="低于 5 件库存",
        slots={"available_lt": 5},
    )

    assert {"field": "Available", "operator": "lt", "value": 5} in plan["filters"]
