import fs from "node:fs/promises";

const workflowPath = "n8n/workflows/chat-parent-son-agent.json";
const data = JSON.parse(await fs.readFile(workflowPath, "utf8"));
const workflow = Array.isArray(data) ? data[0] : data;

function removeNode(name) {
  workflow.nodes = workflow.nodes.filter((node) => node.name !== name);
  delete workflow.connections[name];
  for (const source of Object.keys(workflow.connections)) {
    for (const channel of Object.keys(workflow.connections[source])) {
      workflow.connections[source][channel] = workflow.connections[source][channel].map((group) =>
        group.filter((target) => target.node !== name),
      );
    }
  }
}

[
  "after_sales_agent",
  "order_status_tool",
  "shipment_status_tool",
  "After-sales Qwen Chat Model",
  "Sticky Note - After-sales Agent",
  "Detect After-sales Order",
  "Is After-sales Order?",
  "Run After-sales API Tool",
].forEach(removeNode);

const parent = workflow.nodes.find((node) => node.name === "AI Agent");
parent.parameters.options.systemMessage = `你是 Parent Agent，通过飞书等聊天软件接收用户任务。

你的职责是只负责判断任务类型和派发，不直接执行专业任务，不要自己编造天气、订单、物流、退款或内部系统结果。

可用工具：
- weather_agent：天气专员。凡是用户询问天气、温度、降雨概率、未来预报、是否下雨、是否需要带伞，都必须调用它。
- after_sales_agent：电商售后专员。凡是用户询问订单状态、物流、发货、配送延迟、退款、退货、换货、售后投诉、订单异常，都必须调用它。
- echo_task_tool：测试工具。只有在用户明确要求测试、回显、记录任务、验证链路时才调用。

派发规则：
1. 天气相关请求必须交给 weather_agent，不要自己编造天气结果。
2. 售后、订单、物流、退款、退货、换货、投诉相关请求必须交给 after_sales_agent，不要自己编造订单或物流结果。
3. 只要用户消息里出现 ord_ 开头的订单号，就必须调用 after_sales_agent，并把用户原文作为工具输入。
4. 非上述请求如果只是普通聊天，可以直接简短回复。
5. 需要后续接入表单、总结、待办或其他内部系统时，先说明当前尚未接入对应执行工具，不要声称已经完成外部操作。
6. 回复用户时使用中文，保留子 Agent 返回的关键数据；如果子 Agent 返回表格或结构化内容，尽量保持格式。`;
parent.parameters.options.returnIntermediateSteps = true;

const qwenNode =
  workflow.nodes.find((node) => node.name === "Alibaba Cloud Chat Model") ||
  workflow.nodes.find((node) => node.name === "Qwen3.6 Plus Chat Model");
const qwenCredentials = qwenNode?.credentials;

const afterSalesAgent = {
  parameters: {
    toolDescription:
      "电商售后专员。用于处理订单状态、物流状态、配送延迟、退款、退货、换货、售后投诉等问题。输入是用户原始售后问题。",
    text: "={{ $json.input_text }}",
    hasOutputParser: false,
    options: {
      systemMessage: `你是 After-sales Agent，专门处理电商售后、订单和物流问题。

你必须使用 order_status_tool 查询订单和物流状态，不要凭经验猜测。

处理规则：
1. 如果用户提供了 ord_ 开头的订单号，必须调用 order_status_tool。
2. 如果用户没有提供订单号，回复用户：请提供订单号，例如 ord_100。
3. 如果 order_status_tool 返回“查询成功”，必须直接转述工具返回的订单状态、物流商、物流状态、延迟天数和行动建议，不要改写成系统异常。
4. 如果 shipment_status 是 delayed 或 delay_days 大于 0，要明确说明延迟情况，并建议客服跟进或安抚客户。
5. 只有当工具明确返回 missing_order_id、lookup_failed 或 runtime_error 时，才说明无法查询该订单，并要求人工跟进，不要编造数据。
6. 回复必须简洁，适合直接发回飞书。`,
      maxIterations: 5,
      returnIntermediateSteps: true,
    },
  },
  type: "@n8n/n8n-nodes-langchain.agentTool",
  typeVersion: 3,
  position: [112, 576],
  id: "after-sales-agent-node",
  name: "after_sales_agent",
};

