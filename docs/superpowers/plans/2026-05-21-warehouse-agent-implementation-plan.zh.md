# Warehouse Agent 实现计划

> **给 agentic workers：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务执行本计划。步骤使用 checkbox（`- [ ]`）格式便于跟踪。

**目标：** 把 `Warehouse Agent` 从简单 SKU 库存查询升级为可以回答库存、库位、异常和履约风险问题的仓储运营 agent。

**架构：** n8n 继续作为 Parent/Son workflow 编排层，仓储数据模拟继续放在 `mock-api`。先新增窄 mock 仓储 endpoint 和确定性 fixtures，再通过现有 workflow 生成脚本更新 `Warehouse Agent` 工具和 prompt。

**技术栈：** Python 3.12、FastAPI、pytest、n8n workflow JSON、Docker Compose。

---

## 文件地图

- `fixtures/data/inventory.json`：为每个 SKU 增加 `reserved` 和更丰富库存上下文。
- `fixtures/data/warehouse_locations.json`：新增库位级库存 fixture。
- `fixtures/data/warehouse_exceptions.json`：新增仓储异常 fixture。
- `services/mock-api/app/main.py`：新增仓储 endpoint。
- `services/mock-api/tests/test_api.py`：新增 endpoint 测试。
- `scripts/update_multi_domain_workflow.py`：替换 `inventory_status_tool`，新增仓储工具并更新 prompt。
- `n8n/workflows/chat-parent-son-agent.json`：生成后的 workflow 输出。
- `tests/test_chat_parent_son_workflow.py`：更新 workflow 结构测试。
- `AGENTS.md` 和 `AGENTS.zh.md`：实现后更新短摘要。

---

### Task 1：新增 Warehouse Fixtures 和 Mock API

**文件：**
- 修改：`fixtures/data/inventory.json`
- 新建：`fixtures/data/warehouse_locations.json`
- 新建：`fixtures/data/warehouse_exceptions.json`
- 修改：`services/mock-api/app/main.py`
- 修改：`services/mock-api/tests/test_api.py`

- [ ] **Step 1：为仓储库存、异常和履约写失败测试**

在 `services/mock-api/tests/test_api.py` 中加入：

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

- [ ] **Step 2：运行测试并确认失败**

运行：

```powershell
pytest services\mock-api\tests\test_api.py -v
```

预期：四个新测试因为 404 或 fixture 字段缺失失败。

- [ ] **Step 3：扩展 inventory fixture**

把 `fixtures/data/inventory.json` 更新为：

```json
[
  {"sku":"sku_bottle_1","available":42,"reserved":4,"pending_orders":8,"reorder_threshold":20},
  {"sku":"sku_bag_1","available":5,"reserved":3,"pending_orders":9,"reorder_threshold":15},
  {"sku":"sku_lamp_1","available":2,"reserved":1,"pending_orders":6,"reorder_threshold":10}
]
```

- [ ] **Step 4：新增 warehouse location fixture**

创建 `fixtures/data/warehouse_locations.json`：

```json
[
  {"sku":"sku_bottle_1","warehouse_id":"wh_hk_1","warehouse_name":"Hong Kong Main Warehouse","zone":"A","bin":"A-01-01","quantity":42,"status":"available"},
  {"sku":"sku_bag_1","warehouse_id":"wh_hk_1","warehouse_name":"Hong Kong Main Warehouse","zone":"A","bin":"A-01-03","quantity":5,"status":"available"},
  {"sku":"sku_bag_1","warehouse_id":"wh_hk_1","warehouse_name":"Hong Kong Main Warehouse","zone":"Q","bin":"Q-02-01","quantity":3,"status":"reserved"},
  {"sku":"sku_lamp_1","warehouse_id":"wh_hk_1","warehouse_name":"Hong Kong Main Warehouse","zone":"R","bin":"R-01-07","quantity":2,"status":"pending_putaway"}
]
```

- [ ] **Step 5：新增 warehouse exception fixture**

创建 `fixtures/data/warehouse_exceptions.json`：

```json
[
  {"exception_id":"wh_exc_100","sku":"sku_bag_1","type":"stock_mismatch","severity":"high","status":"open","warehouse_id":"wh_hk_1","zone":"A","bin":"A-01-03","message":"System stock is lower than pending demand.","recommended_action":"cycle_count_required"},
  {"exception_id":"wh_exc_101","sku":"sku_lamp_1","type":"pending_putaway","severity":"medium","status":"open","warehouse_id":"wh_hk_1","zone":"R","bin":"R-01-07","message":"Received items are not yet available for picking.","recommended_action":"finish_putaway"},
  {"exception_id":"wh_exc_102","sku":"sku_bottle_1","type":"picking_delay","severity":"low","status":"closed","warehouse_id":"wh_hk_1","zone":"A","bin":"A-01-01","message":"Previous picking delay resolved.","recommended_action":"no_action"}
]
```

- [ ] **Step 6：实现 helper 和 endpoint**

在 `services/mock-api/app/main.py` 中加入：

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

- [ ] **Step 7：运行测试并确认通过**

运行：

```powershell
pytest services\mock-api\tests\test_api.py -v
```

预期：mock-api 测试全部通过。

---

### Task 2：更新 Workflow 测试

**文件：**
- 修改：`tests/test_chat_parent_son_workflow.py`

- [ ] **Step 1：更新 workflow 测试预期**

更新 `test_new_business_agents_have_tools_and_parent_connections`，使其预期：

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

