# Warehouse Agent Design

Date: 2026-05-21

## Goal

Upgrade `Warehouse Agent` from a simple inventory lookup specialist into a realistic warehouse operations assistant for the ecommerce multi-agent workflow.

The first production-shaped version should still be a local demo. It should use deterministic mock data and narrow tools, but it should model the kinds of questions warehouse and inventory teams actually handle: stock availability, location control, fulfillment risk, receiving/putaway issues, picking/packing delays, damaged goods, inventory mismatches, and cycle-count follow-up.

## Research Summary

Warehouse and inventory work is not limited to checking a quantity. Common operational issues include:

- inaccurate inventory data caused by receiving mistakes, missed scans, unlogged transfers, picking errors, damaged goods, and returns not being processed
- stockouts, overstocks, deadstock, and low-stock replenishment signals
- warehouse space and location problems, including items being in the system but not physically found
- receiving, putaway, replenishment, picking, packing, staging, shipping, and returns workflows
- cycle counting and discrepancy investigation
- fulfillment delays caused by insufficient available stock, unresolved exceptions, or picking/packing bottlenecks

Sources used during design:

- TechTarget inventory management problems
- ShipBob warehouse management challenges
- Kardex order picking best practices
- Inventory clerk job-duty examples covering receiving, issuing, storing, shipping, inventorying, and discrepancy handling

## Current System Context

The current project already has:

- `Warehouse Agent` in `n8n/workflows/chat-parent-son-agent.json`
- `inventory_status_tool` calling `GET /inventory/{sku}`
- `fixtures/data/inventory.json` with `available`, `pending_orders`, and `reorder_threshold`
- `mock-api` as the right boundary for enterprise-system simulation
- `Procurement Agent`, `Customer Support Agent`, and `Operations Agent` as adjacent specialists

This design extends the warehouse domain without replacing the parent/son n8n architecture.

## Responsibilities

`Warehouse Agent` should own four categories of work.

### 1. Inventory Availability

Questions:

- "sku_bag_1 还有多少库存？"
- "这个 SKU 会不会缺货？"
- "可用库存能不能覆盖待发订单？"

The agent should answer using stock fields, not guesses.

Required output:

- SKU
- available stock
- reserved stock
- pending orders
- reorder threshold
- risk level
- recommendation

### 2. Location and Warehouse State

Questions:

- "这个 SKU 在哪个库位？"
- "哪个仓库还有货？"
- "系统有库存但仓库找不到货，怎么办？"

Required output:

- warehouse ID/name
- zone
- bin/location
- quantity by location
- location status such as available, reserved, damaged, hold, or pending_putaway
- whether location data is trustworthy

### 3. Fulfillment and Shipping Risk

Questions:

- "这个订单能不能今天发？"
- "sku_bag_1 会不会影响发货？"
- "哪些 SKU 有履约风险？"

Required output:

- can_ship boolean
- blocker list
- stock coverage calculation
- suggested next action
- whether to involve Customer Support or Procurement

### 4. Warehouse Exceptions

Questions:

- "这个 SKU 有异常吗？"
- "库存差异怎么处理？"
- "今天仓储异常有哪些？"

Exception types for the first version:

- `stock_mismatch`
- `damaged_goods`
- `missing_location`
- `pending_putaway`
- `picking_delay`

Required output:

- exception ID
- SKU
- type
- severity
- status
- location
- recommended action

## Agent Boundaries

`Warehouse Agent` should stay focused on internal warehouse facts and fulfillment risk.

- Route customer-facing refund, complaint, and policy questions to `Customer Support Agent`.
- Route purchase order, supplier, replenishment ownership, and lead-time questions to `Procurement Agent`.
- Route daily summaries, cross-domain incident summaries, and metric reporting to `Operations Agent`.
- If a question starts in the warehouse domain but needs procurement, the agent can say "库存存在补货风险，建议交给 Procurement Agent 处理采购动作" rather than creating a purchase order itself.

## Tools

