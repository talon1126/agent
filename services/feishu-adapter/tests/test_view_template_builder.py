from app.view_template_builder import (
    load_warehouse_view_templates,
    match_warehouse_view_template,
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