同时断言每个 warehouse tool 都连接到 `Warehouse Agent`。

- [ ] **Step 2：运行 workflow 测试并确认失败**

运行：

```powershell
pytest tests\test_chat_parent_son_workflow.py -v
```

预期：失败，因为 workflow 仍然使用 `inventory_status_tool`。

---

### Task 3：更新 Workflow 生成脚本和 n8n JSON

**文件：**
- 修改：`scripts/update_multi_domain_workflow.py`
- 修改：`n8n/workflows/chat-parent-son-agent.json`

- [ ] **Step 1：替换 warehouse prompt**

在 `scripts/update_multi_domain_workflow.py` 中更新 `WAREHOUSE_PROMPT`，包含：

```text
可用工具：
- warehouse_inventory_tool：查询 SKU 库存、已预留库存、待处理订单、补货阈值和库位。
- warehouse_exception_tool：查询库存差异、破损、待上架、找不到库位、拣货延迟等仓储异常。
- warehouse_fulfillment_tool：判断 SKU 或订单是否可以发货，并返回阻塞原因和下一步动作。
```

规则：

- SKU 库存/库位问题必须调用 `warehouse_inventory_tool`。
- 异常、差异、破损、待上架问题必须调用 `warehouse_exception_tool`。
- 发货和履约问题必须调用 `warehouse_fulfillment_tool`。
- 不创建采购单；采购归属问题转 `Procurement Agent`。

- [ ] **Step 2：替换旧库存工具**

把生成的工具名从 `inventory_status_tool` 改为 `warehouse_inventory_tool`，URL 改为：

`http://mock-api:8000/warehouse/inventory/`

返回 JSON 应包含 `locations`、`open_exceptions`、`risk_level`、`recommendation`。

- [ ] **Step 3：新增异常和履约工具代码常量**

新增 `WAREHOUSE_EXCEPTION_TOOL_CODE`：

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

新增 `WAREHOUSE_FULFILLMENT_TOOL_CODE`，使用同样解析 helper，URL 为：

`http://mock-api:8000/warehouse/fulfillment/check`

- [ ] **Step 4：生成 workflow**

运行：

```powershell
python scripts\update_multi_domain_workflow.py
```

预期：`n8n/workflows/chat-parent-son-agent.json` 被更新。

- [ ] **Step 5：运行 workflow 测试并确认通过**

运行：

```powershell
pytest tests\test_chat_parent_son_workflow.py -v
```

预期：workflow 测试全部通过。

---

### Task 4：更新文档并导入 n8n

**文件：**
- 修改：`AGENTS.md`
- 修改：`AGENTS.zh.md`

- [ ] **Step 1：更新短上下文文档**

在 `AGENTS.md` 和 `AGENTS.zh.md` 中记录：

- Warehouse Agent 现在负责库存、库位、异常和履约风险。
- Warehouse tools 是 `warehouse_inventory_tool`、`warehouse_exception_tool`、`warehouse_fulfillment_tool`。
- Warehouse mock endpoint 位于 `/warehouse/*`。

- [ ] **Step 2：导入前验证**

运行：

```powershell
pytest services\mock-api\tests -v
pytest tests\test_chat_parent_son_workflow.py -v
python -m ruff check services\mock-api\app services\mock-api\tests scripts\update_multi_domain_workflow.py tests\test_chat_parent_son_workflow.py
```

预期：全部通过。

- [ ] **Step 3：重建 mock-api**

运行：

```powershell
docker compose up -d --build mock-api
```

预期：mock-api 重启成功。

- [ ] **Step 4：导入并发布 workflow**

运行：

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/chat-parent-son-agent.json
docker compose exec -T n8n n8n publish:workflow --id=wechat-qwen-agent-template
docker compose restart n8n feishu-adapter
```

预期：workflow 导入、发布并重启 n8n。

- [ ] **Step 5：运行不消耗 LLM 的后端 smoke tests**

运行：

```powershell
Invoke-RestMethod http://localhost:8002/warehouse/inventory/sku_bag_1
$body = @{ sku='sku_bag_1'; status='open' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8002/warehouse/exceptions/search -ContentType 'application/json' -Body $body
$body = @{ sku='sku_bag_1' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8002/warehouse/fulfillment/check -ContentType 'application/json' -Body $body
```

预期：全部返回 JSON。除非你确认可以消耗额度，否则不跑 LLM warehouse chat smoke。

---

### Task 5：最终验证和提交

**文件：**
- 除非验证发现真实问题，否则不新增文件。

- [ ] **Step 1：运行完整测试矩阵**

运行：

```powershell
pytest services\mock-api\tests -v
pytest services\ai-service\tests -v
pytest services\feishu-adapter\tests -v
pytest tests\test_chat_parent_son_workflow.py -v
docker compose config --quiet
```

预期：全部通过。

- [ ] **Step 2：检查 git 状态**

运行：

```powershell
git status --short
```

预期：只有预期文件变化。保留现有无关用户改动。

- [ ] **Step 3：提交并推送实现**

运行：

```powershell
git add fixtures/data/inventory.json fixtures/data/warehouse_locations.json fixtures/data/warehouse_exceptions.json services/mock-api/app/main.py services/mock-api/tests/test_api.py scripts/update_multi_domain_workflow.py n8n/workflows/chat-parent-son-agent.json tests/test_chat_parent_son_workflow.py AGENTS.md AGENTS.zh.md
git commit -m "feat: expand warehouse agent operations"
git push origin master
```

预期：在用户确认执行后，提交并推送成功。
