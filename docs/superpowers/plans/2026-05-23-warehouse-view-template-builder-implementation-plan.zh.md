# Warehouse View Template Builder 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 让仓储员工用“帮我建一个香港仓高风险库存视图”这种自然业务语言创建飞书库存视图。

**架构：** 在 `feishu-adapter` 中增加确定性的模板目录和渲染器，暴露 `POST /warehouse/inventory-table/views/from-template`，并更新仓储 n8n fast path，让它先调用该接口，未命中再回退到 `Warehouse Agent`。Agent 最多负责选择 `template + slots`，后端负责 schema 校验和最终飞书 payload 生成。

**技术栈：** Python 3.12、FastAPI、Pydantic、pytest、由 `scripts/generate_department_workflows.py` 生成的 n8n workflow JSON、Docker Compose。

---

## 文件结构

- 新增 `services/feishu-adapter/app/view_templates/warehouse_inventory.json`：面向员工的静态视图模板目录。
- 新增 `services/feishu-adapter/app/view_template_builder.py`：模板加载、alias 匹配、slot 提取和视图计划渲染。
- 修改 `services/feishu-adapter/app/main.py`：增加请求/响应模型和 `GET /view-templates`、`POST /views/from-template`。
- 修改 `services/feishu-adapter/tests/test_feishu_adapter.py`：模板列表和 from-template 创建视图 API 测试。
- 新增 `services/feishu-adapter/tests/test_view_template_builder.py`：matcher 和 renderer 单元测试。
- 修改 `scripts/generate_department_workflows.py`：把字段级 view fast path 替换成模板 fast path。
- 修改 `n8n/workflows/warehouse-workflow.json`：重新生成的 workflow 输出。
- 修改 `tests/test_department_workflows.py`：断言新的模板 fast path 结构。
- 新增 `docs/warehouse-view-template-builder.md` 和 `docs/warehouse-view-template-builder.zh.md`：员工使用示例和运维说明。
- 修改 `AGENTS.md` 和 `AGENTS.zh.md`：实现后更新简短上下文。

---

### Task 1: 增加模板目录和单元测试

**Files:**
- Create: `services/feishu-adapter/app/view_templates/warehouse_inventory.json`
- Create: `services/feishu-adapter/app/view_template_builder.py`
- Create: `services/feishu-adapter/tests/test_view_template_builder.py`

- [ ] **Step 1: 写失败的模板目录测试**

新增 `services/feishu-adapter/tests/test_view_template_builder.py`：

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

运行：

```powershell
pytest services\feishu-adapter\tests\test_view_template_builder.py -v
```

预期：失败，提示 `ModuleNotFoundError: No module named 'app.view_template_builder'`。

- [ ] **Step 3: 增加模板目录 JSON**

创建 `services/feishu-adapter/app/view_templates/warehouse_inventory.json`：

```json
[
  {
    "template_id": "inventory_risk_view",
    "display_name": "库存风险视图",
    "aliases": ["高风险库存", "风险库存", "优先处理库存", "high risk inventory", "risk inventory"],
    "table_name": "Warehouse Inventory Snapshot",
    "visible_fields": ["SKU", "Warehouse", "Available", "Risk Level", "Recommendation"],
    "slots": {
      "risk_level": ["high", "medium", "low"],
      "warehouse": "optional",
      "available_lt": "optional"
    },
    "defaults": {
      "risk_level": "high"
    },
    "sorts": [{"field": "Available", "order": "asc"}]
  },
  {
    "template_id": "low_stock_view",
    "display_name": "缺货预警视图",
    "aliases": ["缺货预警", "低库存", "库存不足", "low stock", "stockout warning"],
    "table_name": "Warehouse Inventory Snapshot",
    "visible_fields": ["SKU", "Warehouse", "Available", "Pending Orders", "Reorder Point", "Recommendation"],
    "slots": {
      "warehouse": "optional",
      "available_lt": "optional"
    },
    "defaults": {
      "available_lt": 10
    },
    "sorts": [{"field": "Available", "order": "asc"}]
  },
  {
    "template_id": "warehouse_exception_view",
    "display_name": "仓储异常视图",
    "aliases": ["仓储异常", "库存异常", "库位异常", "warehouse exception", "inventory exception"],
    "table_name": "Warehouse Inventory Snapshot",
    "visible_fields": ["SKU", "Warehouse", "Location", "Risk Level", "Recommendation"],
    "slots": {
      "warehouse": "optional",
      "risk_level": ["high", "medium", "low"]
    },
    "defaults": {},
    "sorts": [{"field": "Risk Level", "order": "desc"}]
  },
  {
    "template_id": "replenishment_candidate_view",
    "display_name": "补货候选视图",
    "aliases": ["补货建议", "需要补货", "补货候选", "replenishment", "restock"],
    "table_name": "Warehouse Inventory Snapshot",
    "visible_fields": ["SKU", "Warehouse", "Available", "Reorder Point", "Pending Orders", "Recommendation"],
    "slots": {
      "warehouse": "optional"
    },
    "defaults": {},
    "sorts": [{"field": "Available", "order": "asc"}]
  },
  {
    "template_id": "fulfillment_block_view",
    "display_name": "履约阻塞视图",
    "aliases": ["履约阻塞", "发货风险", "不能发货", "fulfillment block", "shipping risk"],
    "table_name": "Warehouse Inventory Snapshot",
    "visible_fields": ["SKU", "Warehouse", "Available", "Pending Orders", "Risk Level", "Recommendation"],
    "slots": {
      "warehouse": "optional",
      "risk_level": ["high", "medium", "low"]
    },
    "defaults": {
      "risk_level": "high"
    },
    "sorts": [{"field": "Pending Orders", "order": "desc"}]
  }
]
```

