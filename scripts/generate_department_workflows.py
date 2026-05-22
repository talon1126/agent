import copy
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "n8n" / "workflows" / "chat-parent-son-agent.json"
TARGET_DIR = ROOT / "n8n" / "workflows"

DEPARTMENTS = {
    "customer_support": {
        "workflow_id": "customer-support-workflow",
        "workflow_name": "Customer Support Workflow",
        "filename": "customer-support-workflow.json",
        "webhook_path": "customer-support-inbound",
        "agent": "Customer Support Agent",
        "model": "Customer Support Qwen Chat Model",
        "memory": "Customer Support Postgres Chat Memory",
        "memory_prefix": "customer_support",
        "tools": ["order_status_tool", "policy_search_tool"],
    },
    "warehouse": {
        "workflow_id": "warehouse-workflow",
        "workflow_name": "Warehouse Workflow",
        "filename": "warehouse-workflow.json",
        "webhook_path": "warehouse-inbound",
        "agent": "Warehouse Agent",
        "model": "Warehouse Qwen Chat Model",
        "memory": "Warehouse Postgres Chat Memory",
        "memory_prefix": "warehouse",
        "tools": [
            "warehouse_inventory_tool",
            "warehouse_exception_tool",
            "warehouse_fulfillment_tool",
            "warehouse_inventory_table_provision_tool",
            "warehouse_inventory_table_sync_tool",
            "warehouse_table_schema_tool",
            "warehouse_view_create_tool",
        ],
    },
    "procurement": {
        "workflow_id": "procurement-workflow",
        "workflow_name": "Procurement Workflow",
        "filename": "procurement-workflow.json",
        "webhook_path": "procurement-inbound",
        "agent": "Procurement Agent",
        "model": "Procurement Qwen Chat Model",
        "memory": "Procurement Postgres Chat Memory",
        "memory_prefix": "procurement",
        "tools": ["procurement_mock_tool"],
    },
    "operations": {
        "workflow_id": "operations-workflow",
        "workflow_name": "Operations Workflow",
        "filename": "operations-workflow.json",
        "webhook_path": "operations-inbound",
        "agent": "Operations Agent",
        "model": "Operations Qwen Chat Model",
        "memory": "Operations Postgres Chat Memory",
        "memory_prefix": "operations",
        "tools": ["operations_mock_tool"],
    },
}

FORMAT_REPLY_JS = """const source = $items('Normalize Inbound Message')[0]?.json ?? {};

function normalizeToolTrace(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map((step) => ({
    tool: step.tool ?? step.name ?? step.action?.tool ?? 'unknown_tool',
    input: step.input ?? step.toolInput ?? step.action?.toolInput ?? null,
    output: step.output ?? step.result ?? step.observation ?? null,
  }));
}

return $input.all().map((item) => {
  const reply = item.json.answer ?? item.json.output ?? item.json.text ?? item.json.response ?? JSON.stringify(item.json);
  const rawToolTrace = item.json.tool_trace ?? item.json.tool_calls ?? item.json.intermediateSteps ?? item.json.intermediate_steps ?? [];
  return {
    json: {
      ok: true,
      platform: source.platform,
      chat_id: source.chat_id,
      sender_id: source.sender_id,
      message_id: source.message_id,
      reply,
      tool_trace: normalizeToolTrace(rawToolTrace),
      raw_agent_output: item.json
    }
  };
});"""

WAREHOUSE_SYNC_TOOL_JS = """function parseMaybeJson(value) {
  if (typeof value !== 'string') return value || {};
  try {
    return JSON.parse(value);
  } catch (error) {
    return { query: value };
  }
}

function first(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== '');
}

function extractSku(input) {
  const text = String(first(input.sku, input.input, input.query, input.text, '')).trim();
  const match = text.match(/\\bsku_[0-9A-Za-z_]+\\b/i);
  return match ? match[0].toLowerCase() : '';
}

try {
  const input = parseMaybeJson(query);
  const sku = extractSku(input);
  if (!sku) {
    return JSON.stringify({ ok: false, error: 'missing_sku', message: '请提供 SKU，例如 sku_bag_1。' });
  }

  const result = await helpers.httpRequest({
    method: 'POST',
    url: 'http://feishu-adapter:8000/warehouse/inventory-table/sync',
    body: { sku },
    json: true
  });

  return JSON.stringify(result);
} catch (error) {
  return JSON.stringify({
    ok: false,
    error: 'warehouse_inventory_table_sync_runtime_error',
    message: error && error.message ? error.message : String(error)
  });
}"""

