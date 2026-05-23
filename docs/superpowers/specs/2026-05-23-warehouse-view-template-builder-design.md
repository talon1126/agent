# Warehouse View Template Builder Design

Date: 2026-05-23

## Goal

Let non-technical warehouse employees create Feishu Bitable views by using plain business language, not field names, JSON, filters, or sort syntax.

The user experience should be:

```text
@Warehouse Help me create a high-risk inventory view.
```

The system should resolve that request into a controlled template, read the backend warehouse data/table schema, create or reuse the Feishu view, and return a concise business reply with `view_id`, selected fields, filters, and sort rules.

## Problem

The current warehouse view creation path can create a Feishu view, but it still expects semi-technical language such as:

```text
Create a view with SKU, Warehouse, Available, Risk Level, Recommendation, filter Risk Level=high, sort by Available ascending.
```

This is not the target user experience. Internal employees should not need to know exact field names, English labels, API payloads, or filter syntax. Letting the Agent freely generate those details caused repeated tool calls and `422` payload errors.

## Recommended Approach

Use a template-driven View Builder:

```text
employee message
-> n8n warehouse workflow
-> detect view-template request
-> feishu-adapter template matcher
-> template + slots
-> schema validation
-> existing /warehouse/inventory-table/views/create
-> Feishu reply
```

The Agent may help classify ambiguous language later, but the Agent must not directly operate the Feishu API or freely generate final field/filter payloads.

## Components

### 1. View Template Catalog

Add a small template catalog under `services/feishu-adapter/app/view_templates/`.

Each template defines:

- `template_id`
- `display_name`
- employee-facing `aliases`
- source `table_name`
- default `visible_fields`
- default `filters`
- default `sorts`
- allowed `slots`

Example:

```json
{
  "template_id": "inventory_risk_view",
  "display_name": "Inventory Risk View",
  "aliases": ["high risk inventory", "risk inventory", "priority inventory"],
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
}
```

### 2. Template Matcher

Add a deterministic matcher in `feishu-adapter` first.

It should:

- normalize Chinese and English text
- match aliases such as `高风险库存`, `风险库存`, `缺货预警`, `库位异常`
- extract simple slots such as risk level, warehouse name/code, SKU, and inventory threshold
- return `matched=false` when confidence is low

This avoids a large brittle keyword tree. Most business variation lives in template aliases and slot definitions.

### 3. View Renderer

The renderer converts:

```json
{
  "template_id": "inventory_risk_view",
  "slots": {
    "risk_level": "high",
    "warehouse": "wh_hk_1"
  }
}
```

into the existing controlled create-view request:

```json
{
  "table_name": "Warehouse Inventory Snapshot",
  "view_name": "High Risk Inventory",
  "visible_fields": ["SKU", "Warehouse", "Available", "Risk Level", "Recommendation"],
  "filters": [
    {"field": "Risk Level", "operator": "is", "value": "high"},
    {"field": "Warehouse", "operator": "is", "value": "wh_hk_1"}
  ],
  "sorts": [{"field": "Available", "order": "asc"}]
}
```

Before calling Feishu, the renderer must validate all fields against the current inventory table schema.

### 4. API

Add:

```text
GET /warehouse/inventory-table/view-templates
POST /warehouse/inventory-table/views/from-template
```

`POST /views/from-template` request:

```json
{
  "message": "帮我建一个香港仓高风险库存视图",
  "view_name": "香港仓高风险库存"
}
```

Response:

```json
{
  "ok": true,
  "matched": true,
  "template_id": "inventory_risk_view",
  "slots": {
    "risk_level": "high",
    "warehouse": "wh_hk_1"
  },
  "view_id": "vew_xxx",
  "action": "created",
  "validated_plan": {
    "visible_fields": ["SKU", "Warehouse", "Available", "Risk Level", "Recommendation"],
    "filters": [
      {"field": "Risk Level", "operator": "is", "value": "high"}
    ],
    "sorts": [{"field": "Available", "order": "asc"}]
  }
}
```

If no template matches, return:

```json
{
  "ok": false,
  "matched": false,
  "error": "unknown_view_template",
  "message": "未匹配到视图模板。可尝试：高风险库存、缺货预警、仓储异常。"
}
```

### 5. n8n Workflow Change

Update `Warehouse Workflow`:

```text
Normalize Inbound Message
-> Detect Warehouse View Template Request
-> Create Warehouse View From Template
-> Format Warehouse View Template Reply
```

Unmatched or non-view requests still go to `Warehouse Agent`.

The n8n Code node only detects broad intent such as `创建/生成/做一个 + 视图/看板/表格`; it does not parse fields, filters, or sort rules.

## Initial Templates

Start with five templates:

- `inventory_risk_view`: inventory risk view, with `risk_level`, `warehouse`, and `available_lt` slots
- `low_stock_view`: low-stock or stockout warning view
- `warehouse_exception_view`: warehouse exception view
- `replenishment_candidate_view`: replenishment candidate view
- `fulfillment_block_view`: fulfillment-blocking inventory view

This is enough for a credible demo without creating a rigid template explosion.

## Employee-Facing Documentation

Add bilingual user docs with examples such as:

```text
You can say:
- Help me create a high-risk inventory view.
- Create a Hong Kong warehouse low-stock warning view.
- Generate a warehouse exception board for today.

You do not need to provide field names or filters.
```

The docs should describe what employees can say, not implementation details.

## Error Handling

- Unknown template: return suggested templates.
- Ambiguous template: ask a short clarification question.
- Unknown warehouse alias: ask the user to choose from known warehouses.
- Unknown field after schema validation: fail before calling Feishu.
- Feishu API failure: return a concise error and include run-log details for debugging.

## Testing

Add tests for:

- template catalog loading
- alias matching in Chinese and English
- slot extraction for risk level, warehouse, SKU, and threshold
- rendering `template + slots` into a validated view plan
- rejecting unknown fields before Feishu calls
- `POST /views/from-template` success and unknown-template responses
- n8n workflow structure: template fast path exists and only unmatched requests enter `Warehouse Agent`

## Success Criteria

An employee can send:

```text
帮我建一个香港仓高风险库存视图
```

and receive a successful Feishu reply without naming fields, writing filters, or knowing the API payload.

The implementation should keep the core rule: natural language may choose a template and fill slots, but the backend owns schema validation and final Feishu payload generation.