- [ ] **Step 4: 实现最小 matcher**

创建 `services/feishu-adapter/app/view_template_builder.py`：

```python
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel


TEMPLATE_PATH = Path(__file__).resolve().parent / "view_templates" / "warehouse_inventory.json"


class WarehouseViewTemplate(BaseModel):
    template_id: str
    display_name: str
    aliases: list[str]
    table_name: str
    visible_fields: list[str]
    slots: dict[str, Any] = {}
    defaults: dict[str, Any] = {}
    sorts: list[dict[str, str]] = []


class TemplateMatchResult(BaseModel):
    matched: bool
    template_id: str | None = None
    view_name: str | None = None
    slots: dict[str, Any] = {}
    suggestions: list[str] = []
    error: str | None = None


WAREHOUSE_ALIASES = {
    "香港仓": "wh_hk_1",
    "香港": "wh_hk_1",
    "hong kong": "wh_hk_1",
    "hk": "wh_hk_1",
    "深圳仓": "wh_sz_1",
    "深圳": "wh_sz_1",
    "shenzhen": "wh_sz_1",
    "sz": "wh_sz_1",
}

RISK_ALIASES = {
    "高风险": "high",
    "高": "high",
    "high": "high",
    "中风险": "medium",
    "中": "medium",
    "medium": "medium",
    "低风险": "low",
    "低": "low",
    "low": "low",
}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def load_warehouse_view_templates() -> list[WarehouseViewTemplate]:
    data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return [WarehouseViewTemplate.model_validate(item) for item in data]


def extract_warehouse(text: str) -> str | None:
    lowered = normalize_text(text)
    for alias, warehouse_id in WAREHOUSE_ALIASES.items():
        if alias.lower() in lowered:
            return warehouse_id
    return None


def extract_risk_level(text: str) -> str | None:
    lowered = normalize_text(text)
    for alias, risk_level in RISK_ALIASES.items():
        if alias.lower() in lowered:
            return risk_level
    return None


def extract_available_lt(text: str) -> int | None:
    match = re.search(r"(?:低于|少于|小于|below|under|less than)\s*(\d+)", text, re.I)
    return int(match.group(1)) if match else None


def extract_view_name(text: str, template: WarehouseViewTemplate) -> str:
    quoted = re.search(r"[“\"']([^”\"']+)[”\"']", text)
    if quoted:
        return quoted.group(1).strip()
    warehouse_prefix = "香港仓" if extract_warehouse(text) == "wh_hk_1" else ""
    risk_level = extract_risk_level(text)
    risk_prefix = "高风险" if risk_level == "high" else "中风险" if risk_level == "medium" else "低风险" if risk_level == "low" else ""
    return f"{warehouse_prefix}{risk_prefix}{template.display_name}".strip() or template.display_name


def template_score(text: str, template: WarehouseViewTemplate) -> int:
    lowered = normalize_text(text)
    score = 0
    for alias in template.aliases:
        if alias.lower() in lowered:
            score += len(alias)
    return score


def match_warehouse_view_template(message: str) -> TemplateMatchResult:
    templates = load_warehouse_view_templates()
    ranked = sorted(
        ((template_score(message, template), template) for template in templates),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best_template = ranked[0]
    if best_score <= 0:
        return TemplateMatchResult(
            matched=False,
            error="unknown_view_template",
            suggestions=[template.display_name for template in templates[:3]],
        )

    slots = dict(best_template.defaults)
    warehouse = extract_warehouse(message)
    risk_level = extract_risk_level(message)
    available_lt = extract_available_lt(message)
    if warehouse:
        slots["warehouse"] = warehouse
    if risk_level and "risk_level" in best_template.slots:
        slots["risk_level"] = risk_level
    if available_lt is not None and "available_lt" in best_template.slots:
        slots["available_lt"] = available_lt

    return TemplateMatchResult(
        matched=True,
        template_id=best_template.template_id,
        view_name=extract_view_name(message, best_template),
        slots=slots,
        suggestions=[],
    )
```

