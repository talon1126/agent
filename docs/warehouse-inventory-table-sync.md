# Warehouse Inventory Feishu Table Provision and Sync

Warehouse inventory table provisioning creates a fixed-schema Feishu table inside an existing Bitable app/base. Warehouse inventory table sync publishes batch + location inventory snapshots when a warehouse user explicitly asks for a table, export, sync, or dashboard view.

The Feishu table is a read model, not the inventory source of truth.

## Design

```text
mock-api / future warehouse system
        -> feishu-adapter /warehouse/inventory-table/provision
        -> feishu-adapter /warehouse/inventory-table/sync
        -> feishu-adapter /warehouse/inventory-table/sync/filter
        -> feishu-adapter /warehouse/inventory-table/schema
        -> feishu-adapter /warehouse/inventory-table/views/from-template
        -> Feishu table upsert or Feishu view create
        -> Warehouse workflow reply
```

Inventory facts stay in `mock-api` or a future warehouse system. Feishu gives employees a collaborative snapshot with risk, expiry state, locations, and recommendations.

## Data Model

The current warehouse inventory model is batch + location based:

- `warehouses`: warehouse identity, for example Shenzhen, Hong Kong, Singapore
- `storage_locations`: specific positions such as A1, B1, C1
- `categories`: business categories such as paper, dairy, beverage, daily chemical, office supply
- `items`: item master data, including brand, spec, and unit
- `inventory_batches`: quantity, reservation, production date, expiry date, risk level, and recommendation by item, warehouse, location, and batch

The idempotent record identity for Feishu sync is:

```text
Warehouse ID + Location + Item ID + Batch No
```

## Provision Behavior

The Warehouse Agent has a tool named `warehouse_inventory_table_provision_tool`. It should call this tool only when the user explicitly asks to create, initialize, configure, or provision the warehouse inventory Feishu table.

The provision endpoint:

- Requires `FEISHU_INVENTORY_TABLE_APP_ID`, `FEISHU_INVENTORY_TABLE_APP_SECRET`, and `FEISHU_INVENTORY_TABLE_APP_TOKEN`.
- Creates a data table in the configured Bitable app/base.
- Adds fixed batch + location fields, including colored single-select fields for `Risk Level`, `Expiry Risk`, `Storage Status`, and `Sync Status`.
- Returns `action=existing` without calling Feishu when `FEISHU_INVENTORY_TABLE_ID` is already configured.
- Reuses an existing table with the same name when Feishu returns `TableNameDuplicated`, then creates any missing fields.
- Writes a run log when `FEISHU_RUN_LOG_URL` is configured.
- Does not create a new Feishu app/base or a source-of-truth inventory database.

## Sync Behavior

There are two sync paths:

- `/warehouse/inventory-table/sync` syncs a specific `item_id`, for example `item_vinda_tissue`.
- `/warehouse/inventory-table/sync/filter` syncs a filtered set of records by warehouse, location, category, risk level, expiry state, or batch number.

The sync endpoints:

- Fetch rows from `mock-api /warehouse/inventory/table-rows`.
- Auto-provision or reuse the inventory table when `FEISHU_INVENTORY_TABLE_ID` is not configured.
- Build Feishu rows with batch + location fields.
- Look up existing Feishu records by `Warehouse ID + Location + Item ID + Batch No`.
- Update existing records or create new records.
- Return a safe error if table credentials are missing.

## View Creation Behavior

Natural-language view creation should use:

- `POST /warehouse/inventory-table/views/from-template` for employee-friendly requests.
- `POST /warehouse/inventory-table/views/create` only for controlled JSON plans.

Example user request:

```text
@Warehouse 帮我建一个香港仓乳制品临期库存视图
```

Expected template plan:

```json
{
  "template_id": "expiring_inventory_view",
  "view_name": "香港仓乳制品临期库存",
  "visible_fields": ["Warehouse", "Location", "Category", "Item Name", "Batch No", "Expiry Date", "Days To Expiry", "Expiry Risk", "Quantity Available", "Recommendation"],
  "filters": [
    {"field": "Warehouse ID", "operator": "is", "value": "wh_hk_1"},
    {"field": "Category ID", "operator": "is", "value": "dairy"},
    {"field": "Expiry Risk", "operator": "is", "value": "expiring_soon"}
  ],
  "sorts": [{"field": "Days To Expiry", "order": "asc"}]
}
```

## Table Fields

Create these Feishu table fields:

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

`Source Version` is a lightweight idempotency marker such as `mock-api:wh_sz_1:A1:item_vinda_tissue:BATCH-20260501`.

## Required Environment

```env
FEISHU_INVENTORY_TABLE_APP_ID=
FEISHU_INVENTORY_TABLE_APP_SECRET=
FEISHU_INVENTORY_TABLE_APP_TOKEN=
FEISHU_INVENTORY_TABLE_ID=
FEISHU_INVENTORY_TABLE_VIEW_ID=
FEISHU_INVENTORY_TABLE_URL=
```

`FEISHU_INVENTORY_TABLE_ID` is optional. If it is configured, provision and sync use that table. If it is empty, the backend looks for `Warehouse Inventory Snapshot` by name, reuses it when found, or creates it when missing. The adapter remembers the resolved `table_id` for the current process, so sync can work without manual `.env` editing.

## Manual Smoke Tests

Provision:

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/provision `
  -ContentType "application/json" `
  -Body '{"table_name":"Warehouse Inventory Snapshot"}' | ConvertTo-Json -Depth 10
```

Sync one item:

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/sync `
  -ContentType "application/json" `
  -Body '{"item_id":"item_vinda_tissue"}' | ConvertTo-Json -Depth 10
```

Sync a filtered warehouse/category/location scope:

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/sync/filter `
  -ContentType "application/json" `
  -Body '{"warehouse_id":"wh_sz_1","location_code":"A1","category":"dairy","expiry_risk":"expiring_soon","limit":50}' | ConvertTo-Json -Depth 10
```

Create or reuse a view from a template:

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/views/from-template `
  -ContentType "application/json" `
  -Body '{"message":"帮我建一个深圳仓A1库位库存视图"}' | ConvertTo-Json -Depth 10
```

Expected behavior:

- Missing provision config returns `ok=false` and `missing_feishu_inventory_table_provision_config`.
- Existing `FEISHU_INVENTORY_TABLE_ID` returns `ok=true` and `action=existing`.
- Successful provisioning returns `ok=true`, `action=created` or `action=existing`, `table_id`, and the fixed field list.
- Valid sync config returns `ok=true`, `synced_count`, and per-record `item_id`, `batch_no`, `warehouse_id`, `location_code`, `risk_level`, `action`, and `record_id`.
- Schema returns `ok=true`, `table_id`, `fields`, and `views`.
- Template view creation returns `ok=true`, `action=created` or `action=existing`, `view_id`, and `validated_plan`.
- Unknown fields return `ok=false`, `invalid_inventory_view_plan`, `missing_fields`, and no Feishu view creation call.
