# Multi-domain Agent Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the weather specialist with English-named enterprise business agents and wire Customer Support plus Warehouse into the n8n parent/son workflow.

**Architecture:** Start from the live workflow export so the user's canvas spacing is preserved. Keep Parent Agent as a dispatcher, convert the old after-sales specialist into `Customer Support Agent`, add `Warehouse Agent` with a real `mock-api` inventory tool, and add `Procurement Agent` plus `Operations Agent` as deterministic placeholders.

**Tech Stack:** n8n workflow JSON, FastAPI `mock-api`, Python pytest, Docker Compose.

---

### Task 1: Update Mock Business APIs

**Files:**
- Modify: `services/mock-api/app/main.py`
- Modify: `services/mock-api/tests/test_api.py`

- [ ] **Step 1: Write failing tests for procurement and operations mock endpoints**

Add two tests to `services/mock-api/tests/test_api.py`:

```python
def test_procurement_mock_recommends_replenishment_for_low_stock():
    response = client.post("/procurement/mock", json={"sku": "sku_bag_1"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["sku"] == "sku_bag_1"
    assert body["recommendation"] == "create_purchase_request"
    assert body["system"] == "mock-procurement"


def test_operations_summary_mock_returns_cross_domain_summary():
    response = client.post("/operations/summary/mock", json={"query": "帮我总结今天的运营异常"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["system"] == "mock-operations"
    assert body["summary"]
    assert any(item["domain"] == "warehouse" for item in body["incidents"])
```

- [ ] **Step 2: Run mock-api tests and verify failure**

Run: `pytest services\mock-api\tests\test_api.py -v`

Expected: FAIL with 404 for `/procurement/mock` and `/operations/summary/mock`.

- [ ] **Step 3: Implement minimal mock endpoints**

In `services/mock-api/app/main.py`, add:

```python
@app.post("/procurement/mock")
def procurement_mock(payload: dict) -> dict:
    sku = str(payload.get("sku") or "").strip()
    inventory = find_by_id("inventory.json", "sku", sku) if sku else None
    if not inventory:
        return {
            "ok": False,
            "system": "mock-procurement",
            "sku": sku,
            "recommendation": "request_valid_sku",
            "message": "未找到 SKU，需要提供有效 SKU。",
        }

    available = int(inventory.get("available", 0))
    pending_orders = int(inventory.get("pending_orders", 0))
    reorder_threshold = int(inventory.get("reorder_threshold", 0))
    should_replenish = available < reorder_threshold or available < pending_orders
    return {
        "ok": True,
        "system": "mock-procurement",
        "sku": sku,
        "available": available,
        "pending_orders": pending_orders,
        "reorder_threshold": reorder_threshold,
        "recommendation": "create_purchase_request" if should_replenish else "no_action",
        "message": "库存低于阈值，建议创建采购申请。" if should_replenish else "当前库存无需补货。",
    }


@app.post("/operations/summary/mock")
def operations_summary_mock(payload: dict) -> dict:
    query = str(payload.get("query") or payload.get("text") or "").strip()
    return {
        "ok": True,
        "system": "mock-operations",
        "query": query,
        "summary": "今日主要运营异常集中在低库存 SKU 和待跟进售后订单。",
        "incidents": [
            {"domain": "warehouse", "severity": "medium", "message": "sku_bag_1 可用库存低于补货阈值。"},
            {"domain": "customer_support", "severity": "low", "message": "退款咨询需要引用售后政策。"},
        ],
        "next_actions": ["检查低库存 SKU", "汇总客服退款问题", "确认采购补货计划"],
    }
```

- [ ] **Step 4: Run mock-api tests and verify pass**

Run: `pytest services\mock-api\tests\test_api.py -v`

Expected: all tests pass.

---

### Task 2: Update Workflow Structure Tests

**Files:**
- Modify: `tests/test_chat_parent_son_workflow.py`