- [ ] **Step 5: 运行单元测试**

运行：

```powershell
pytest services\feishu-adapter\tests\test_view_template_builder.py -v
```

预期：全部通过。

- [ ] **Step 6: 提交**

```powershell
git add services\feishu-adapter\app\view_templates\warehouse_inventory.json services\feishu-adapter\app\view_template_builder.py services\feishu-adapter\tests\test_view_template_builder.py
git commit -m "feat: add warehouse view template matcher"
```

---

### Task 2: 把 Template Slots 渲染成受控视图计划

**Files:**
- Modify: `services/feishu-adapter/app/view_template_builder.py`
- Modify: `services/feishu-adapter/tests/test_view_template_builder.py`

- [ ] **Step 1: 写失败的 renderer 测试**

追加到 `services/feishu-adapter/tests/test_view_template_builder.py`：

```python
from app.view_template_builder import render_warehouse_view_plan


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
    assert {"field": "Risk Level", "operator": "is", "value": "high"} in plan["filters"]
    assert {"field": "Warehouse", "operator": "is", "value": "wh_hk_1"} in plan["filters"]
    assert plan["sorts"] == [{"field": "Available", "order": "asc"}]


def test_renders_available_threshold_slot() -> None:
    plan = render_warehouse_view_plan(
        template_id="low_stock_view",
        view_name="低于 5 件库存",
        slots={"available_lt": 5},
    )

    assert {"field": "Available", "operator": "lt", "value": 5} in plan["filters"]
```

- [ ] **Step 2: 运行测试，确认失败**

运行：

```powershell
pytest services\feishu-adapter\tests\test_view_template_builder.py -v
```

预期：失败，提示缺少 `render_warehouse_view_plan`。

- [ ] **Step 3: 增加 renderer 实现**

追加到 `services/feishu-adapter/app/view_template_builder.py`：

```python
def get_template(template_id: str) -> WarehouseViewTemplate:
    for template in load_warehouse_view_templates():
        if template.template_id == template_id:
            return template
    raise ValueError(f"unknown template_id: {template_id}")


def render_filters(slots: dict[str, Any]) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    if slots.get("risk_level"):
        filters.append({"field": "Risk Level", "operator": "is", "value": slots["risk_level"]})
    if slots.get("warehouse"):
        filters.append({"field": "Warehouse", "operator": "is", "value": slots["warehouse"]})
    if slots.get("available_lt") is not None:
        filters.append({"field": "Available", "operator": "lt", "value": int(slots["available_lt"])})
    return filters


def render_warehouse_view_plan(
    template_id: str,
    view_name: str | None,
    slots: dict[str, Any],
) -> dict[str, Any]:
    template = get_template(template_id)
    merged_slots = {**template.defaults, **slots}
    return {
        "table_name": template.table_name,
        "view_name": view_name or template.display_name,
        "view_type": "grid",
        "visible_fields": list(template.visible_fields),
        "filters": render_filters(merged_slots),
        "sorts": list(template.sorts),
        "template_id": template.template_id,
        "slots": merged_slots,
    }
```

- [ ] **Step 4: 运行 renderer 测试**

运行：

```powershell
pytest services\feishu-adapter\tests\test_view_template_builder.py -v
```

预期：全部通过。

- [ ] **Step 5: 提交**

```powershell
git add services\feishu-adapter\app\view_template_builder.py services\feishu-adapter\tests\test_view_template_builder.py
git commit -m "feat: render warehouse view templates"
```

---

### Task 3: 增加 From-Template API

**Files:**
- Modify: `services/feishu-adapter/app/main.py`
- Modify: `services/feishu-adapter/tests/test_feishu_adapter.py`

