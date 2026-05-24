# Warehouse View Template Builder

Warehouse employees can create Feishu inventory views with plain business language. They do not need to know field names, filter syntax, sort rules, or API payloads.

The current warehouse model is batch + location inventory. A view can filter by warehouse, location, category, expiry state, risk level, or available quantity.

## Natural Language Examples

```text
@Warehouse Help me create a Shenzhen warehouse paper inventory view.
@Warehouse Help me create a Hong Kong dairy expiring inventory view.
@Warehouse Help me create a Shenzhen A1 location inventory view.
@Warehouse Help me create a milk warning view for stock below 20.
@Warehouse Help me create a high-risk batch view.
```

## Runtime Flow

```text
Feishu message
-> feishu-adapter
-> n8n Warehouse Workflow
-> Warehouse Intent Router
-> Create Warehouse View From Template
-> POST /warehouse/inventory-table/views/from-template
-> Feishu Bitable view create or reuse
-> Format Warehouse View Template Reply
```

If the backend cannot match a supported template, the workflow restores the original message and sends it to `Warehouse Agent` as a fallback.

## Live Templates

| Template ID | Business Use | Example |
| --- | --- | --- |
| `category_inventory_view` | Inventory by category | Shenzhen paper inventory |
| `low_stock_view` | Low-stock warning | stock below 20 milk warning |
| `expiring_inventory_view` | Expiring or expired batches | Hong Kong dairy expiring inventory |
| `location_inventory_view` | Inventory by warehouse location | Shenzhen A1 location inventory |
| `batch_risk_view` | High-risk inventory batches | high-risk batch view |
| `replenishment_candidate_view` | Restock candidates | replenishment candidate view |

## Controlled Fields

Templates use fields from the batch + location read model:

```text
Warehouse
Warehouse ID
Location
Category
Category ID
Item ID
Item Name
Brand
Spec
Unit
Batch No
Quantity On Hand
Quantity Available
Quantity Reserved
Reorder Threshold
Production Date
Expiry Date
Days To Expiry
Expiry Risk
Risk Level
Storage Status
Recommendation
Last Synced At
Sync Status
Source Version
```

The backend validates all visible fields, filter fields, and sort fields before calling Feishu.

## Smoke Tests

Template match without writing to Feishu:

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/intents/route `
  -ContentType "application/json" `
  -Body '{"message":"帮我建一个深圳仓纸品库存视图"}' | ConvertTo-Json -Depth 10
```

Create or reuse a Feishu view:

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/views/from-template `
  -ContentType "application/json" `
  -Body '{"message":"帮我建一个香港仓乳制品临期库存视图"}' | ConvertTo-Json -Depth 10
```

Expected result:

- `matched=true`
- `template_id` is one of the live templates above
- `validated_plan.visible_fields` contains batch + location fields
- `validated_plan.filters` contains business filters such as `Warehouse ID`, `Category ID`, `Location`, `Expiry Risk`, `Risk Level`, or `Quantity Available`
- Unknown templates return `ok=false`, `matched=false`, and suggestions
