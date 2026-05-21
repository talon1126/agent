# Warehouse Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `Warehouse Agent` from simple SKU inventory lookup into a warehouse operations assistant that can answer inventory, location, exception, and fulfillment-risk questions.

**Architecture:** Keep n8n as the parent/son workflow orchestrator and keep warehouse data simulation inside `mock-api`. Add narrow mock warehouse endpoints and deterministic fixtures, then update `Warehouse Agent` tools and prompts through the existing workflow generation script.

**Tech Stack:** Python 3.12, FastAPI, pytest, n8n workflow JSON, Docker Compose.

---

## File Map

- `fixtures/data/inventory.json`: extend each SKU with `reserved` and richer stock context.
- `fixtures/data/warehouse_locations.json`: new location-level stock fixture.
- `fixtures/data/warehouse_exceptions.json`: new warehouse exception fixture.
- `services/mock-api/app/main.py`: add warehouse endpoints.
- `services/mock-api/tests/test_api.py`: add endpoint tests.
- `scripts/update_multi_domain_workflow.py`: replace `inventory_status_tool` with warehouse tools and update prompts.
- `n8n/workflows/chat-parent-son-agent.json`: generated workflow output.
- `tests/test_chat_parent_son_workflow.py`: update workflow structure tests.
- `AGENTS.md` and `AGENTS.zh.md`: update short project summary after implementation.

---

### Task 1: Add Warehouse Fixtures and Mock API Endpoints

**Files:**
- Modify: `fixtures/data/inventory.json`
- Create: `fixtures/data/warehouse_locations.json`
- Create: `fixtures/data/warehouse_exceptions.json`
- Modify: `services/mock-api/app/main.py`
- Modify: `services/mock-api/tests/test_api.py`

- [ ] **Step 1: Write failing tests for warehouse inventory, exceptions, and fulfillment**

Add to `services/mock-api/tests/test_api.py`:

```python
def test_warehouse_inventory_returns_locations_and_risk():
    response = client.get("/warehouse/inventory/sku_bag_1")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["sku"] == "sku_bag_1"
    assert body["available"] == 5
    assert body["reserved"] == 3
    assert body["risk_level"] == "high"
    assert body["locations"][0]["warehouse_id"] == "wh_hk_1"
    assert body["recommendation"]


def test_warehouse_exception_search_returns_open_sku_exceptions():
    response = client.post(
        "/warehouse/exceptions/search",
        json={"sku": "sku_bag_1", "status": "open"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["matches"]
    assert body["matches"][0]["sku"] == "sku_bag_1"
    assert body["matches"][0]["status"] == "open"


def test_warehouse_fulfillment_check_blocks_low_stock_sku():
    response = client.post(
        "/warehouse/fulfillment/check",
        json={"sku": "sku_bag_1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["sku"] == "sku_bag_1"
    assert body["can_ship"] is False
    assert "insufficient_available_stock" in body["blockers"]
    assert body["next_action"] == "notify_procurement"


def test_warehouse_fulfillment_check_allows_healthy_sku():
    response = client.post(
        "/warehouse/fulfillment/check",
        json={"sku": "sku_bottle_1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["sku"] == "sku_bottle_1"
    assert body["can_ship"] is True
    assert body["blockers"] == []
    assert body["next_action"] == "release_to_pick"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
pytest services\mock-api\tests\test_api.py -v
```

Expected: the four new tests fail with 404 or missing fixture fields.

- [ ] **Step 3: Extend inventory fixture**

Update `fixtures/data/inventory.json` to:

```json
[
  {"sku":"sku_bottle_1","available":42,"reserved":4,"pending_orders":8,"reorder_threshold":20},
  {"sku":"sku_bag_1","available":5,"reserved":3,"pending_orders":9,"reorder_threshold":15},
  {"sku":"sku_lamp_1","available":2,"reserved":1,"pending_orders":6,"reorder_threshold":10}
]
```

- [ ] **Step 4: Add warehouse location fixture**

Create `fixtures/data/warehouse_locations.json`:

