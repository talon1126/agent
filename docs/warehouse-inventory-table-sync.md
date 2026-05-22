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
- Adds the fixed inventory snapshot fields listed below, including colored single-select fields for `Risk Level` and `Sync Status`.
- Returns `action=existing` without calling Feishu when `FEISHU_INVENTORY_TABLE_ID` is already configured.
- Reuses an existing table with the same name when Feishu returns `TableNameDuplicated`, then creates any missing fields. This recovers from a partially provisioned table.
- Upgrades existing `Risk Level` and `Sync Status` text fields to colored single-select fields when Feishu returns their field IDs.
- Writes a run log when `FEISHU_RUN_LOG_URL` is configured.
- Does not create a new Feishu app/base or a source-of-truth inventory database.

## Sync Behavior

The Warehouse Agent has a tool named `warehouse_inventory_table_sync_tool`. It should call this tool only when the user asks to sync, export, publish, show a Feishu table, or create a dashboard-style snapshot.

The sync endpoint:

- Fetches `GET /warehouse/inventory/{sku}` from `mock-api`.
- Auto-provisions or reuses the inventory table when `FEISHU_INVENTORY_TABLE_ID` is not configured.
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
Risk Level        # single select: low=green, medium=yellow, high=red, unknown=gray
Open Exception Count
Recommendation
Last Synced At
Sync Status       # single select: synced=green, pending=yellow, failed=red
Source Version
```

`Source Version` is a lightweight idempotency marker such as `mock-api:sku_bag_1:wh_hk_1`.

If an older table already shows white/plain text values in `Risk Level` or `Sync Status`, call the provision endpoint again after deploying this version. The adapter will inspect the existing fields and update those two fields to colored single-select fields.

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

For long-running demos, copying the returned `table_id` into `.env` is still useful because it survives container restarts and avoids a table-name lookup.

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
- Successful provisioning returns `ok=true`, `action=created` or `action=existing`, `table_id`, and the fixed field list.
- Missing sync config returns `ok=false` and `missing_feishu_inventory_table_config`.
- Valid sync config returns `ok=true`, auto-creates or reuses the table when needed, then returns `action=created` or `action=updated` plus a `record_id`.
