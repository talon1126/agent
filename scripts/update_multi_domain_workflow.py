import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "n8n" / "workflows" / "chat-parent-son-agent.live-2026-05-21.json"
TARGET = ROOT / "n8n" / "workflows" / "chat-parent-son-agent.json"


PARENT_PROMPT = """你是 Parent Agent，通过飞书等聊天软件接收用户任务。

你的职责是只负责判断任务类型和派发，不直接执行专业任务，不要自己编造订单、库存、采购、运营或内部系统结果。

可用工具：
- Customer Support Agent：客服专员。凡是用户询问订单状态、物流、发货、配送延迟、退款、退货、换货、售后投诉、订单异常、售后政策，都必须调用它。
- Warehouse Agent：仓储专员。凡是用户询问库存、SKU、仓库、履约、发货作业、拣货、打包、库存不足、仓储异常，都必须调用它。
- Procurement Agent：采购专员。凡是用户询问补货、供应商、采购单、采购建议、交期、缺货补采，都必须调用它。
- Operations Agent：运营专员。凡是用户询问日报、周报、运营异常、指标汇总、跨部门总结、经营分析，都必须调用它。
- echo_task_tool：测试工具。只有在用户明确要求测试、回显、记录任务、验证链路时才调用。

派发规则：
1. 客服、订单、物流、退款、退货、换货、投诉和政策问题必须交给 Customer Support Agent。
2. 库存、SKU、仓库、履约、发货作业和仓储异常必须交给 Warehouse Agent。
3. 补货、供应商、采购单、采购建议和交期必须交给 Procurement Agent。
4. 日报、周报、运营异常、指标汇总和跨域总结必须交给 Operations Agent。
5. 只要用户消息里出现 ord_ 开头的订单号，就优先调用 Customer Support Agent。
6. 只要用户消息里出现 sku_ 开头的 SKU，且问题与库存、补货、采购或运营相关，就根据关键词分发给 Warehouse Agent、Procurement Agent 或 Operations Agent。
7. 非上述请求如果只是普通聊天，可以直接简短回复。
8. 回复用户时使用中文，保留子 Agent 返回的关键数据；如果子 Agent 返回表格或结构化内容，尽量保持格式。"""


CUSTOMER_SUPPORT_PROMPT = """你是 Customer Support Agent，专门处理电商客服、订单、物流、退款、退货、换货和投诉问题。

你有短期会话记忆。用户说“这个订单”“该订单”“刚才那个订单”时，先从最近对话中找到 ord_ 开头的订单号；如果记忆中没有订单号，再请用户提供订单号，例如 ord_100。

可用工具：
- order_status_tool：查询订单和物流状态。涉及订单状态、物流、配送延迟、退款资格判断时必须调用，不要凭经验猜测。
- policy_search_tool：检索公司售后政策。涉及退款、退货、换货、审批、补偿、物流赔偿、差评处理时必须调用，不要凭常识编造政策。

处理规则：
1. 如果用户提供了 ord_ 开头的订单号，必须调用 order_status_tool。
2. 如果用户没有提供订单号，但上下文记忆里有最近订单号，可以使用该订单号继续处理，并在回复中说明“我按上一单 <订单号> 处理”。
3. 退款、退货、换货、审批和补偿问题必须调用 policy_search_tool，并在回复中明确引用 source_file、section、clause_id 和 clause_title。
4. 如果 policy_search_tool 没有返回 matches，回复“未找到对应公司政策，需要人工确认”，不要编造条款。
5. 如果 order_status_tool 返回查询成功，必须直接转述并保留订单状态、物流商、物流状态、延迟天数和行动建议；如果工具返回结构化 JSON，优先使用其中的 summary、order 和 shipment 字段。
6. 如果 shipment_status 是 delayed 或 delay_days 大于 0，要明确说明延迟情况，并建议客服跟进或安抚客户。
7. 只有当工具明确返回 missing_order_id、lookup_failed 或 runtime_error 时，才说明无法查询该订单，并要求人工跟进。
8. 回复必须简洁，适合直接发回飞书。"""