```json
[
  {"sku":"sku_bottle_1","warehouse_id":"wh_hk_1","warehouse_name":"Hong Kong Main Warehouse","zone":"A","bin":"A-01-01","quantity":42,"status":"available"},
  {"sku":"sku_bag_1","warehouse_id":"wh_hk_1","warehouse_name":"Hong Kong Main Warehouse","zone":"A","bin":"A-01-03","quantity":5,"status":"available"},
  {"sku":"sku_bag_1","warehouse_id":"wh_hk_1","warehouse_name":"Hong Kong Main Warehouse","zone":"Q","bin":"Q-02-01","quantity":3,"status":"reserved"},
  {"sku":"sku_lamp_1","warehouse_id":"wh_hk_1","warehouse_name":"Hong Kong Main Warehouse","zone":"R","bin":"R-01-07","quantity":2,"status":"pending_putaway"}
]
```

- [ ] **Step 5: Add warehouse exception fixture**

Create `fixtures/data/warehouse_exceptions.json`:

```json
[
  {"exception_id":"wh_exc_100","sku":"sku_bag_1","type":"stock_mismatch","severity":"high","status":"open","warehouse_id":"wh_hk_1","zone":"A","bin":"A-01-03","message":"System stock is lower than pending demand.","recommended_action":"cycle_count_required"},
  {"exception_id":"wh_exc_101","sku":"sku_lamp_1","type":"pending_putaway","severity":"medium","status":"open","warehouse_id":"wh_hk_1","zone":"R","bin":"R-01-07","message":"Received items are not yet available for picking.","recommended_action":"finish_putaway"},
  {"exception_id":"wh_exc_102","sku":"sku_bottle_1","type":"picking_delay","severity":"low","status":"closed","warehouse_id":"wh_hk_1","zone":"A","bin":"A-01-01","message":"Previous picking delay resolved.","recommended_action":"no_action"}
]
```

- [ ] **Step 6: Implement helper functions and endpoints**

Add to `services/mock-api/app/main.py`:

```python
def load_locations_for_sku(sku: str) -> list[dict]:
    from app.store import load_json

    return [item for item in load_json("warehouse_locations.json") if item.get("sku") == sku]


def load_exceptions_for_sku(sku: str, status: str | None = None) -> list[dict]:
    from app.store import load_json

    records = [item for item in load_json("warehouse_exceptions.json") if item.get("sku") == sku]
    if status:
        records = [item for item in records if item.get("status") == status]
    return records


def warehouse_risk_level(inventory: dict, open_exceptions: list[dict]) -> str:
    available = int(inventory.get("available", 0))
    reserved = int(inventory.get("reserved", 0))
    pending_orders = int(inventory.get("pending_orders", 0))
    reorder_threshold = int(inventory.get("reorder_threshold", 0))
    if any(item.get("severity") == "high" for item in open_exceptions):
        return "high"
    if available - reserved < pending_orders or available < reorder_threshold:
        return "high"
    if open_exceptions:
        return "medium"
    return "low"


@app.get("/warehouse/inventory/{sku}")
def get_warehouse_inventory(sku: str) -> dict:
    inventory = find_by_id("inventory.json", "sku", sku)
    if not inventory:
        raise HTTPException(status_code=404, detail="inventory not found")
    locations = load_locations_for_sku(sku)
    open_exceptions = load_exceptions_for_sku(sku, "open")
    risk_level = warehouse_risk_level(inventory, open_exceptions)
    return {
        "ok": True,
        **inventory,
        "locations": locations,
        "open_exceptions": open_exceptions,
        "risk_level": risk_level,
        "recommendation": "库存或异常存在履约风险，建议仓库复核并通知采购。" if risk_level == "high" else "库存状态正常，可继续履约。",
    }


@app.post("/warehouse/exceptions/search")
def search_warehouse_exceptions(payload: dict) -> dict:
    sku = str(payload.get("sku") or "").strip()
    status = str(payload.get("status") or "").strip() or None
    if not sku:
        return {"ok": False, "error": "missing_sku", "matches": []}
    matches = load_exceptions_for_sku(sku, status)
    return {"ok": True, "sku": sku, "status": status, "matches": matches}


@app.post("/warehouse/fulfillment/check")
def check_warehouse_fulfillment(payload: dict) -> dict:
    sku = str(payload.get("sku") or "").strip()
    if not sku:
        return {"ok": False, "error": "missing_sku", "can_ship": False, "blockers": ["missing_sku"]}
    inventory = find_by_id("inventory.json", "sku", sku)
    if not inventory:
        return {"ok": False, "error": "inventory_not_found", "sku": sku, "can_ship": False, "blockers": ["inventory_not_found"]}
    locations = load_locations_for_sku(sku)
    open_exceptions = load_exceptions_for_sku(sku, "open")
    available = int(inventory.get("available", 0))
    reserved = int(inventory.get("reserved", 0))
    pending_orders = int(inventory.get("pending_orders", 0))
    blockers: list[str] = []
    if available - reserved < pending_orders:
        blockers.append("insufficient_available_stock")
    if not any(item.get("status") == "available" and int(item.get("quantity", 0)) > 0 for item in locations):
        blockers.append("missing_available_location")
    if any(item.get("severity") in {"high", "medium"} for item in open_exceptions):
        blockers.append("open_exception")
    can_ship = not blockers
    next_action = "release_to_pick" if can_ship else ("notify_procurement" if "insufficient_available_stock" in blockers else "manual_review")
    return {
        "ok": True,
        "sku": sku,
        "can_ship": can_ship,
        "blockers": blockers,
        "available": available,
        "reserved": reserved,
        "pending_orders": pending_orders,
        "locations": locations,
        "open_exceptions": open_exceptions,
        "next_action": next_action,
    }
```