### `warehouse_inventory_tool`

Replaces the narrow `inventory_status_tool`.

Backend endpoint:

`GET /warehouse/inventory/{sku}`

Returns:

```json
{
  "ok": true,
  "sku": "sku_bag_1",
  "available": 5,
  "reserved": 3,
  "pending_orders": 9,
  "reorder_threshold": 15,
  "locations": [
    {
      "warehouse_id": "wh_hk_1",
      "zone": "A",
      "bin": "A-01-03",
      "quantity": 5,
      "status": "available"
    }
  ],
  "risk_level": "high",
  "recommendation": "库存不足以覆盖待处理订单，建议通知采购并关注发货风险。"
}
```

### `warehouse_exception_tool`

Backend endpoint:

`POST /warehouse/exceptions/search`

Input:

```json
{
  "sku": "sku_bag_1",
  "status": "open"
}
```

Returns exception records with type, severity, location, status, and recommended action.

### `warehouse_fulfillment_tool`

Backend endpoint:

`POST /warehouse/fulfillment/check`

Input may include `sku` or `order_id`.

Returns:

- whether the item/order can ship
- blockers such as insufficient_stock, open_exception, missing_location, or pending_putaway
- next action such as release_to_pick, cycle_count_required, notify_procurement, or manual_review

## Mock Data

Create or extend fixtures:

- `fixtures/data/inventory.json`
- `fixtures/data/warehouse_locations.json`
- `fixtures/data/warehouse_exceptions.json`

Keep data small and inspectable.

Minimum scenarios:

- `sku_bottle_1`: healthy inventory, low risk
- `sku_bag_1`: low stock, open stock mismatch or picking risk
- `sku_lamp_1`: pending putaway or damaged-goods risk

## n8n Workflow Changes

Update `Warehouse Agent` prompt:

- mention all three warehouse tools
- require tool use for SKU, location, exception, or fulfillment questions
- prohibit inventing warehouse data
- keep Chinese replies suitable for Feishu

Update parent routing:

- inventory, SKU, warehouse, stock, picking, packing, putaway, fulfillment, cycle count, location, and damaged-goods questions route to `Warehouse Agent`
- replenishment ownership and purchase action route to `Procurement Agent`
- customer-facing complaint/refund route to `Customer Support Agent`

Update tool nodes:

- rename `inventory_status_tool` to `warehouse_inventory_tool`
- add `warehouse_exception_tool`
- add `warehouse_fulfillment_tool`

## Testing

Mock API tests:

- `GET /warehouse/inventory/sku_bag_1` returns location and high risk
- `POST /warehouse/exceptions/search` returns open exceptions for `sku_bag_1`
- `POST /warehouse/fulfillment/check` returns cannot ship when stock or exception blocks fulfillment
- healthy SKU returns can ship

Workflow tests:

- `Warehouse Agent` prompt mentions all warehouse tools
- old `inventory_status_tool` is not present
- new tool nodes connect to `Warehouse Agent`
- parent prompt routes warehouse terms to `Warehouse Agent`
- procurement terms remain routed to `Procurement Agent`

Smoke tests:

- `sku_bag_1 还有多少库存`
- `sku_bag_1 在哪个库位`
- `sku_bag_1 有仓储异常吗`
- `sku_bag_1 今天能发货吗`

The smoke tests may trigger Qwen, so they should be optional and run only after the user agrees to spend model quota.

## Non-goals

- No real WMS integration yet.
- No write actions such as creating pick tasks, moving stock, closing exceptions, or posting inventory adjustments.
- No barcode/RFID implementation.
- No advanced optimization such as route planning or wave picking.
- No autonomous procurement action.

## Recommended First Implementation

Implement this as a focused warehouse-readiness upgrade:

1. Add warehouse fixtures and mock-api endpoints.
2. Replace `inventory_status_tool` with `warehouse_inventory_tool`.
3. Add exception and fulfillment tools.
4. Update prompts and tests.
5. Import/publish n8n only after structure tests pass.
