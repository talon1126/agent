# Warehouse Inventory Feishu Table Provision and Sync

Warehouse inventory table provisioning creates a fixed-schema Feishu table inside an existing Bitable app/base. Warehouse inventory table sync then publishes an SKU inventory snapshot to that table when a warehouse user explicitly asks for a table, export, sync, or dashboard view.

## Design

The Feishu table is a read model, not the inventory source of truth.

```text
mock-api / future warehouse system
        -> feishu-adapter /warehouse/inventory-table/provision
        -> feishu-adapter /warehouse/inventory-table/sync
        -> Feishu table upsert
        -> Warehouse Agent reply
```

Inventory facts stay in `mock-api` or a future warehouse system. The Feishu table gives employees a collaborative snapshot with risk and recommendations.

## Provision Behavior

The Warehouse Agent has a tool named `warehouse_inventory_table_provision_tool`. It should call this tool only when the user explicitly asks to create, initialize, configure, or provision the warehouse inventory Feishu table.

The provision endpoint:

- Requires `FEISHU_INVENTORY_TABLE_APP_ID`, `FEISHU_INVENTORY_TABLE_APP_SECRET`, and `FEISHU_INVENTORY_TABLE_APP_TOKEN`.
- Creates a data table in the configured Bitable app/base.
- Adds the fixed inventory snapshot fields listed below.
- Returns `action=existing` without calling Feishu when `FEISHU_INVENTORY_TABLE_ID` is already configured.
- Writes a run log when `FEISHU_RUN_LOG_URL` is configured.
- Does not create a new Feishu app/base or a source-of-truth inventory database.

## Sync Behavior

The Warehouse Agent has a tool named `warehouse_inventory_table_sync_tool`. It should call this tool only when the user asks to sync, export, publish, show a Feishu table, or create a dashboard-style snapshot.

The sync endpoint:

- Fetches `GET /warehouse/inventory/{sku}` from `mock-api`.
- Builds a normalized table row.
- Looks up an existing Feishu table record by `SKU + Warehouse`.
- Updates the existing record or creates a new one.
- Writes a run log when `FEISHU_RUN_LOG_URL` is configured.
- Returns a safe error if table credentials are missing.

## Table Fields

Create these Feishu table fields:

```text
SKU
Product Name
Warehouse
Available
Reserved
Pending Orders
Risk Level
Open Exception Count
Recommendation
Last Synced At
Sync Status
Source Version
```

`Source Version` is a lightweight idempotency marker such as `mock-api:sku_bag_1:wh_hk_1`.

## Required Environment

```env
FEISHU_INVENTORY_TABLE_APP_ID=
FEISHU_INVENTORY_TABLE_APP_SECRET=
FEISHU_INVENTORY_TABLE_APP_TOKEN=
FEISHU_INVENTORY_TABLE_ID=
FEISHU_INVENTORY_TABLE_VIEW_ID=
FEISHU_INVENTORY_TABLE_URL=
```

For provisioning, `FEISHU_INVENTORY_TABLE_ID` is optional. After a successful create, copy the returned `table_id` into `.env` as `FEISHU_INVENTORY_TABLE_ID` so future sync calls update that table.

`FEISHU_INVENTORY_TABLE_VIEW_ID` and `FEISHU_INVENTORY_TABLE_URL` are optional. The URL is only returned to users as a convenient link.

## Manual Smoke Test

Provision:

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/provision `
  -ContentType "application/json" `
  -Body '{"table_name":"Warehouse Inventory Snapshot"}' | ConvertTo-Json -Depth 10
```

Sync:

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/sync `
  -ContentType "application/json" `
  -Body '{"sku":"sku_bag_1"}' | ConvertTo-Json -Depth 10
```

Expected behavior:

- Missing provision config returns `ok=false` and `missing_feishu_inventory_table_provision_config`.
- Existing `FEISHU_INVENTORY_TABLE_ID` returns `ok=true` and `action=existing`.
- Successful provisioning returns `ok=true`, `action=created`, `table_id`, and the fixed field list.
- Missing sync config returns `ok=false` and `missing_feishu_inventory_table_config`.
- Valid config returns `ok=true`, `action=created` or `action=updated`, and a `record_id`.