WAREHOUSE_PROMPT = """你是 Warehouse Agent，专门处理仓储、库存、履约和发货作业问题。

可用工具：
- inventory_status_tool：按 SKU 查询库存、待处理订单和补货阈值。涉及库存、现货、缺货、仓储异常或履约风险时必须调用。

处理规则：
1. 如果用户提供了 sku_ 开头的 SKU，必须调用 inventory_status_tool。
2. 如果用户没有提供 SKU，请要求用户提供 SKU，例如 sku_bag_1。
3. 回复必须包含 SKU、可用库存、待处理订单、补货阈值和行动建议。
4. 不要编造仓库或库存数据；工具不可用时说明需要人工确认。
5. 回复必须简洁，适合直接发回飞书。"""


PROCUREMENT_PROMPT = """你是 Procurement Agent，专门处理采购、补货、供应商和采购单问题。

可用工具：
- procurement_mock_tool：根据 SKU 和库存信号返回 mock 采购建议。涉及补货、采购建议、供应商、采购单或交期时必须调用。

处理规则：
1. 如果用户提供了 sku_ 开头的 SKU，必须调用 procurement_mock_tool。
2. 如果用户没有提供 SKU，请要求用户提供 SKU，例如 sku_bag_1。
3. 明确说明当前连接的是 mock-procurement，不是真实采购系统。
4. 回复必须包含建议动作、库存依据和下一步。"""


OPERATIONS_PROMPT = """你是 Operations Agent，专门处理运营异常、日报、周报、指标汇总和跨部门总结。

可用工具：
- operations_mock_tool：返回 mock 运营摘要、异常列表和下一步动作。涉及运营总结、日报、周报或跨域异常时必须调用。

处理规则：
1. 运营总结、日报、周报、异常汇总和跨部门分析必须调用 operations_mock_tool。
2. 明确说明当前连接的是 mock-operations，不是真实 BI 或运营系统。
3. 回复应包含摘要、主要异常和下一步动作。
4. 回复必须简洁，适合直接发回飞书。"""


INVENTORY_TOOL_CODE = r"""function parseMaybeJson(value) {
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
  const match = text.match(/\bsku_[0-9A-Za-z_]+\b/i);
  return match ? match[0].toLowerCase() : '';
}

try {
  const input = parseMaybeJson(query);
  const sku = extractSku(input);
  if (!sku) {
    return JSON.stringify({ ok: false, error: 'missing_sku', message: '请提供 SKU，例如 sku_bag_1。' });
  }

  const inventory = await helpers.httpRequest({
    method: 'GET',
    url: 'http://mock-api:8000/inventory/' + encodeURIComponent(sku),
    json: true
  });

  const available = Number(inventory.available || 0);
  const pendingOrders = Number(inventory.pending_orders || 0);
  const reorderThreshold = Number(inventory.reorder_threshold || 0);
  const risk = available < reorderThreshold || available < pendingOrders;
  return JSON.stringify({
    ok: true,
    system: 'mock-warehouse',
    sku,
    available,
    pending_orders: pendingOrders,
    reorder_threshold: reorderThreshold,
    risk_level: risk ? 'medium' : 'low',
    recommendation: risk ? '库存偏低，建议通知采购并关注待履约订单。' : '库存状态正常。'
  });
} catch (error) {
  return JSON.stringify({
    ok: false,
    error: 'inventory_status_runtime_error',
    message: error && error.message ? error.message : String(error)
  });
}"""


PROCUREMENT_TOOL_CODE = r"""function parseMaybeJson(value) {
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
  const match = text.match(/\bsku_[0-9A-Za-z_]+\b/i);
  return match ? match[0].toLowerCase() : '';
}

try {
  const input = parseMaybeJson(query);
  const sku = extractSku(input);
  const result = await helpers.httpRequest({
    method: 'POST',
    url: 'http://mock-api:8000/procurement/mock',
    body: { sku, query: String(first(input.query, input.text, input.input, query, '')) },
    json: true
  });
  return JSON.stringify(result);
} catch (error) {
  return JSON.stringify({
    ok: false,
    error: 'procurement_mock_runtime_error',
    message: error && error.message ? error.message : String(error)
  });
}"""


