# Multi-domain Agent Workflow Design

Date: 2026-05-21

## Goal

Extend the current Feishu to n8n parent/son workflow from a single after-sales specialist into a clearer enterprise operations demo with multiple business-domain agents.

The first implementation should be useful but controlled:

- Keep Feishu protocol handling in `feishu-adapter`.
- Keep orchestration, visual layout, and parent/son dispatch in n8n.
- Keep deterministic business tools in `ai-service` or `mock-api`.
- Preserve the user's manually adjusted n8n canvas spacing by starting from a live workflow export.
- Use English names for agent nodes and tool names.

## Current Baseline

The current live workflow was exported from n8n workflow ID `wechat-qwen-agent-template` to:

`n8n/workflows/chat-parent-son-agent.live-2026-05-21.json`

n8n currently contains two workflows with the same display name `Wechat Gateway to Qwen Agent`, so future operations must target workflow ID `wechat-qwen-agent-template`, not only the display name.

## Agent Set

### Parent Agent

Purpose: routing only.

The parent should decide which specialist agent to call and should not directly answer business questions that require internal tools.

Routing targets:

- `Customer Support Agent`
- `Warehouse Agent`
- `Procurement Agent`
- `Operations Agent`
- existing `Weather Agent`, if still present in the live workflow
- simple test or echo tool, only for explicit test requests

### Customer Support Agent

This replaces the old `after_sales_agent` role.

Responsibilities:

- order status lookup
- shipment status lookup when answering customer questions
- refund, return, exchange, compensation, and complaint handling
- policy lookup with metadata citations
- short conversational references such as "this order" or "the previous order"

State:

- n8n Postgres Chat Memory remains scoped with a `customer_support:` namespace.
- `ai-service` `session_state` continues storing durable backend state such as `last_order_id`.
- `user_profile` remains reserved for compact user-level facts and future summaries.

### Warehouse Agent

This is the first new fully wired specialist.

Responsibilities:

- inventory checks by SKU
- shipment or fulfillment status checks
- warehouse exception summaries
- practical response for customer support or operations use

First-pass tools:

- `inventory_status_tool`: call a backend API to return SKU availability, pending orders, and reorder threshold.
- `shipment_status_tool` or reuse the existing order/shipment data where practical.

The first version should use mock data through `mock-api`, not a real ERP or WMS.

### Procurement Agent

This is a first-pass placeholder specialist.

Responsibilities:

- identify replenishment, supplier, purchase order, and lead-time requests
- return a structured mock result
- clearly state when no real procurement system is connected

First-pass tool:

- `procurement_mock_tool`

### Operations Agent

This is a first-pass placeholder specialist.

Responsibilities:

- summarize operational incidents
- prepare daily or weekly operational summaries
- group cross-domain signals from customer support, warehouse, and procurement
- return structured mock output for future dashboard/report work

First-pass tool:

- `operations_mock_tool`

## Workflow Shape

Inbound path stays unchanged:

Feishu -> `feishu-adapter` -> n8n `/webhook/chat-agent-inbound` -> fast path check -> Parent Agent -> specialist agent -> tool/API -> formatted webhook reply -> Feishu

Fast path remains before the Parent Agent for clear customer-support order/refund requests. It may keep using the current `/after-sales/fast-path` endpoint internally for now, but user-facing node names should move toward `Customer Support` naming. Endpoint renaming can be a later compatibility-safe change.

The n8n canvas should use separated horizontal or vertical lanes for specialist agents:

- Parent and inbound handling near the left/top
- Customer Support lane
- Warehouse lane
- Procurement lane
- Operations lane
- shared model/memory/tool nodes placed close to their owning agent

## Prompt Rules

Parent prompt:

- Use Chinese to reply to users.
- Use English agent names when referring to tools internally.
- Route customer service, order, refund, return, exchange, complaint, or policy questions to `Customer Support Agent`.
- Route inventory, warehouse, fulfillment, stock, dispatch, picking, packing, or shipment operations questions to `Warehouse Agent`.
- Route supplier, purchase, replenishment, procurement, lead time, or purchase order questions to `Procurement Agent`.
- Route daily report, metrics, incident summary, operational summary, or cross-domain analysis requests to `Operations Agent`.
- If a task spans multiple domains, call the most specific first agent and summarize the result; multi-agent chaining can be added later.

Specialist prompts:

- Do not invent internal system data.
- Call the relevant tool before answering when data is needed.
- Return concise Chinese suitable for Feishu.
- Include source metadata when policy or knowledge-base content is used.
- Say clearly when a backend is mock-only or not connected.

## Data and API Design

Keep the first pass simple:

- Reuse existing `fixtures/data/inventory.json` for `Warehouse Agent`.
- Reuse existing order and shipment fixtures where possible.
- Add only narrow mock endpoints if existing endpoints are not enough.
- Keep procurement and operations tools deterministic and inspectable.

Candidate backend endpoints:

- `GET /inventory/{sku}` or existing inventory lookup equivalent
- `GET /shipments/{shipment_id}`
- `POST /procurement/mock`
- `POST /operations/summary/mock`

## Testing

Required tests before importing the workflow:

- workflow JSON contains all English agent names
- Parent Agent prompt routes to the correct agent names
- `Customer Support Agent` keeps policy search and order status tools
- `Warehouse Agent` has at least one backend API tool
- `Procurement Agent` and `Operations Agent` exist as placeholders with mock tools
- memory namespaces are not shared blindly between agents
- workflow connections preserve the path through Parent Agent before specialists

Runtime smoke tests:

- customer support: `帮我查一下订单 ord_100`
- warehouse: `sku_bag_1 还有多少库存`
- procurement: `sku_bag_1 需要补货吗`
- operations: `帮我总结今天的运营异常`

## Non-goals

- No real ERP, WMS, OMS, or procurement system integration in this phase.
- No multi-agent recursive chaining in this phase.
- No large UI redesign beyond preserving the user's n8n layout and adding clear lanes.
- No migration of Feishu adapter behavior.

## Implementation Recommendation

Implement in two small slices:

1. Rename and harden `Customer Support Agent`, then add `Warehouse Agent` with a real mock-api-backed tool.
2. Add `Procurement Agent` and `Operations Agent` as routed placeholders with deterministic mock tools.

This keeps the demo useful while avoiding a large brittle workflow edit.