- [ ] **Step 7: Run tests and verify pass**

Run:

```powershell
pytest services\mock-api\tests\test_api.py -v
```

Expected: all mock-api tests pass.

---

### Task 2: Update Workflow Tests for Warehouse Tools

**Files:**
- Modify: `tests/test_chat_parent_son_workflow.py`

- [ ] **Step 1: Update workflow test expectations**

Update `test_new_business_agents_have_tools_and_parent_connections` so it expects:

```python
warehouse = node_by_name(workflow, "Warehouse Agent")
warehouse_inventory_tool = node_by_name(workflow, "warehouse_inventory_tool")
warehouse_exception_tool = node_by_name(workflow, "warehouse_exception_tool")
warehouse_fulfillment_tool = node_by_name(workflow, "warehouse_fulfillment_tool")

system_message = warehouse["parameters"]["options"]["systemMessage"]
assert "warehouse_inventory_tool" in system_message
assert "warehouse_exception_tool" in system_message
assert "warehouse_fulfillment_tool" in system_message
assert "inventory_status_tool" not in {node["name"] for node in workflow["nodes"]}
assert "http://mock-api:8000/warehouse/inventory/" in warehouse_inventory_tool["parameters"]["jsCode"]
assert "http://mock-api:8000/warehouse/exceptions/search" in warehouse_exception_tool["parameters"]["jsCode"]
assert "http://mock-api:8000/warehouse/fulfillment/check" in warehouse_fulfillment_tool["parameters"]["jsCode"]
```

Assert each warehouse tool connects to `Warehouse Agent`.

- [ ] **Step 2: Run workflow tests and verify failure**

Run:

```powershell
pytest tests\test_chat_parent_son_workflow.py -v
```

Expected: tests fail because the workflow still uses `inventory_status_tool`.

---

### Task 3: Update Workflow Generation Script and n8n JSON

**Files:**
- Modify: `scripts/update_multi_domain_workflow.py`
- Modify: `n8n/workflows/chat-parent-son-agent.json`

- [ ] **Step 1: Replace warehouse prompt**

In `scripts/update_multi_domain_workflow.py`, update `WAREHOUSE_PROMPT` to mention:

```text
可用工具：
- warehouse_inventory_tool：查询 SKU 库存、已预留库存、待处理订单、补货阈值和库位。
- warehouse_exception_tool：查询库存差异、破损、待上架、找不到库位、拣货延迟等仓储异常。
- warehouse_fulfillment_tool：判断 SKU 或订单是否可以发货，并返回阻塞原因和下一步动作。
```

Rules:

- SKU inventory/location questions must call `warehouse_inventory_tool`.
- exception/discrepancy/damage/pending putaway questions must call `warehouse_exception_tool`.
- shipping/fulfillment questions must call `warehouse_fulfillment_tool`.
- Do not create purchase orders; route purchase ownership to `Procurement Agent`.

- [ ] **Step 2: Rename and replace old inventory tool**

Change the generated tool name from `inventory_status_tool` to `warehouse_inventory_tool`, and update its URL to:

`http://mock-api:8000/warehouse/inventory/`

The returned JSON should include `locations`, `open_exceptions`, `risk_level`, and `recommendation`.

- [ ] **Step 3: Add exception and fulfillment tool code constants**

Add `WAREHOUSE_EXCEPTION_TOOL_CODE`:

