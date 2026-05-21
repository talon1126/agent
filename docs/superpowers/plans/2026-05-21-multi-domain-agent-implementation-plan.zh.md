# 多业务域 Agent Workflow 实现计划

> **给 agentic workers：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务执行本计划。步骤使用 checkbox（`- [ ]`）格式便于跟踪。

**目标：** 用英文命名的企业业务 agent 替换天气专员，并把 Customer Support 与 Warehouse 接入 n8n Parent/Son 工作流。

**架构：** 从 live workflow export 开始，保护用户已经调整过的 n8n 画布间距。Parent Agent 只做分发；旧售后专员转换为 `Customer Support Agent`；新增带真实 mock-api 库存工具的 `Warehouse Agent`；新增确定性占位的 `Procurement Agent` 和 `Operations Agent`。

**技术栈：** n8n workflow JSON、FastAPI `mock-api`、Python pytest、Docker Compose。

---

### Task 1：更新 Mock Business APIs

**文件：**
- 修改：`services/mock-api/app/main.py`
- 修改：`services/mock-api/tests/test_api.py`

- [ ] **Step 1：为采购和运营 mock endpoint 写失败测试**

在 `services/mock-api/tests/test_api.py` 中加入：

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

- [ ] **Step 2：运行 mock-api 测试并确认失败**

运行：`pytest services\mock-api\tests\test_api.py -v`

预期：`/procurement/mock` 和 `/operations/summary/mock` 返回 404，测试失败。

- [ ] **Step 3：实现最小 mock endpoint**

在 `services/mock-api/app/main.py` 中加入：

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

- [ ] **Step 4：运行 mock-api 测试并确认通过**

运行：`pytest services\mock-api\tests\test_api.py -v`

预期：全部通过。

---

### Task 2：更新 Workflow 结构测试

**文件：**
- 修改：`tests/test_chat_parent_son_workflow.py`

- [ ] **Step 1：把 after-sales/weather 预期替换成 multi-domain 预期**

更新测试，使其断言：

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

同时把现有路由、memory 和工具连接测试改为使用 `Customer Support Agent`，并增加 Parent Agent prompt 必须包含 `Warehouse Agent`、`Procurement Agent` 和 `Operations Agent` 的断言。

- [ ] **Step 2：运行 workflow 测试并确认失败**

运行：`pytest tests\test_chat_parent_son_workflow.py -v`

预期：失败，因为 workflow 仍然包含 weather 和旧 after-sales 命名。

---

### Task 3：生成更新后的 n8n Workflow

**文件：**
- 新建：`scripts/update_multi_domain_workflow.py`
- 修改：`n8n/workflows/chat-parent-son-agent.json`

- [ ] **Step 1：新增确定性 workflow 转换脚本**

创建 `scripts/update_multi_domain_workflow.py`，执行：

- 读取 `n8n/workflows/chat-parent-son-agent.live-2026-05-21.json`
- 删除 `weather_agent`、`weather_forecast_tool` 和 `Sticky Note1`
- 把 `AI Agent` 重命名为 `Parent Agent`
- 把 `after_sales_agent` 重命名为 `Customer Support Agent`
- 把 `After-sales Qwen Chat Model` 重命名为 `Customer Support Qwen Chat Model`
- 把 `After-sales Postgres Chat Memory` 重命名为 `Customer Support Postgres Chat Memory`
- 把 memory session prefix 从 `after_sales:` 改为 `customer_support:`
- 新增 `Warehouse Agent`、`Procurement Agent`、`Operations Agent`
- 新增 `inventory_status_tool`、`procurement_mock_tool`、`operations_mock_tool`
- 把新增 agent 作为 `ai_tool` 连接到 `Parent Agent`
- 把每个工具作为 `ai_tool` 连接到所属 agent
- 写入 `n8n/workflows/chat-parent-son-agent.json`

- [ ] **Step 2：运行转换脚本**

运行：`python scripts\update_multi_domain_workflow.py`

预期：`n8n/workflows/chat-parent-son-agent.json` 成功更新。

- [ ] **Step 3：运行 workflow 测试并确认通过**

运行：`pytest tests\test_chat_parent_son_workflow.py -v`

预期：workflow 结构测试全部通过。

---

### Task 4：导入和 Smoke Test

**文件：**
- 如有必要修改：`AGENTS.md`
- 如有必要修改：`AGENTS.zh.md`

- [ ] **Step 1：导入更新后的 workflow**

运行：`docker compose exec -T n8n n8n import:workflow --input=/workflows/chat-parent-son-agent.json`

预期：导入成功。

- [ ] **Step 2：按 ID 发布 workflow**

运行：`docker compose exec -T n8n n8n publish:workflow --id=wechat-qwen-agent-template`

预期：发布成功。

- [ ] **Step 3：运行本地 smoke tests**

通过 n8n webhook 发送客服测试：`帮我查一下订单 ord_100`。

通过 n8n webhook 发送仓储测试：`sku_bag_1 还有多少库存`。

预期：两个请求都返回结构化回复，日志中没有 workflow 执行错误。

- [ ] **Step 4：最终验证**

运行：

```powershell
pytest services\mock-api\tests -v
pytest services\ai-service\tests -v
pytest services\feishu-adapter\tests -v
pytest tests\test_chat_parent_son_workflow.py -v
docker compose config --quiet
```

预期：测试和 compose 配置全部通过。
