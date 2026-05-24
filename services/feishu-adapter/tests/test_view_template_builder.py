import pytest

from app.view_template_builder import (
    load_warehouse_view_templates,
    match_warehouse_view_template,
    render_warehouse_view_plan,
)


def test_loads_initial_warehouse_view_templates() -> None:
    templates = load_warehouse_view_templates()

    template_ids = {template.template_id for template in templates}

    assert {
        "category_inventory_view",
        "low_stock_view",
        "expiring_inventory_view",
        "location_inventory_view",
        "batch_risk_view",
        "replenishment_candidate_view",
    }.issubset(template_ids)


def test_template_visible_fields_match_current_inventory_schema() -> None:
    supported_fields = {
        "Warehouse",
        "Warehouse ID",
        "Location",
        "Category",
        "Category ID",
        "Item ID",
        "Item Name",
        "Brand",
        "Spec",
        "Unit",
        "Batch No",
        "Quantity On Hand",
        "Quantity Available",
        "Quantity Reserved",
        "Reorder Threshold",
        "Production Date",
        "Expiry Date",
        "Days To Expiry",
        "Expiry Risk",
        "Risk Level",
        "Storage Status",
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
    assert result.template_id == "batch_risk_view"
    assert result.slots["risk_level"] == "high"
    assert result.slots["warehouse"] == "wh_hk_1"
    assert result.view_name == "香港仓高风险批次"


def test_matches_category_inventory_request() -> None:
    result = match_warehouse_view_template("帮我建一个深圳仓纸品库存视图")

    assert result.matched is True
    assert result.template_id == "category_inventory_view"
    assert result.slots["warehouse"] == "wh_sz_1"
    assert result.slots["category"] == "paper"
    assert result.view_name == "深圳仓纸品库存"


def test_matches_expiring_dairy_request() -> None:
    result = match_warehouse_view_template("帮我建一个香港仓乳制品临期库存视图")

    assert result.matched is True
    assert result.template_id == "expiring_inventory_view"
    assert result.slots["warehouse"] == "wh_hk_1"
    assert result.slots["category"] == "dairy"
    assert result.slots["expiry_risk"] == "expiring_soon"
    assert result.view_name == "香港仓乳制品临期库存"


def test_matches_location_inventory_request() -> None:
    result = match_warehouse_view_template("帮我建一个深圳仓A1库位库存视图")

    assert result.matched is True
    assert result.template_id == "location_inventory_view"
    assert result.slots["warehouse"] == "wh_sz_1"
    assert result.slots["location_code"] == "A1"
    assert result.view_name == "深圳仓A1库位库存"


def test_matches_english_low_stock_request() -> None:
    result = match_warehouse_view_template("Create a low stock warning view for Hong Kong warehouse")

    assert result.matched is True
    assert result.template_id == "low_stock_view"
    assert result.slots["warehouse"] == "wh_hk_1"


def test_below_threshold_does_not_match_low_risk_substring() -> None:
    result = match_warehouse_view_template(
        "Create a batch risk view for items below 10 units"
    )

    assert result.matched is True
    assert result.template_id == "batch_risk_view"
    assert result.slots["risk_level"] == "high"
    assert result.slots["available_lt"] == 10


def test_unknown_template_returns_suggestions() -> None:
    result = match_warehouse_view_template("帮我建一个财务利润视图")

    assert result.matched is False
    assert result.error == "unknown_view_template"
    assert "分类库存" in result.suggestions


def test_renders_inventory_risk_template_with_slots() -> None:
    plan = render_warehouse_view_plan(
        template_id="batch_risk_view",
        view_name="香港仓高风险批次",
        slots={"risk_level": "high", "warehouse": "wh_hk_1"},
    )

    assert plan["table_name"] == "Warehouse Inventory Snapshot"
    assert plan["view_name"] == "香港仓高风险批次"
    assert plan["visible_fields"] == [
        "Warehouse",
        "Location",
        "Category",
        "Item Name",
        "Batch No",
        "Quantity Available",
        "Expiry Date",
        "Risk Level",
        "Recommendation",
    ]
    assert {"field": "Risk Level", "operator": "is", "value": "high"} in plan[
        "filters"
    ]
    assert {"field": "Warehouse ID", "operator": "is", "value": "wh_hk_1"} in plan[
        "filters"
    ]
    assert plan["sorts"] == [{"field": "Risk Level", "order": "desc"}]


def test_renders_category_location_and_expiry_filters() -> None:
    plan = render_warehouse_view_plan(
        template_id="expiring_inventory_view",
        view_name="香港仓乳制品临期库存",
        slots={
            "warehouse": "wh_hk_1",
            "category": "dairy",
            "location_code": "B1",
            "expiry_risk": "expiring_soon",
        },
    )

    assert {"field": "Warehouse ID", "operator": "is", "value": "wh_hk_1"} in plan["filters"]
    assert {"field": "Category ID", "operator": "is", "value": "dairy"} in plan["filters"]
    assert {"field": "Location", "operator": "is", "value": "B1"} in plan["filters"]
    assert {"field": "Expiry Risk", "operator": "is", "value": "expiring_soon"} in plan["filters"]


def test_renders_available_threshold_slot() -> None:
    plan = render_warehouse_view_plan(
        template_id="low_stock_view",
        view_name="低于 5 件库存",
        slots={"available_lt": 5},
    )

    assert {"field": "Quantity Available", "operator": "lt", "value": 5} in plan["filters"]


def test_render_rejects_non_integer_available_threshold() -> None:
    with pytest.raises(ValueError, match="available_lt must be an integer"):
        render_warehouse_view_plan(
            template_id="low_stock_view",
            view_name="低于五件库存",
            slots={"available_lt": "five"},
        )