OPERATIONS_TOOL_CODE = r"""function parseMaybeJson(value) {
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
  const text = String(first(input.query, input.text, input.input, query, '')).trim();
  const result = await helpers.httpRequest({
    method: 'POST',
    url: 'http://mock-api:8000/operations/summary/mock',
    body: { query: text },
    json: true
  });
  return JSON.stringify(result);
} catch (error) {
  return JSON.stringify({
    ok: false,
    error: 'operations_mock_runtime_error',
    message: error && error.message ? error.message : String(error)
  });
}"""


SON_AGENT_Y = 688
MODEL_Y = 864
TOOL_Y = 928
NOTE_Y = 544
LANES = {
    "customer_support": {"x": 400, "note_x": 288, "tool_x": 720, "extra_tool_x": 896, "color": 4},
    "warehouse": {"x": 1120, "note_x": 1008, "tool_x": 1440, "color": 5},
    "procurement": {"x": 1840, "note_x": 1728, "tool_x": 2160, "color": 6},
    "operations": {"x": 2560, "note_x": 2448, "tool_x": 2880, "color": 7},
}


def load_workflow() -> tuple[dict, bool]:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    return (data[0], True) if isinstance(data, list) else (data, False)


def save_workflow(workflow: dict, wrapped: bool) -> None:
    data = [workflow] if wrapped else workflow
    TARGET.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_node(workflow: dict, name: str) -> dict:
    for node in workflow["nodes"]:
        if node["name"] == name:
            return node
    raise KeyError(name)


def rename_node(workflow: dict, old: str, new: str) -> None:
    find_node(workflow, old)["name"] = new
    if old in workflow["connections"]:
        workflow["connections"][new] = workflow["connections"].pop(old)
    for source_connections in workflow["connections"].values():
        for output_group in source_connections.values():
            for output in output_group:
                for connection in output:
                    if connection.get("node") == old:
                        connection["node"] = new


def remove_nodes(workflow: dict, names: set[str]) -> None:
    workflow["nodes"] = [node for node in workflow["nodes"] if node["name"] not in names]
    for name in names:
        workflow["connections"].pop(name, None)
    for source, source_connections in list(workflow["connections"].items()):
        for output_name, output_group in list(source_connections.items()):
            filtered_group = []
            for output in output_group:
                filtered = [connection for connection in output if connection.get("node") not in names]
                filtered_group.append(filtered)
            source_connections[output_name] = filtered_group


def add_connection(workflow: dict, source: str, channel: str, target: str) -> None:
    workflow["connections"].setdefault(source, {}).setdefault(channel, [])
    workflow["connections"][source][channel].append(
        [{"node": target, "type": channel, "index": 0}]
    )


def make_agent(name: str, node_id: str, description: str, prompt: str, position: list[int]) -> dict:
    return {
        "parameters": {
            "toolDescription": description,
            "text": "={{ $json.input_text }}",
            "options": {
                "systemMessage": prompt,
                "maxIterations": 3,
                "returnIntermediateSteps": True,
            },
        },
        "type": "@n8n/n8n-nodes-langchain.agentTool",
        "typeVersion": 3,
        "position": position,
        "id": node_id,
        "name": name,
    }


def make_model(template: dict, name: str, node_id: str, position: list[int]) -> dict:
    node = copy.deepcopy(template)
    node["name"] = name
    node["id"] = node_id
    node["position"] = position
    return node


def make_tool(name: str, node_id: str, description: str, js_code: str, position: list[int]) -> dict:
    return {
        "parameters": {
            "description": description,
            "jsCode": js_code,
        },
        "id": node_id,
        "name": name,
        "type": "@n8n/n8n-nodes-langchain.toolCode",
        "typeVersion": 1.3,
        "position": position,
    }


def make_note(name: str, content: str, position: list[int], color: int) -> dict:
    return {
        "parameters": {
            "content": content,
            "height": 592,
            "width": 704,
            "color": color,
        },
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type": "n8n-nodes-base.stickyNote",
        "typeVersion": 1,
        "position": position,
    }