- [ ] **Step 1: 写失败的 API 测试**

追加到 `services/feishu-adapter/tests/test_feishu_adapter.py`：

```python
def test_inventory_table_view_templates_endpoint_lists_employee_templates(client):
    response = client.get("/warehouse/inventory-table/view-templates")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert any(item["template_id"] == "inventory_risk_view" for item in body["templates"])


def test_inventory_table_view_from_template_creates_controlled_view(client, monkeypatch):
    monkeypatch.setenv("FEISHU_INVENTORY_TABLE_APP_ID", "app_id")
    monkeypatch.setenv("FEISHU_INVENTORY_TABLE_APP_SECRET", "app_secret")
    monkeypatch.setenv("FEISHU_INVENTORY_TABLE_APP_TOKEN", "app_token")
    monkeypatch.setenv("FEISHU_INVENTORY_TABLE_ID", "tbl_test")

    response = client.post(
        "/warehouse/inventory-table/views/from-template",
        json={"message": "帮我建一个香港仓高风险库存视图"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["matched"] is True
    assert body["template_id"] == "inventory_risk_view"
    assert body["slots"]["risk_level"] == "high"
    assert body["slots"]["warehouse"] == "wh_hk_1"
    assert body["validated_plan"]["view_name"] == "香港仓高风险库存"


def test_inventory_table_view_from_template_returns_suggestions_for_unknown_template(client):
    response = client.post(
        "/warehouse/inventory-table/views/from-template",
        json={"message": "帮我建一个财务利润视图"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["matched"] is False
    assert body["error"] == "unknown_view_template"
    assert "高风险库存" in body["message"]
```

- [ ] **Step 2: 运行测试，确认失败**

运行：

```powershell
pytest services\feishu-adapter\tests\test_feishu_adapter.py -k "view_template or from_template" -v
```

预期：失败，返回 `404 Not Found`。

- [ ] **Step 3: 增加 request model 和 import**

在 `services/feishu-adapter/app/main.py` 中导入：

```python
from app.view_template_builder import (
    load_warehouse_view_templates,
    match_warehouse_view_template,
    render_warehouse_view_plan,
)
```

在现有库存请求模型附近增加：

```python
class InventoryTableViewFromTemplateRequest(BaseModel):
    message: str
    view_name: str | None = None
```

- [ ] **Step 4: 增加模板列表接口**

在 `create_app` 内、现有库存表接口附近增加：

```python
@app.get("/warehouse/inventory-table/view-templates")
def list_inventory_view_templates() -> dict[str, Any]:
    templates = load_warehouse_view_templates()
    return {
        "ok": True,
        "templates": [
            {
                "template_id": template.template_id,
                "display_name": template.display_name,
                "aliases": template.aliases,
                "slots": template.slots,
            }
            for template in templates
        ],
    }
```

- [ ] **Step 5: 增加 from-template 接口**

在 `create_app` 内增加：

```python
@app.post("/warehouse/inventory-table/views/from-template")
def create_inventory_view_from_template(
    request: InventoryTableViewFromTemplateRequest,
) -> dict[str, Any]:
    match = match_warehouse_view_template(request.message)
    if not match.matched or not match.template_id:
        suggestions = "、".join(match.suggestions)
        return {
            "ok": False,
            "matched": False,
            "error": match.error or "unknown_view_template",
            "message": f"未匹配到视图模板。可尝试：{suggestions}。",
            "suggestions": match.suggestions,
        }

    plan = render_warehouse_view_plan(
        template_id=match.template_id,
        view_name=request.view_name or match.view_name,
        slots=match.slots,
    )
    create_request = InventoryTableViewCreateRequest.model_validate(plan)
    result = create_inventory_view(create_request)
    return {
        **result,
        "matched": True,
        "template_id": match.template_id,
        "slots": match.slots,
    }
```

- [ ] **Step 6: 运行 API 测试**

运行：

```powershell
pytest services\feishu-adapter\tests\test_feishu_adapter.py -k "view_template or from_template" -v
pytest services\feishu-adapter\tests -v
```

预期：选中测试通过，随后 adapter 全量测试通过。

- [ ] **Step 7: 提交**

```powershell
git add services\feishu-adapter\app\main.py services\feishu-adapter\tests\test_feishu_adapter.py
git commit -m "feat: create warehouse views from templates"
```

---

### Task 4: 更新 n8n 仓储 Fast Path

**Files:**
- Modify: `scripts/generate_department_workflows.py`
- Modify: `n8n/workflows/warehouse-workflow.json`
- Modify: `tests/test_department_workflows.py`