WAREHOUSE_PROVISION_TOOL_JS = """function parseMaybeJson(value) {
  if (typeof value !== 'string') return value || {};
  try {
    return JSON.parse(value);
  } catch (error) {
    return { query: value };
  }
}

function first(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== '');
}

try {
  const input = parseMaybeJson(query);
  const tableName = String(first(input.table_name, input.tableName, input.name, 'Warehouse Inventory Snapshot')).trim();

  const result = await helpers.httpRequest({
    method: 'POST',
    url: 'http://feishu-adapter:8000/warehouse/inventory-table/provision',
    body: { table_name: tableName || 'Warehouse Inventory Snapshot' },
    json: true
  });

  return JSON.stringify(result);
} catch (error) {
  return JSON.stringify({
    ok: false,
    error: 'warehouse_inventory_table_provision_runtime_error',
    message: error && error.message ? error.message : String(error)
  });
}"""

WAREHOUSE_SCHEMA_TOOL_JS = """function parseMaybeJson(value) {
  if (typeof value !== 'string') return value || {};
  try {
    return JSON.parse(value);
  } catch (error) {
    return { query: value };
  }
}

function first(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== '');
}

try {
  const input = parseMaybeJson(query);
  const tableName = String(first(input.table_name, input.tableName, input.name, 'Warehouse Inventory Snapshot')).trim();
  const encodedName = encodeURIComponent(tableName || 'Warehouse Inventory Snapshot');

  const result = await helpers.httpRequest({
    method: 'GET',
    url: `http://feishu-adapter:8000/warehouse/inventory-table/schema?table_name=${encodedName}`,
    json: true
  });

  return JSON.stringify(result);
} catch (error) {
  return JSON.stringify({
    ok: false,
    error: 'warehouse_table_schema_runtime_error',
    message: error && error.message ? error.message : String(error)
  });
}"""

WAREHOUSE_VIEW_CREATE_TOOL_JS = """function parseMaybeJson(value) {
  if (typeof value !== 'string') return value || {};
  try {
    return JSON.parse(value);
  } catch (error) {
    return { query: value };
  }
}

function first(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== '');
}

function splitFields(value) {
  if (Array.isArray(value)) return value;
  if (!value) return [];
  return String(value)
    .split(/[,，]/)
    .map((field) => field.trim())
    .filter(Boolean);
}

try {
  const input = parseMaybeJson(query);
  const text = String(first(input.query, input.text, '')).trim();
  const quotedName = text.match(/[“\"]([^”\"]+)[”\"]/);
  const viewName = String(first(input.view_name, input.viewName, input.name, quotedName && quotedName[1], 'Warehouse Inventory View')).trim();
  const visibleFields = splitFields(first(input.visible_fields, input.visibleFields, input.fields));
  const filters = Array.isArray(input.filters) ? input.filters : [];
  const sorts = Array.isArray(input.sorts) ? input.sorts : [];

  const result = await helpers.httpRequest({
    method: 'POST',
    url: 'http://feishu-adapter:8000/warehouse/inventory-table/views/create',
    body: {
      table_name: String(first(input.table_name, input.tableName, 'Warehouse Inventory Snapshot')).trim(),
      view_name: viewName,
      view_type: String(first(input.view_type, input.viewType, 'grid')).trim(),
      visible_fields: visibleFields,
      filters,
      sorts
    },
    json: true
  });

  return JSON.stringify(result);
} catch (error) {
  return JSON.stringify({
    ok: false,
    error: 'warehouse_view_create_runtime_error',
    message: error && error.message ? error.message : String(error)
  });
}"""


def load_source_workflow() -> dict[str, Any]:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    return data[0] if isinstance(data, list) else data