```javascript
function parseMaybeJson(value) {
  if (typeof value !== 'string') return value || {};
  try { return JSON.parse(value); } catch (error) { return { query: value }; }
}
function first(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== '');
}
function extractSku(input) {
  const text = String(first(input.sku, input.input, input.query, input.text, '')).trim();
  const match = text.match(/\bsku_[0-9A-Za-z_]+\b/i);
  return match ? match[0].toLowerCase() : '';
}
try {
  const input = parseMaybeJson(query);
  const sku = extractSku(input);
  const result = await helpers.httpRequest({
    method: 'POST',
    url: 'http://mock-api:8000/warehouse/exceptions/search',
    body: { sku, status: 'open' },
    json: true
  });
  return JSON.stringify(result);
} catch (error) {
  return JSON.stringify({ ok: false, error: 'warehouse_exception_runtime_error', message: error && error.message ? error.message : String(error) });
}
```

Add `WAREHOUSE_FULFILLMENT_TOOL_CODE` with the same parsing helpers and URL:

`http://mock-api:8000/warehouse/fulfillment/check`

- [ ] **Step 4: Generate workflow**

Run:

```powershell
python scripts\update_multi_domain_workflow.py
```

Expected: `n8n/workflows/chat-parent-son-agent.json` updates.

- [ ] **Step 5: Run workflow tests and verify pass**

Run:

```powershell
pytest tests\test_chat_parent_son_workflow.py -v
```

Expected: all workflow tests pass.

---

### Task 4: Update Docs and Import n8n

**Files:**
- Modify: `AGENTS.md`
- Modify: `AGENTS.zh.md`

- [ ] **Step 1: Update short context docs**

In both `AGENTS.md` and `AGENTS.zh.md`, document:

- Warehouse Agent now owns inventory, location, exception, and fulfillment-risk questions.
- Warehouse tools are `warehouse_inventory_tool`, `warehouse_exception_tool`, and `warehouse_fulfillment_tool`.
- Warehouse mock endpoints are under `/warehouse/*`.

- [ ] **Step 2: Run verification before import**

Run:

```powershell
pytest services\mock-api\tests -v
pytest tests\test_chat_parent_son_workflow.py -v
python -m ruff check services\mock-api\app services\mock-api\tests scripts\update_multi_domain_workflow.py tests\test_chat_parent_son_workflow.py
```

Expected: all pass.

- [ ] **Step 3: Rebuild mock-api**

Run:

```powershell
docker compose up -d --build mock-api
```

Expected: mock-api restarts successfully.

- [ ] **Step 4: Import and publish workflow**

Run:

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/chat-parent-son-agent.json
docker compose exec -T n8n n8n publish:workflow --id=wechat-qwen-agent-template
docker compose restart n8n feishu-adapter
```

Expected: workflow imports, publishes, and n8n restarts.

- [ ] **Step 5: Run non-LLM backend smoke tests**

Run:

```powershell
Invoke-RestMethod http://localhost:8002/warehouse/inventory/sku_bag_1
$body = @{ sku='sku_bag_1'; status='open' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8002/warehouse/exceptions/search -ContentType 'application/json' -Body $body
$body = @{ sku='sku_bag_1' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8002/warehouse/fulfillment/check -ContentType 'application/json' -Body $body
```

Expected: all return JSON. Do not run LLM warehouse chat smoke unless the user approves quota use.

---

### Task 5: Final Verification and Commit

**Files:**
- No new files unless verification reveals a real issue.

- [ ] **Step 1: Run full test matrix**

Run:

```powershell
pytest services\mock-api\tests -v
pytest services\ai-service\tests -v
pytest services\feishu-adapter\tests -v
pytest tests\test_chat_parent_son_workflow.py -v
docker compose config --quiet
```

Expected: all pass.

- [ ] **Step 2: Check git status**

Run:

```powershell
git status --short
```

Expected: only intended files changed. Preserve any unrelated existing user changes.

- [ ] **Step 3: Commit and push implementation branch**

Run:

```powershell
git add fixtures/data/inventory.json fixtures/data/warehouse_locations.json fixtures/data/warehouse_exceptions.json services/mock-api/app/main.py services/mock-api/tests/test_api.py scripts/update_multi_domain_workflow.py n8n/workflows/chat-parent-son-agent.json tests/test_chat_parent_son_workflow.py AGENTS.md AGENTS.zh.md
git commit -m "feat: expand warehouse agent operations"
git push origin master
```

Expected: commit and push succeed after user confirmation to execute.