def update_prompts(workflow: dict) -> None:
    parent = find_node(workflow, "Parent Agent")
    parent["parameters"]["options"]["systemMessage"] = PARENT_PROMPT

    customer_support = find_node(workflow, "Customer Support Agent")
    customer_support["parameters"]["toolDescription"] = (
        "Customer Support specialist for ecommerce orders, shipment, refunds, returns, exchanges, complaints, and policy lookup."
    )
    customer_support["parameters"]["options"]["systemMessage"] = CUSTOMER_SUPPORT_PROMPT


def update_fast_path_names_and_copy(workflow: dict) -> None:
    rename_node(workflow, "Detect Fast After-sales Path", "Detect Fast Customer Support Path")
    rename_node(workflow, "Is Fast After-sales Path?", "Is Fast Customer Support Path?")
    rename_node(workflow, "Run After-sales Fast Path", "Run Customer Support Fast Path")

    detector = find_node(workflow, "Detect Fast Customer Support Path")
    detector["parameters"]["jsCode"] = detector["parameters"]["jsCode"].replace(
        "after sales", "customer support"
    ).replace("After-sales", "Customer Support")


def main() -> None:
    workflow, wrapped = load_workflow()

    remove_nodes(workflow, {"weather_agent", "weather_forecast_tool", "Sticky Note1", "Alibaba Cloud Chat Model"})

    rename_node(workflow, "AI Agent", "Parent Agent")
    rename_node(workflow, "after_sales_agent", "Customer Support Agent")
    rename_node(workflow, "After-sales Qwen Chat Model", "Customer Support Qwen Chat Model")
    rename_node(workflow, "After-sales Postgres Chat Memory", "Customer Support Postgres Chat Memory")
    rename_node(workflow, "Sticky Note - After-sales Agent", "Sticky Note - Customer Support Agent")
    update_fast_path_names_and_copy(workflow)

    customer_memory = find_node(workflow, "Customer Support Postgres Chat Memory")
    customer_memory["parameters"]["sessionKey"] = customer_memory["parameters"]["sessionKey"].replace(
        "after_sales:", "customer_support:"
    )
    for memory_name in ("Parent Postgres Chat Memory", "Customer Support Postgres Chat Memory"):
        memory = find_node(workflow, memory_name)
        memory["parameters"]["tableName"] = "n8n_chat_histories"
        memory["parameters"]["contextWindowLength"] = 6

    customer_sticky = find_node(workflow, "Sticky Note - Customer Support Agent")
    customer_sticky["parameters"]["content"] = "# Customer Support Agent\n"
    customer_sticky["parameters"]["height"] = 592
    customer_sticky["parameters"]["width"] = 704
    customer_sticky["parameters"]["color"] = LANES["customer_support"]["color"]
    customer_sticky["position"] = [LANES["customer_support"]["note_x"], NOTE_Y]

    parent_sticky = find_node(workflow, "Sticky Note")
    parent_sticky["parameters"]["content"] = "# Parent Agent\n"
    parent_sticky["parameters"]["color"] = 3
    parent_sticky["name"] = "Sticky Note - Parent Agent"

    update_prompts(workflow)

    find_node(workflow, "Customer Support Agent")["position"] = [LANES["customer_support"]["x"], SON_AGENT_Y]
    find_node(workflow, "Customer Support Qwen Chat Model")["position"] = [
        LANES["customer_support"]["x"] - 16,
        MODEL_Y,
    ]
    find_node(workflow, "Customer Support Postgres Chat Memory")["position"] = [
        LANES["customer_support"]["x"] + 144,
        MODEL_Y,
    ]
    find_node(workflow, "order_status_tool")["position"] = [
        LANES["customer_support"]["tool_x"],
        TOOL_Y,
    ]
    find_node(workflow, "policy_search_tool")["position"] = [
        LANES["customer_support"]["extra_tool_x"],
        TOOL_Y,
    ]

    model_template = find_node(workflow, "Customer Support Qwen Chat Model")
    workflow["nodes"].extend(
        [
            make_agent(
                "Warehouse Agent",
                "warehouse-agent-node",
                "Warehouse specialist for inventory, stock, fulfillment, shipment operations, and warehouse exceptions.",
                WAREHOUSE_PROMPT,
                [LANES["warehouse"]["x"], SON_AGENT_Y],
            ),
            make_model(
                model_template,
                "Warehouse Qwen Chat Model",
                "warehouse-qwen-chat-model-node",
                [LANES["warehouse"]["x"] - 16, MODEL_Y],
            ),
            make_tool(
                "inventory_status_tool",
                "inventory-status-tool-node",
                "Use this backend API tool for SKU inventory, pending orders, reorder threshold, and fulfillment risk lookup.",
                INVENTORY_TOOL_CODE,
                [LANES["warehouse"]["tool_x"], TOOL_Y],
            ),
            make_note(
                "Sticky Note - Warehouse Agent",
                "# Warehouse Agent\n",
                [LANES["warehouse"]["note_x"], NOTE_Y],
                LANES["warehouse"]["color"],
            ),
            make_agent(
                "Procurement Agent",
                "procurement-agent-node",
                "Procurement specialist for replenishment, purchase recommendations, suppliers, purchase orders, and lead time questions.",
                PROCUREMENT_PROMPT,
                [LANES["procurement"]["x"], SON_AGENT_Y],
            ),
            make_model(
                model_template,
                "Procurement Qwen Chat Model",
                "procurement-qwen-chat-model-node",
                [LANES["procurement"]["x"] - 16, MODEL_Y],
            ),
            make_tool(
                "procurement_mock_tool",
                "procurement-mock-tool-node",
                "Use this mock procurement API tool for SKU replenishment and purchase request recommendations.",
                PROCUREMENT_TOOL_CODE,
                [LANES["procurement"]["tool_x"], TOOL_Y],
            ),
            make_note(
                "Sticky Note - Procurement Agent",
                "# Procurement Agent\n",
                [LANES["procurement"]["note_x"], NOTE_Y],
                LANES["procurement"]["color"],
            ),
            make_agent(
                "Operations Agent",
                "operations-agent-node",
                "Operations specialist for daily summaries, incident summaries, metrics, and cross-domain operational analysis.",
                OPERATIONS_PROMPT,
                [LANES["operations"]["x"], SON_AGENT_Y],
            ),
            make_model(
                model_template,
                "Operations Qwen Chat Model",
                "operations-qwen-chat-model-node",
                [LANES["operations"]["x"] - 16, MODEL_Y],
            ),
            make_tool(
                "operations_mock_tool",
                "operations-mock-tool-node",
                "Use this mock operations API tool for operational summaries, incident lists, and next actions.",
                OPERATIONS_TOOL_CODE,
                [LANES["operations"]["tool_x"], TOOL_Y],
            ),
            make_note(
                "Sticky Note - Operations Agent",
                "# Operations Agent\n",
                [LANES["operations"]["note_x"], NOTE_Y],
                LANES["operations"]["color"],
            ),
        ]
    )

    add_connection(workflow, "Warehouse Agent", "ai_tool", "Parent Agent")
    add_connection(workflow, "Procurement Agent", "ai_tool", "Parent Agent")
    add_connection(workflow, "Operations Agent", "ai_tool", "Parent Agent")

    add_connection(workflow, "Warehouse Qwen Chat Model", "ai_languageModel", "Warehouse Agent")
    add_connection(workflow, "Procurement Qwen Chat Model", "ai_languageModel", "Procurement Agent")
    add_connection(workflow, "Operations Qwen Chat Model", "ai_languageModel", "Operations Agent")

    add_connection(workflow, "inventory_status_tool", "ai_tool", "Warehouse Agent")
    add_connection(workflow, "procurement_mock_tool", "ai_tool", "Procurement Agent")
    add_connection(workflow, "operations_mock_tool", "ai_tool", "Operations Agent")

    save_workflow(workflow, wrapped)


if __name__ == "__main__":
    main()