def find_node(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    for node in workflow["nodes"]:
        if node.get("name") == name:
            return node
    raise KeyError(name)


def clone_node(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    return copy.deepcopy(find_node(workflow, name))


def make_warehouse_sync_tool() -> dict[str, Any]:
    return {
        "parameters": {
            "description": (
                "Use this Feishu table sync tool only when the user asks to sync, export, "
                "publish, or show an SKU inventory snapshot in a Feishu table."
            ),
            "jsCode": WAREHOUSE_SYNC_TOOL_JS,
        },
        "id": "warehouse-inventory-table-sync-tool-node",
        "name": "warehouse_inventory_table_sync_tool",
        "type": "@n8n/n8n-nodes-langchain.toolCode",
        "typeVersion": 1.3,
        "position": [1248, 320],
    }


def make_warehouse_provision_tool() -> dict[str, Any]:
    return {
        "parameters": {
            "description": (
                "Use this tool only when the user explicitly asks to create, initialize, "
                "or provision the Feishu inventory table for warehouse snapshots."
            ),
            "jsCode": WAREHOUSE_PROVISION_TOOL_JS,
        },
        "id": "warehouse-inventory-table-provision-tool-node",
        "name": "warehouse_inventory_table_provision_tool",
        "type": "@n8n/n8n-nodes-langchain.toolCode",
        "typeVersion": 1.3,
        "position": [1248, 320],
    }


def make_warehouse_schema_tool() -> dict[str, Any]:
    return {
        "parameters": {
            "description": (
                "Use this tool before creating a Feishu inventory table view. It returns "
                "the current table fields, field types, colored select options, and existing views."
            ),
            "jsCode": WAREHOUSE_SCHEMA_TOOL_JS,
        },
        "id": "warehouse-table-schema-tool-node",
        "name": "warehouse_table_schema_tool",
        "type": "@n8n/n8n-nodes-langchain.toolCode",
        "typeVersion": 1.3,
        "position": [1248, 320],
    }


def make_warehouse_view_create_tool() -> dict[str, Any]:
    return {
        "parameters": {
            "description": (
                "Use this tool after warehouse_table_schema_tool when the user asks to "
                "create a Feishu inventory view. Pass JSON with view_name, visible_fields, filters, and sorts."
            ),
            "jsCode": WAREHOUSE_VIEW_CREATE_TOOL_JS,
        },
        "id": "warehouse-view-create-tool-node",
        "name": "warehouse_view_create_tool",
        "type": "@n8n/n8n-nodes-langchain.toolCode",
        "typeVersion": 1.3,
        "position": [1248, 320],
    }


def make_agent_node(source_agent: dict[str, Any]) -> dict[str, Any]:
    agent = copy.deepcopy(source_agent)
    agent["type"] = "@n8n/n8n-nodes-langchain.agent"
    agent["parameters"].pop("toolDescription", None)
    agent["parameters"]["promptType"] = "define"
    agent["position"] = [320, 120]
    return agent


def update_warehouse_agent_prompt(agent: dict[str, Any]) -> None:
    if agent.get("name") != "Warehouse Agent":
        return
    system_message = agent["parameters"]["options"]["systemMessage"]
    system_message = system_message.replace(
        "- warehouse_fulfillment_tool：判断 SKU 是否可以发货，并返回阻塞原因和下一步动作。",
        "- warehouse_fulfillment_tool：判断 SKU 是否可以发货，并返回阻塞原因和下一步动作。\n"
        "- warehouse_inventory_table_provision_tool：仅在用户明确要求创建、初始化或配置飞书库存表时，创建固定 schema 的飞书表格。\n"
        "- warehouse_inventory_table_sync_tool：仅在用户要求同步、导出、发布或展示到飞书表格时，把 SKU 库存快照同步到飞书表格。\n"
        "- warehouse_table_schema_tool：当用户要求创建飞书库存视图时，先读取当前表字段、字段类型、颜色选项和已有视图。\n"
        "- warehouse_view_create_tool：读取 schema 后，用受控 JSON 创建或复用飞书库存视图。",
    )
    system_message = system_message.replace(
        "4. 如果用户提供了 sku_ 开头的 SKU，结合问题类型调用对应工具；必要时可以连续调用多个工具。",
        "4. 如果用户要求创建、初始化或配置飞书库存表，先调用 warehouse_inventory_table_provision_tool；如果同时要求同步某个 sku_ 开头的 SKU，再调用 warehouse_inventory_tool 获取事实，然后调用 warehouse_inventory_table_sync_tool 同步快照。\n"
        "5. 如果用户要求同步、导出、发布、表格、飞书表格或看板，并提供了 sku_ 开头的 SKU，先调用 warehouse_inventory_tool 获取事实，再调用 warehouse_inventory_table_sync_tool 同步快照。\n"
        "6. 如果用户要求创建某个飞书库存视图，必须先调用 warehouse_table_schema_tool，再调用 warehouse_view_create_tool；warehouse_view_create_tool 的输入必须是 JSON，包含 view_name、visible_fields、filters 和 sorts；不要编造 schema 中不存在的字段。\n"
        "7. 如果用户只是查询库存或履约风险，不要调用 warehouse_inventory_table_provision_tool、warehouse_inventory_table_sync_tool、warehouse_table_schema_tool 或 warehouse_view_create_tool。",
    )
    renumbering = {
        "5. 如果用户没有提供 SKU": "8. 如果用户没有提供 SKU",
        "6. 回复必须保留工具返回": "9. 回复必须保留工具返回",
        "7. 不要创建采购单": "10. 不要创建采购单",
        "8. 不要编造仓库或库存数据": "11. 不要编造仓库或库存数据",
        "9. 回复必须简洁": "12. 回复必须简洁",
    }
    for old, new in renumbering.items():
        system_message = system_message.replace(old, new)
    agent["parameters"]["options"]["systemMessage"] = system_message


def make_memory_node(template: dict[str, Any], name: str, prefix: str) -> dict[str, Any]:
    memory = copy.deepcopy(template)
    memory["name"] = name
    memory["id"] = f"{prefix}-postgres-chat-memory"
    memory["position"] = [520, 320]
    memory["parameters"]["sessionKey"] = (
        f"={{{{ '{prefix}:' + ($json.session_id || 'feishu:' + $json.chat_id + ':' + $json.sender_id) }}}}"
    )
    return memory


def base_workflow(source: dict[str, Any], department: dict[str, Any]) -> dict[str, Any]:
    workflow = copy.deepcopy(source)
    workflow["id"] = department["workflow_id"]
    workflow["name"] = department["workflow_name"]
    workflow["description"] = (
        f"Independent department workflow for {department['workflow_name']}."
    )
    workflow["nodes"] = []
    workflow["connections"] = {}
    workflow["pinData"] = {}
    workflow["tags"] = []
    workflow["shared"] = []
    workflow["versionCounter"] = 1
    workflow["triggerCount"] = 0
    workflow.pop("activeVersionId", None)
    workflow.pop("versionId", None)
    return workflow


def add_connection(workflow: dict[str, Any], source: str, channel: str, target: str) -> None:
    workflow["connections"].setdefault(source, {}).setdefault(channel, [])
    workflow["connections"][source][channel].append(
        [{"node": target, "type": channel, "index": 0}]
    )


def build_department_workflow(source: dict[str, Any], department: dict[str, Any]) -> dict[str, Any]:
    workflow = base_workflow(source, department)

    webhook = clone_node(source, "When Chat Message Received")
    webhook["parameters"]["path"] = department["webhook_path"]
    webhook["position"] = [-720, 80]

    normalize = clone_node(source, "Normalize Inbound Message")
    normalize["position"] = [-496, 80]

    agent = make_agent_node(clone_node(source, department["agent"]))
    update_warehouse_agent_prompt(agent)
    model = clone_node(source, department["model"])
    model["position"] = [256, 320]

    memory_template = clone_node(source, "Customer Support Postgres Chat Memory")
    memory = (
        clone_node(source, department["memory"])
        if department["memory"] == "Customer Support Postgres Chat Memory"
        else make_memory_node(memory_template, department["memory"], department["memory_prefix"])
    )
    memory["position"] = [512, 320]

    format_reply = clone_node(source, "Format Webhook Reply")
    format_reply["position"] = [720, 80]
    format_reply["parameters"]["jsCode"] = FORMAT_REPLY_JS
    respond = clone_node(source, "Respond to Webhook")
    respond["position"] = [944, 80]

    tools = []
    for index, tool_name in enumerate(department["tools"]):
        tool = (
            make_warehouse_provision_tool()
            if tool_name == "warehouse_inventory_table_provision_tool"
            else
            make_warehouse_sync_tool()
            if tool_name == "warehouse_inventory_table_sync_tool"
            else
            make_warehouse_schema_tool()
            if tool_name == "warehouse_table_schema_tool"
            else
            make_warehouse_view_create_tool()
            if tool_name == "warehouse_view_create_tool"
            else clone_node(source, tool_name)
        )
        tool["position"] = [720 + index * 176, 320]
        tools.append(tool)

    workflow["nodes"] = [webhook, normalize, agent, model, memory, *tools, format_reply, respond]

    add_connection(workflow, "When Chat Message Received", "main", "Normalize Inbound Message")
    add_connection(workflow, "Normalize Inbound Message", "main", department["agent"])
    add_connection(workflow, department["agent"], "main", "Format Webhook Reply")
    add_connection(workflow, "Format Webhook Reply", "main", "Respond to Webhook")
    add_connection(workflow, department["model"], "ai_languageModel", department["agent"])
    add_connection(workflow, department["memory"], "ai_memory", department["agent"])
    for tool_name in department["tools"]:
        add_connection(workflow, tool_name, "ai_tool", department["agent"])

    return workflow


def main() -> None:
    source = load_source_workflow()
    for department in DEPARTMENTS.values():
        workflow = build_department_workflow(source, department)
        target = TARGET_DIR / department["filename"]
        target.write_text(
            json.dumps([workflow], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
