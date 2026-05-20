# After-sales Son Agent Design

## Goal

Add a dedicated after-sales son agent to the existing n8n chat gateway so Feishu messages can be routed by the parent agent to a specialist that calls backend APIs and returns the result to Feishu.

## Current Flow

`feishu-adapter` receives Feishu events at `/feishu/events`, normalizes the message, forwards it to n8n `/webhook/chat-agent-inbound`, and sends the returned `reply` back to Feishu.

The active n8n workflow is `Wechat Gateway to Qwen Agent` with id `wechat-qwen-agent-template`. It already has a parent agent, a weather son agent, a weather tool, and an echo test tool.

## Target Flow

```text
Feishu -> feishu-adapter -> n8n chat webhook -> Parent Agent
Parent Agent -> after_sales_agent -> order_status_tool -> mock-api
Parent Agent -> Format Webhook Reply -> feishu-adapter -> Feishu
```

## Parent Agent Prompt

The parent agent only identifies task type and dispatches. It must not fabricate business data. Routing rules:

- Weather, temperature, rain, forecast, umbrella questions go to `weather_agent`.
- Order status, logistics, delivery, refund, return, exchange, after-sales, complaint, or shipping-delay questions go to `after_sales_agent`.
- Explicit test, echo, or link verification requests go to `echo_task_tool`.
- Plain chat may be answered briefly.

## After-sales Son Agent

`after_sales_agent` handles ecommerce after-sales requests. For this phase it supports order and logistics status. It must call `order_status_tool` when the user gives an order id such as `ord_100`, and it must ask for an order id when missing.

The response should be Chinese and include:

- order id
- order status
- carrier
- shipment status
- delay days
- action suggestion

## Tool

`order_status_tool` is an n8n LangChain code tool. It receives a natural-language query or JSON, extracts an `ord_*` order id, then calls:

- `http://mock-api:8000/orders/{order_id}`
- `http://mock-api:8000/shipments/{shipment_id}`

It returns JSON to the son agent. If the order id is missing or the backend fails, it returns a structured error instead of invented data.

## Verification

- JSON structure for the workflow must parse.
- The workflow must include parent agent prompt routing for `after_sales_agent`.
- The workflow must include an `after_sales_agent` node connected as a tool to the parent agent.
- The workflow must include an `order_status_tool` node connected as a tool to `after_sales_agent`.
- Import and publish the workflow in the current n8n container.
- A webhook smoke request for `帮我查一下订单 ord_100` should return a reply containing `ord_100`.