- [ ] **Step 1: Replace after-sales/weather expectations with multi-domain expectations**

Update tests so they assert:

```python
def test_workflow_contains_english_business_agents_and_tools() -> None:
    workflow = load_workflow()

    expected_nodes = {
        "Customer Support Agent",
        "Warehouse Agent",
        "Procurement Agent",
        "Operations Agent",
        "order_status_tool",
        "policy_search_tool",
        "inventory_status_tool",
        "procurement_mock_tool",
        "operations_mock_tool",
    }
    node_names = {node["name"] for node in workflow["nodes"]}

    assert expected_nodes.issubset(node_names)
    assert "weather_agent" not in node_names
    assert "weather_forecast_tool" not in node_names
```

Also update existing routing, memory, and tool connection tests to use `Customer Support Agent` instead of `after_sales_agent`, and add assertions that the Parent Agent prompt mentions `Warehouse Agent`, `Procurement Agent`, and `Operations Agent`.

- [ ] **Step 2: Run workflow tests and verify failure**

Run: `pytest tests\test_chat_parent_son_workflow.py -v`

Expected: FAIL because the workflow still contains weather and old after-sales names.

---

### Task 3: Generate Updated n8n Workflow

**Files:**
- Create: `scripts/update_multi_domain_workflow.py`
- Modify: `n8n/workflows/chat-parent-son-agent.json`

- [ ] **Step 1: Add a deterministic workflow transform script**

Create `scripts/update_multi_domain_workflow.py` that:

- loads `n8n/workflows/chat-parent-son-agent.live-2026-05-21.json`
- removes nodes named `weather_agent`, `weather_forecast_tool`, and `Sticky Note1`
- renames `AI Agent` to `Parent Agent`
- renames `after_sales_agent` to `Customer Support Agent`
- renames `After-sales Qwen Chat Model` to `Customer Support Qwen Chat Model`
- renames `After-sales Postgres Chat Memory` to `Customer Support Postgres Chat Memory`
- updates memory session prefix from `after_sales:` to `customer_support:`
- adds `Warehouse Agent`, `Procurement Agent`, `Operations Agent`
- adds `inventory_status_tool`, `procurement_mock_tool`, `operations_mock_tool`
- connects new agents to `Parent Agent` as `ai_tool`
- connects each tool to its owning agent as `ai_tool`
- writes `n8n/workflows/chat-parent-son-agent.json`

- [ ] **Step 2: Run transform script**

Run: `python scripts\update_multi_domain_workflow.py`

Expected: `n8n/workflows/chat-parent-son-agent.json` updates successfully.

- [ ] **Step 3: Run workflow tests and verify pass**

Run: `pytest tests\test_chat_parent_son_workflow.py -v`

Expected: all workflow structure tests pass.

---

### Task 4: Import and Smoke Test

**Files:**
- Modify only if needed: `AGENTS.md`
- Modify only if needed: `AGENTS.zh.md`

- [ ] **Step 1: Import updated workflow**

Run: `docker compose exec -T n8n n8n import:workflow --input=/workflows/chat-parent-son-agent.json`

Expected: import succeeds.

- [ ] **Step 2: Publish workflow by ID**

Run: `docker compose exec -T n8n n8n publish:workflow --id=wechat-qwen-agent-template`

Expected: publish succeeds.

- [ ] **Step 3: Run local smoke tests**

Run customer support smoke through the n8n webhook with `帮我查一下订单 ord_100`.

Run warehouse smoke through the n8n webhook with `sku_bag_1 还有多少库存`.

Expected: both requests return a structured reply and no workflow execution error appears in logs.

- [ ] **Step 4: Run final verification**

Run:

```powershell
pytest services\mock-api\tests -v
pytest services\ai-service\tests -v
pytest services\feishu-adapter\tests -v
pytest tests\test_chat_parent_son_workflow.py -v
docker compose config --quiet
```

Expected: tests and compose config pass.
