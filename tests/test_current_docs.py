from pathlib import Path


CURRENT_DOCS = [
    Path("docs/architecture.md"),
    Path("docs/architecture.zh.md"),
    Path("docs/demo-script.md"),
    Path("docs/demo-script.zh.md"),
    Path("docs/local-runbook.md"),
    Path("docs/local-runbook.zh.md"),
    Path("docs/warehouse-inventory-table-sync.md"),
    Path("docs/warehouse-inventory-table-sync.zh.md"),
    Path("docs/warehouse-view-template-builder.md"),
    Path("docs/warehouse-view-template-builder.zh.md"),
]

WAREHOUSE_AGENT_DOCS = [
    Path("docs/AGENTS/warehouse-agent/README.md"),
    Path("docs/AGENTS/warehouse-agent/business-boundary.md"),
    Path("docs/AGENTS/warehouse-agent/mock-api.md"),
    Path("docs/AGENTS/warehouse-agent/database-tables.md"),
]


def test_current_warehouse_docs_use_batch_location_inventory_model() -> None:
    for path in CURRENT_DOCS:
        text = path.read_text(encoding="utf-8")
        assert "item_vinda_tissue" in text or "batch + location" in text or "批次 + 库位" in text

    split_docs_text = "\n".join(path.read_text(encoding="utf-8") for path in WAREHOUSE_AGENT_DOCS)
    assert "item_vinda_tissue" in split_docs_text or "batch + location" in split_docs_text or "批次 + 库位" in split_docs_text

    for path in CURRENT_DOCS + WAREHOUSE_AGENT_DOCS:
        text = path.read_text(encoding="utf-8")
        assert "sku_bag_1" not in text
        assert "SKU + Warehouse" not in text
        assert '"SKU"' not in text
        assert '"Available"' not in text


def test_root_agents_doc_routes_to_split_warehouse_docs() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "docs/AGENTS/warehouse-agent/README.md" in text
    assert "docs/AGENTS/warehouse-agent/mock-api.md" in text
    assert "sku_bag_1" not in text
    assert "SKU + Warehouse" not in text


def test_current_warehouse_docs_describe_live_view_templates() -> None:
    english = Path("docs/warehouse-view-template-builder.md").read_text(encoding="utf-8")
    chinese = Path("docs/warehouse-view-template-builder.zh.md").read_text(encoding="utf-8")

    for text in (english, chinese):
        assert "category_inventory_view" in text
        assert "low_stock_view" in text
        assert "expiring_inventory_view" in text
        assert "location_inventory_view" in text
        assert "batch_risk_view" in text
        assert "replenishment_candidate_view" in text