const afterSalesModel = {
  parameters: {
    options: { maxTokens: 1200, temperature: 0.2, topP: 0.9 },
    model: "qwen3.6-plus",
  },
  type: "@n8n/n8n-nodes-langchain.lmChatAlibabaCloud",
  typeVersion: 1,
  position: [96, 752],
  id: "after-sales-qwen-chat-model-node",
  name: "After-sales Qwen Chat Model",
};
if (qwenCredentials) {
  afterSalesModel.credentials = qwenCredentials;
}

const orderStatusCode = `function parseMaybeJson(value) {
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

function extractOrderId(input) {
  const text = String(first(input.order_id, input.orderId, input.input, input.query, input.text, '')).trim();
  const match = text.match(/\\bord_[0-9A-Za-z]+\\b/i);
  return match ? match[0].toLowerCase() : '';
}

try {
  const input = parseMaybeJson(query);
  const orderId = extractOrderId(input);
  if (!orderId) {
    return JSON.stringify({ ok: false, error: 'missing_order_id', message: '请提供订单号，例如 ord_100。' });
  }

  const orderUrl = 'http://mock-api:8000/orders/' + encodeURIComponent(orderId);
  const order = await helpers.httpRequest({
    method: 'GET',
    url: orderUrl,
    json: true
  });
  if (!order.shipment_id) {
    return JSON.stringify({ ok: false, error: 'missing_shipment_id', step: 'parse_order', order_id: orderId, order });
  }

  const shipmentUrl = 'http://mock-api:8000/shipments/' + encodeURIComponent(order.shipment_id);
  const shipment = await helpers.httpRequest({
    method: 'GET',
    url: shipmentUrl,
    json: true
  });

  const suggestion = Number(shipment.delay_days || 0) > 0 || shipment.status === 'delayed'
    ? '建议客服主动安抚客户，并跟进物流延迟原因。'
    : '建议告知客户订单状态正常，无需人工介入。';
  return [
    '查询成功。',
    '订单号：' + order.order_id,
    '订单状态：' + order.status,
    '物流商：' + shipment.carrier,
    '物流状态：' + shipment.status,
    '延迟天数：' + shipment.delay_days,
    '行动建议：' + suggestion
  ].join('\\n');
} catch (error) {
  return JSON.stringify({
    ok: false,
    error: 'tool_runtime_error',
    step: 'unexpected_exception',
    message: error && error.message ? error.message : String(error)
  });
}`;

const orderStatusTool = {
  id: "order-status-tool-node",
  name: "order_status_tool",
  type: "@n8n/n8n-nodes-langchain.toolCode",
  typeVersion: 1.3,
  position: [432, 816],
  parameters: {
    description:
      "Use this backend API tool for ecommerce after-sales order status and shipment status lookup. Input can be a natural language order question or JSON with order_id/query/text/input.",
    jsCode: orderStatusCode,
  },
};

const sticky = {
  parameters: { content: "# After-sales Agent\n", height: 592, width: 704 },
  type: "n8n-nodes-base.stickyNote",
  typeVersion: 1,
  position: [0, 432],
  id: "after-sales-sticky-note-node",
  name: "Sticky Note - After-sales Agent",
};

workflow.nodes.push(
  afterSalesAgent,
  afterSalesModel,
  orderStatusTool,
  sticky,
);
workflow.connections["Normalize Inbound Message"] = {
  main: [[{ node: "Has Text Or Recognition?", type: "main", index: 0 }]],
};
workflow.connections.after_sales_agent = {
  ai_tool: [[{ node: "AI Agent", type: "ai_tool", index: 0 }]],
};
workflow.connections["After-sales Qwen Chat Model"] = {
  ai_languageModel: [[{ node: "after_sales_agent", type: "ai_languageModel", index: 0 }]],
};
workflow.connections.order_status_tool = {
  ai_tool: [[{ node: "after_sales_agent", type: "ai_tool", index: 0 }]],
};
workflow.versionCounter = (workflow.versionCounter || 0) + 1;

await fs.writeFile(workflowPath, `${JSON.stringify(Array.isArray(data) ? [workflow] : workflow, null, 2)}\n`);