- [ ] **Step 1: 更新失败的 workflow 结构测试**

修改 `tests/test_department_workflows.py` 的仓储断言，要求：

```python
template_detector = node_by_name(workflow, "Detect Warehouse View Template Request")
template_create = node_by_name(workflow, "Create Warehouse View From Template")
template_reply = node_by_name(workflow, "Format Warehouse View Template Reply")

detector_code = template_detector["parameters"]["jsCode"]
assert "warehouse_view_template_candidate" in detector_code
assert "视图" in detector_code
assert "看板" in detector_code
assert template_create["parameters"]["url"].endswith(
    "/warehouse/inventory-table/views/from-template"
)
assert template_create["parameters"]["jsonBody"] == "={{ $json.warehouse_view_template_body }}"
assert "warehouse_view_template_fast_path" in template_reply["parameters"]["jsCode"]
```

同时更新连接断言：

```python
assert connections["Normalize Inbound Message"]["main"] == [
    [{"node": "Detect Warehouse View Template Request", "type": "main", "index": 0}]
]
assert connections["Detect Warehouse View Template Request"]["main"] == [
    [{"node": "Is Warehouse View Template Request", "type": "main", "index": 0}]
]
assert connections["Is Warehouse View Template Request"]["main"] == [
    [{"node": "Create Warehouse View From Template", "type": "main", "index": 0}],
    [{"node": expected["agent"], "type": "main", "index": 0}],
]
```

- [ ] **Step 2: 运行测试，确认失败**

运行：

```powershell
pytest tests\test_department_workflows.py -v
```

预期：失败，因为当前节点仍然是字段级 `Warehouse View Fast Path` 名称。

- [ ] **Step 3: 替换 fast path JavaScript 常量**

在 `scripts/generate_department_workflows.py` 中，把字段级 detector 替换为：

```javascript
return $input.all().map((item) => {
  const text = String(item.json.input_text || item.json.text || '').trim();
  const lowered = text.toLowerCase();
  const hasCreateIntent = ['创建', '新建', '新增', '生成', '做一个', 'create', 'generate'].some((keyword) => lowered.includes(keyword));
  const hasViewIntent = ['视图', '看板', '表格', 'view', 'board', 'table'].some((keyword) => lowered.includes(keyword));
  const candidate = Boolean(hasCreateIntent && hasViewIntent);

  return {
    json: {
      ...item.json,
      warehouse_view_template_candidate: candidate,
      warehouse_view_template_body: {
        message: text,
        view_name: ''
      }
    }
  };
});
```

把 HTTP 节点 URL 改为：

```text
http://feishu-adapter:8000/warehouse/inventory-table/views/from-template
```

把 reply formatter 的 tool trace 名称改为：

```text
warehouse_view_template_fast_path
```

- [ ] **Step 4: 重新生成 workflows**

运行：

```powershell
python scripts\generate_department_workflows.py
```

预期：`n8n/workflows/warehouse-workflow.json` 出现 template fast path 节点名。

- [ ] **Step 5: 运行 workflow 测试**

运行：

```powershell
pytest tests\test_department_workflows.py -v
pytest tests\test_chat_parent_son_workflow.py -v
```

预期：全部通过。

- [ ] **Step 6: 提交**

```powershell
git add scripts\generate_department_workflows.py n8n\workflows\warehouse-workflow.json tests\test_department_workflows.py
git commit -m "feat: route warehouse view requests through templates"
```

---

### Task 5: 增加员工文档和上下文更新

**Files:**
- Create: `docs/warehouse-view-template-builder.md`
- Create: `docs/warehouse-view-template-builder.zh.md`
- Modify: `README.md`
- Modify: `README.zh.md`
- Modify: `AGENTS.md`
- Modify: `AGENTS.zh.md`

- [ ] **Step 1: 创建英文员工文档**

创建 `docs/warehouse-view-template-builder.md`：

```markdown
# Warehouse View Template Builder

Warehouse employees can create Feishu inventory views with plain business language.

Examples:

- Help me create a high-risk inventory view.
- Create a Hong Kong warehouse low-stock warning view.
- Generate a warehouse exception board.
- Create a fulfillment risk view.

Employees do not need to provide field names, filters, sort rules, or API payloads. The backend maps the request to a controlled template, validates the current Feishu table schema, then creates or reuses the view.

Initial templates:

- Inventory risk view
- Low-stock warning view
- Warehouse exception view
- Replenishment candidate view
- Fulfillment-blocking inventory view

Smoke test:

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/views/from-template `
  -ContentType "application/json" `
  -Body '{"message":"帮我建一个香港仓高风险库存视图"}' | ConvertTo-Json -Depth 10
```
```

- [ ] **Step 2: 创建中文员工文档**

创建 `docs/warehouse-view-template-builder.zh.md`：

```markdown
# 仓储视图模板构建器

仓储员工可以用自然业务语言创建飞书库存视图。

示例：

- 帮我建一个高风险库存视图
- 创建一个香港仓缺货预警视图
- 生成一个仓储异常看板
- 建一个履约风险视图

员工不需要提供字段名、筛选条件、排序规则或 API payload。后端会把请求映射到受控模板，校验当前飞书表格 schema，然后创建或复用视图。

第一版模板：

- 库存风险视图
- 缺货/低库存预警视图
- 仓储异常视图
- 补货候选视图
- 履约阻塞视图

Smoke test：

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/views/from-template `
  -ContentType "application/json" `
  -Body '{"message":"帮我建一个香港仓高风险库存视图"}' | ConvertTo-Json -Depth 10
```
```

- [ ] **Step 3: 更新 README 链接**

在 README 仓储表格文档附近增加：

```markdown
- [Warehouse View Template Builder](docs/warehouse-view-template-builder.md)
```

中文 README 增加：

```markdown
- [仓储视图模板构建器](docs/warehouse-view-template-builder.zh.md)
```

- [ ] **Step 4: 更新 AGENTS 上下文**

在 `AGENTS.md` 和 `AGENTS.zh.md` 中增加简短 bullet：

```markdown
- Warehouse view template builder: employees can ask for views such as high-risk inventory or low-stock warning in plain language. `feishu-adapter` maps the message to `template + slots`, validates schema, and calls the controlled view creation endpoint.
```

中文：

```markdown
- 仓储视图模板构建器：员工可以用“高风险库存”“缺货预警”等自然语言创建视图。`feishu-adapter` 会把消息映射为 `template + slots`，校验 schema，再调用受控视图创建接口。
```

- [ ] **Step 5: 提交**

```powershell
git add docs\warehouse-view-template-builder.md docs\warehouse-view-template-builder.zh.md README.md README.zh.md AGENTS.md AGENTS.zh.md
git commit -m "docs: explain warehouse view templates"
```

---

### Task 6: 最终验证和本地 n8n 导入

**Files:**
- 正常情况下不需要改代码；如果验证发现缺陷，再做针对性修复。

- [ ] **Step 1: 运行完整验证**

运行：

```powershell
pytest services\feishu-adapter\tests -v
pytest services\mock-api\tests -v
pytest services\ai-service\tests -v
pytest tests\test_department_workflows.py -v
pytest tests\test_chat_parent_son_workflow.py -v
ruff check services\feishu-adapter services\mock-api tests scripts
docker compose config --quiet
```

预期：

- 所有 pytest 通过
- ruff 输出 `All checks passed!`
- Docker Compose config 退出码为 0

- [ ] **Step 2: 导入更新后的 warehouse workflow 到运行中的 n8n**

对当前运行的 compose project 执行：

```powershell
docker compose -p after-sales-implementation exec -T n8n n8n import:workflow --input=/workflows/warehouse-workflow.json
docker compose -p after-sales-implementation exec -T n8n n8n update:workflow --id=warehouse-workflow --active=true
docker compose -p after-sales-implementation restart n8n
```

预期：workflow 导入、发布，n8n 重启。

- [ ] **Step 3: 不消耗 Qwen 的 smoke test**

运行：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"platform":"feishu","message_type":"text","sender_id":"local-smoke","chat_id":"local-smoke","message_id":"warehouse_template_view_smoke_001","text":"帮我建一个香港仓高风险库存视图"}' http://localhost:5678/webhook/warehouse-inbound | ConvertTo-Json -Depth 10
```

预期：

- response `ok=true`
- `tool_trace[0].tool` 是 `warehouse_view_template_fast_path`
- `template_id` 是 `inventory_risk_view`
- `slots.risk_level` 是 `high`
- `slots.warehouse` 是 `wh_hk_1`
- workflow execution 中没有 Qwen/LLM 调用

- [ ] **Step 4: 推送分支**

运行：

```powershell
git status --short --branch
git push origin codex/enterprise-readiness-upgrade
```

预期：工作区干净，分支已推送。

