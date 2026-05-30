from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from app.store import load_json
from app.warehouse_store import WAREHOUSE_COLUMN_COMMENTS

from .schemas import WarehouseInventorySearchRequest, WarehouseStockBalanceTableRowsRequest
from .state import (
    RECEIVED_INVENTORY_BATCHES,
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES,
    get_warehouse_repository,
)

router = APIRouter()


DEMO_TODAY = date(2026, 5, 24)

CATEGORY_ALIASES = {
    "paper": {"paper", "纸品", "纸巾", "抽纸"},
    "dairy": {"dairy", "乳制品", "牛奶", "酸奶", "奶制品"},
    "beverage": {"beverage", "饮料", "矿泉水", "可乐"},
    "daily_chemical": {"daily_chemical", "日化", "洗衣液"},
    "office_supply": {"office_supply", "办公耗材", "办公用品", "文具"},
}

WAREHOUSE_INVENTORY_TABLE_SCHEMA = [
    {
        "name": "Warehouse",
        "source": "warehouses.name",
        "type": "text",
        "comment": "仓库展示名称，例如深圳仓、香港仓。",
    },
    {
        "name": "Warehouse ID",
        "source": "warehouses.warehouse_id",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["warehouses"]["warehouse_id"],
    },
    {
        "name": "Location",
        "source": "storage_locations.location_code",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["storage_locations"]["location_code"],
    },
    {
        "name": "Category",
        "source": "categories.category_name",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["categories"]["category_name"],
    },
    {
        "name": "Category ID",
        "source": "categories.category_id",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["categories"]["category_id"],
    },
    {
        "name": "Item ID",
        "source": "items.item_id",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["items"]["item_id"],
    },
    {
        "name": "Item Name",
        "source": "items.item_name",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["items"]["item_name"],
    },
    {"name": "Brand", "source": "items.brand", "type": "text", "comment": WAREHOUSE_COLUMN_COMMENTS["items"]["brand"]},
    {"name": "Spec", "source": "items.spec", "type": "text", "comment": WAREHOUSE_COLUMN_COMMENTS["items"]["spec"]},
    {"name": "Unit", "source": "items.unit", "type": "text", "comment": WAREHOUSE_COLUMN_COMMENTS["items"]["unit"]},
    {
        "name": "Batch No",
        "source": "inventory_batches.batch_no",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_batches"]["batch_no"],
    },
    {
        "name": "Quantity On Hand",
        "source": "inventory_batches.quantity_on_hand",
        "type": "number",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_batches"]["quantity_on_hand"],
    },
    {
        "name": "Quantity Available",
        "source": "computed.quantity_available",
        "type": "number",
        "comment": "账面库存扣除预留库存后的可用数量。",
    },
    {
        "name": "Quantity Reserved",
        "source": "inventory_batches.quantity_reserved",
        "type": "number",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_batches"]["quantity_reserved"],
    },
    {
        "name": "Reorder Threshold",
        "source": "inventory_batches.reorder_threshold",
        "type": "number",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_batches"]["reorder_threshold"],
    },
    {
        "name": "Production Date",
        "source": "inventory_batches.production_date",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_batches"]["production_date"],
    },
    {
        "name": "Expiry Date",
        "source": "inventory_batches.expiry_date",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_batches"]["expiry_date"],
    },
    {
        "name": "Days To Expiry",
        "source": "computed.days_to_expiry",
        "type": "number",
        "comment": "以演示日期 2026-05-24 计算的剩余保质期天数。",
    },
    {
        "name": "Expiry Risk",
        "source": "computed.expiry_risk",
        "type": "single_select",
        "comment": "批次临期风险，expired 为已过期，expiring_soon 为 45 天内临期。",
        "options": [
            {"name": "normal", "color": 28},
            {"name": "expiring_soon", "color": 24},
            {"name": "expired", "color": 17},
        ],
    },
    {
        "name": "Risk Level",
        "source": "computed.risk_level",
        "type": "single_select",
        "comment": "根据可用数量、补货阈值、保质期和存储状态计算出的批次库存风险。",
        "options": [
            {"name": "low", "color": 28},
            {"name": "medium", "color": 24},
            {"name": "high", "color": 17},
            {"name": "unknown", "color": 0},
        ],
    },
    {
        "name": "Storage Status",
        "source": "inventory_batches.storage_status",
        "type": "single_select",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_batches"]["storage_status"],
        "options": [
            {"name": "available", "color": 28},
            {"name": "quality_hold", "color": 17},
        ],
    },
    {
        "name": "Recommendation",
        "source": "computed.recommendation",
        "type": "text",
        "comment": "根据批次风险生成的仓储处理建议。",
    },
    {
        "name": "Last Synced At",
        "source": "sync.last_synced_at",
        "type": "text",
        "comment": "同步到飞书多维表格的时间。",
    },
    {
        "name": "Sync Status",
        "source": "sync.status",
        "type": "single_select",
        "comment": "该行数据的同步状态。",
        "options": [
            {"name": "synced", "color": 28},
            {"name": "pending", "color": 24},
            {"name": "failed", "color": 17},
        ],
    },
    {
        "name": "Source Version",
        "source": "computed.source_version",
        "type": "text",
        "comment": "用于追踪该行批次库存快照来源的版本标识。",
    },
]

WAREHOUSE_STOCK_BALANCE_TABLE_SCHEMA = [
    {
        "name": "Balance Key",
        "source": "computed.balance_key",
        "type": "text",
        "comment": "库存余额行唯一键，格式为 item_id:warehouse_id:location_code。",
    },
    {"name": "Warehouse", "source": "warehouses.name", "type": "text", "comment": "仓库展示名称，例如深圳仓、香港仓。"},
    {
        "name": "Warehouse ID",
        "source": "warehouses.warehouse_id",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["warehouses"]["warehouse_id"],
    },
    {
        "name": "Location",
        "source": "inventory_location_balances.location_code",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_location_balances"]["location_code"],
    },
    {
        "name": "Category",
        "source": "categories.category_name",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["categories"]["category_name"],
    },
    {
        "name": "Category ID",
        "source": "categories.category_id",
        "type": "text",
        "comment": WAREHOUSE_COLUMN_COMMENTS["categories"]["category_id"],
    },
    {"name": "Item ID", "source": "items.item_id", "type": "text", "comment": WAREHOUSE_COLUMN_COMMENTS["items"]["item_id"]},
    {"name": "Item Name", "source": "items.item_name", "type": "text", "comment": WAREHOUSE_COLUMN_COMMENTS["items"]["item_name"]},
    {"name": "Brand", "source": "items.brand", "type": "text", "comment": WAREHOUSE_COLUMN_COMMENTS["items"]["brand"]},
    {"name": "Spec", "source": "items.spec", "type": "text", "comment": WAREHOUSE_COLUMN_COMMENTS["items"]["spec"]},
    {"name": "Unit", "source": "items.unit", "type": "text", "comment": WAREHOUSE_COLUMN_COMMENTS["items"]["unit"]},
    {
        "name": "Quantity On Hand",
        "source": "inventory_location_balances.quantity_on_hand",
        "type": "number",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_location_balances"]["quantity_on_hand"],
    },
    {
        "name": "Quantity Available",
        "source": "inventory_location_balances.quantity_on_hand",
        "type": "number",
        "comment": "当前可用库存余额；当前模型中等于 Quantity On Hand。",
    },
    {
        "name": "Reorder Threshold",
        "source": "inventory_location_balances.reorder_threshold",
        "type": "number",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_location_balances"]["reorder_threshold"],
    },
    {
        "name": "Storage Status",
        "source": "inventory_location_balances.storage_status",
        "type": "single_select",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_location_balances"]["storage_status"],
        "options": [{"name": "available", "color": 28}, {"name": "quality_hold", "color": 17}],
    },
    {
        "name": "Risk Level",
        "source": "computed.risk_level",
        "type": "single_select",
        "comment": "根据库存余额、补货阈值和存储状态计算出的余额风险。",
        "options": [
            {"name": "low", "color": 28},
            {"name": "medium", "color": 24},
            {"name": "high", "color": 17},
            {"name": "unknown", "color": 0},
        ],
    },
    {
        "name": "Balance Status",
        "source": "computed.balance_status",
        "type": "single_select",
        "comment": "库存余额状态，zero_stock 表示余额为 0，low_stock 表示低于补货阈值。",
        "options": [
            {"name": "available", "color": 28},
            {"name": "low_stock", "color": 24},
            {"name": "zero_stock", "color": 17},
            {"name": "quality_hold", "color": 17},
        ],
    },
    {
        "name": "Created At",
        "source": "inventory_location_balances.created_at",
        "type": "date",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_location_balances"]["created_at"],
    },
    {
        "name": "Updated At",
        "source": "inventory_location_balances.updated_at",
        "type": "date",
        "comment": WAREHOUSE_COLUMN_COMMENTS["inventory_location_balances"]["updated_at"],
    },
    {"name": "Last Synced At", "source": "sync.last_synced_at", "type": "date", "comment": "同步到飞书多维表格的时间。"},
    {
        "name": "Sync Status",
        "source": "sync.status",
        "type": "single_select",
        "comment": "该行数据的同步状态。",
        "options": [{"name": "synced", "color": 28}, {"name": "pending", "color": 24}, {"name": "failed", "color": 17}],
    },
    {"name": "Source Version", "source": "computed.source_version", "type": "text", "comment": "用于追踪该库存余额行来源的版本标识。"},
]


def normalize_category(value: str | None) -> str:
    raw = (value or "").strip().casefold()
    for category_id, aliases in CATEGORY_ALIASES.items():
        if raw == category_id or raw in {alias.casefold() for alias in aliases}:
            return category_id
    return raw


def inventory_batch_key(row: dict[str, Any]) -> str:
    return ":".join(
        [
            str(row["warehouse_id"]),
            str(row["location_code"]),
            str(row["item_id"]),
            str(row["batch_no"]),
        ]
    )


def apply_inventory_batch_override(row: dict[str, Any]) -> dict[str, Any]:
    override = WAREHOUSE_BATCH_QUANTITY_OVERRIDES.get(inventory_batch_key(row))
    if not override:
        return row
    return {**row, **override}


def load_batch_inventory_rows(
    *,
    item_id: str | None = None,
    warehouse_id: str | None = None,
    location_code: str | None = None,
    category_id: str | None = None,
    batch_no: str | None = None,
) -> list[dict[str, Any]]:
    repository = get_warehouse_repository()
    if repository:
        return repository.list_inventory_batches(
            item_id=item_id,
            warehouse_id=warehouse_id,
            location_code=location_code,
            category_id=category_id,
            batch_no=batch_no,
        )

    warehouse_by_id = {item["warehouse_id"]: item for item in load_json("warehouses.json")}
    location_by_key = {
        (item["warehouse_id"], item["location_code"]): item
        for item in load_json("storage_locations.json")
    }
    category_by_id = {item["category_id"]: item for item in load_json("categories.json")}
    item_by_id = {item["item_id"]: item for item in load_json("items.json")}
    rows: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(load_json("inventory_batches.json"), start=1):
        batch = {**batch, "batch_id": batch_index}
        item = item_by_id[str(batch["item_id"])]
        category = category_by_id[str(item["category_id"])]
        if item_id and batch["item_id"] != item_id:
            continue
        if warehouse_id and batch["warehouse_id"] != warehouse_id:
            continue
        if location_code and str(batch["location_code"]).casefold() != location_code.casefold():
            continue
        if category_id and category["category_id"] != category_id:
            continue
        if batch_no and batch["batch_no"] != batch_no:
            continue
        warehouse = warehouse_by_id[str(batch["warehouse_id"])]
        location = location_by_key[(str(batch["warehouse_id"]), str(batch["location_code"]))]
        rows.append({**batch, **warehouse, **location, **category, **item})
    for batch in RECEIVED_INVENTORY_BATCHES:
        item = item_by_id[str(batch["item_id"])]
        category = category_by_id[str(item["category_id"])]
        if item_id and batch["item_id"] != item_id:
            continue
        if warehouse_id and batch["warehouse_id"] != warehouse_id:
            continue
        if location_code and str(batch["location_code"]).casefold() != location_code.casefold():
            continue
        if category_id and category["category_id"] != category_id:
            continue
        if batch_no and batch["batch_no"] != batch_no:
            continue
        warehouse = warehouse_by_id[str(batch["warehouse_id"])]
        location = location_by_key[(str(batch["warehouse_id"]), str(batch["location_code"]))]
        rows.append({**batch, **warehouse, **location, **category, **item})
    return sorted(
        [apply_inventory_batch_override(row) for row in rows],
        key=lambda row: (row["warehouse_id"], row["location_code"], row["item_name"], row["batch_no"]),
    )


def days_to_expiry(row: dict[str, Any]) -> int:
    return (date.fromisoformat(str(row["expiry_date"])) - DEMO_TODAY).days


def expiry_risk(row: dict[str, Any]) -> str:
    days = days_to_expiry(row)
    if days < 0:
        return "expired"
    if days <= 45:
        return "expiring_soon"
    return "normal"


def quantity_available(row: dict[str, Any]) -> int:
    return max(int(row.get("quantity_on_hand", 0)), 0)


def batch_risk_level(row: dict[str, Any]) -> str:
    if row.get("storage_status") == "quality_hold" or expiry_risk(row) in {"expired", "expiring_soon"}:
        return "high"
    available = quantity_available(row)
    threshold = int(row.get("reorder_threshold", 0))
    if available < threshold:
        return "high"
    if available < threshold * 1.5:
        return "medium"
    return "low"


def batch_recommendation(row: dict[str, Any]) -> str:
    if row.get("storage_status") == "quality_hold":
        return "库存处于质检冻结状态，建议仓库复核后再放行。"
    if expiry_risk(row) == "expired":
        return "批次已过期，建议下架并按报损流程处理。"
    if expiry_risk(row) == "expiring_soon":
        return "批次临近保质期，建议优先出库或做促销处理。"
    if quantity_available(row) < int(row.get("reorder_threshold", 0)):
        return "可用库存低于补货阈值，建议通知采购或调拨。"
    return "库存状态正常，可继续履约。"


def aggregate_location_balances(
    *,
    item_id: str,
    warehouse_id: str,
) -> dict[str, Any]:
    rows = [enrich_batch_row(row) for row in load_batch_inventory_rows(item_id=item_id, warehouse_id=warehouse_id)]
    if not rows:
        raise HTTPException(status_code=404, detail="warehouse stock balance not found")

    risk_order = {"low": 1, "medium": 2, "high": 3}
    locations: dict[str, dict[str, Any]] = {}
    for row in rows:
        location = locations.setdefault(
            row["location_code"],
            {
                "item_id": item_id,
                "warehouse_id": warehouse_id,
                "warehouse_name": row["warehouse_name"],
                "location_code": row["location_code"],
                "quantity_on_hand": 0,
                "quantity_reserved": 0,
                "quantity_available": 0,
                "batch_count": 0,
                "earliest_expiry_date": row["expiry_date"],
                "risk_level": "low",
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        location["quantity_on_hand"] += int(row["quantity_on_hand"])
        location["quantity_reserved"] += int(row["quantity_reserved"])
        location["quantity_available"] += int(row["quantity_available"])
        location["batch_count"] += 1
        if row["expiry_date"] < location["earliest_expiry_date"]:
            location["earliest_expiry_date"] = row["expiry_date"]
        if risk_order[row["risk_level"]] > risk_order[location["risk_level"]]:
            location["risk_level"] = row["risk_level"]

    sorted_locations = sorted(locations.values(), key=lambda item: item["location_code"])
    return {
        "ok": True,
        "schema_id": "inventory_location_balances",
        "item_id": item_id,
        "warehouse_id": warehouse_id,
        "warehouse_name": rows[0]["warehouse_name"],
        "total_quantity_on_hand": sum(item["quantity_on_hand"] for item in sorted_locations),
        "total_quantity_reserved": sum(item["quantity_reserved"] for item in sorted_locations),
        "total_quantity_available": sum(item["quantity_available"] for item in sorted_locations),
        "locations": sorted_locations,
    }



def enrich_batch_row(row: dict[str, Any]) -> dict[str, Any]:
    row = {**row, "quantity_reserved": 0}
    available = quantity_available(row)
    enriched = {
        **row,
        "quantity_available": available,
        "days_to_expiry": days_to_expiry(row),
        "expiry_risk": expiry_risk(row),
        "risk_level": batch_risk_level(row),
    }
    enriched["recommendation"] = batch_recommendation(enriched)
    enriched["batch_key"] = (
        f"{enriched['warehouse_id']}:{enriched['location_code']}:"
        f"{enriched['item_id']}:{enriched['batch_no']}"
    )
    return enriched


def batch_inventory_table_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "Warehouse": row["warehouse_name"],
        "Warehouse ID": row["warehouse_id"],
        "Location": row["location_code"],
        "Category": row["category_name"],
        "Category ID": row["category_id"],
        "Item ID": row["item_id"],
        "Item Name": row["item_name"],
        "Brand": row["brand"],
        "Spec": row["spec"],
        "Unit": row["unit"],
        "Batch No": row["batch_no"],
        "Quantity On Hand": int(row["quantity_on_hand"]),
        "Quantity Available": int(row["quantity_available"]),
        "Quantity Reserved": int(row["quantity_reserved"]),
        "Reorder Threshold": int(row["reorder_threshold"]),
        "Production Date": row["production_date"],
        "Expiry Date": row["expiry_date"],
        "Days To Expiry": int(row["days_to_expiry"]),
        "Expiry Risk": row["expiry_risk"],
        "Risk Level": row["risk_level"],
        "Storage Status": row["storage_status"],
        "Recommendation": row["recommendation"],
        "Last Synced At": datetime.now(UTC).isoformat(),
        "Sync Status": "synced",
        "Source Version": f"mock-api:{row['batch_key']}",
    }


def balance_key(row: dict[str, Any]) -> str:
    return f"{row['item_id']}:{row['warehouse_id']}:{row['location_code']}"


def balance_status(row: dict[str, Any]) -> str:
    if row.get("storage_status") == "quality_hold":
        return "quality_hold"
    quantity = int(row.get("quantity_on_hand") or 0)
    if quantity <= 0:
        return "zero_stock"
    if quantity < int(row.get("reorder_threshold") or 0):
        return "low_stock"
    return "available"


def balance_risk_level(row: dict[str, Any]) -> str:
    status = balance_status(row)
    if status in {"quality_hold", "zero_stock", "low_stock"}:
        return "high"
    quantity = int(row.get("quantity_on_hand") or 0)
    threshold = int(row.get("reorder_threshold") or 0)
    if threshold > 0 and quantity < threshold * 1.5:
        return "medium"
    return "low"


def load_stock_balance_rows(
    *,
    item_id: str | None = None,
    warehouse_id: str | None = None,
    location_code: str | None = None,
) -> list[dict[str, Any]]:
    repository = get_warehouse_repository()
    if repository:
        return aggregate_stock_balance_snapshot_rows(
            repository.list_inventory_balance_snapshots(
                item_id=item_id,
                warehouse_id=warehouse_id,
                location_code=location_code,
            )
        )

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in load_batch_inventory_rows(
        item_id=item_id,
        warehouse_id=warehouse_id,
        location_code=location_code,
    ):
        key = (str(row["item_id"]), str(row["warehouse_id"]), str(row["location_code"]))
        existing = grouped.get(key)
        if existing:
            existing["quantity_on_hand"] = int(existing["quantity_on_hand"]) + int(row["quantity_on_hand"])
            existing["reorder_threshold"] = max(int(existing["reorder_threshold"]), int(row["reorder_threshold"]))
            if row.get("storage_status") == "quality_hold":
                existing["storage_status"] = "quality_hold"
            continue
        grouped[key] = {
            **row,
            "created_at": "2026-05-24T00:00:00+00:00",
            "updated_at": "2026-05-24T00:00:00+00:00",
        }
    return sorted(
        grouped.values(),
        key=lambda row: (row["warehouse_id"], row["location_code"], row["item_id"]),
    )


def aggregate_stock_balance_snapshot_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["item_id"]), str(row["warehouse_id"]), str(row["location_code"]))
        existing = grouped.get(key)
        if existing:
            existing["quantity_on_hand"] = int(existing.get("quantity_on_hand") or 0) + int(
                row.get("quantity_on_hand") or 0
            )
            existing["reorder_threshold"] = max(
                int(existing.get("reorder_threshold") or 0),
                int(row.get("reorder_threshold") or 0),
            )
            if row.get("storage_status") == "quality_hold":
                existing["storage_status"] = "quality_hold"
            created_at = str(row.get("created_at") or existing.get("created_at") or "")
            updated_at = str(row.get("updated_at") or existing.get("updated_at") or "")
            if created_at and created_at < str(existing.get("created_at") or created_at):
                existing["created_at"] = created_at
            if updated_at and updated_at > str(existing.get("updated_at") or updated_at):
                existing["updated_at"] = updated_at
            continue
        grouped[key] = {
            **row,
            "quantity_on_hand": int(row.get("quantity_on_hand") or 0),
            "reorder_threshold": int(row.get("reorder_threshold") or 0),
        }
    return sorted(
        grouped.values(),
        key=lambda row: (row["warehouse_id"], row["location_code"], row["item_id"]),
    )


def stock_balance_table_fields(row: dict[str, Any]) -> dict[str, Any]:
    key = balance_key(row)
    quantity = int(row["quantity_on_hand"])
    return {
        "Balance Key": key,
        "Warehouse": row["warehouse_name"],
        "Warehouse ID": row["warehouse_id"],
        "Location": row["location_code"],
        "Category": row["category_name"],
        "Category ID": row["category_id"],
        "Item ID": row["item_id"],
        "Item Name": row["item_name"],
        "Brand": row["brand"],
        "Spec": row["spec"],
        "Unit": row["unit"],
        "Quantity On Hand": quantity,
        "Quantity Available": quantity,
        "Reorder Threshold": int(row["reorder_threshold"]),
        "Storage Status": row["storage_status"],
        "Risk Level": balance_risk_level(row),
        "Balance Status": balance_status(row),
        "Created At": row["created_at"],
        "Updated At": row["updated_at"],
        "Last Synced At": datetime.now(UTC).isoformat(),
        "Sync Status": "synced",
        "Source Version": f"mock-api:{key}:{row['updated_at']}",
    }


def apply_cursor_page(rows: list[dict[str, Any]], *, cursor: str | None, limit: int) -> tuple[list[dict[str, Any]], str]:
    start = 0
    if cursor:
        try:
            start = max(int(cursor), 0)
        except ValueError:
            start = 0
    end = start + limit
    return rows[start:end], (str(end) if end < len(rows) else "")


@router.get("/warehouse/stock/balances/table-schema")
def get_warehouse_stock_balance_table_schema() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_id": "warehouse_inventory_balances",
        "source": "mock-api",
        "fields": WAREHOUSE_STOCK_BALANCE_TABLE_SCHEMA,
    }


@router.post("/warehouse/stock/balances/table-rows")
def get_warehouse_stock_balance_table_rows(payload: WarehouseStockBalanceTableRowsRequest) -> dict[str, Any]:
    rows = load_stock_balance_rows(
        item_id=(payload.item_id or "").strip() or None,
        warehouse_id=(payload.warehouse_id or "").strip() or None,
        location_code=(payload.location_code or "").strip() or None,
    )
    limit = max(min(int(payload.limit or 500), 500), 1)
    page, next_cursor = apply_cursor_page(rows, cursor=payload.cursor, limit=limit)
    return {
        "ok": True,
        "schema_id": "warehouse_inventory_balances",
        "count": len(page),
        "next_cursor": next_cursor,
        "items": [
            {
                "balance_key": balance_key(row),
                "item_id": row["item_id"],
                "warehouse_id": row["warehouse_id"],
                "location_code": row["location_code"],
                "fields": stock_balance_table_fields(row),
            }
            for row in page
        ],
    }



@router.get("/warehouse/inventory/table-schema")
def get_warehouse_inventory_table_schema() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_id": "warehouse_batch_inventory",
        "source": "mock-api",
        "fields": WAREHOUSE_INVENTORY_TABLE_SCHEMA,
    }


@router.post("/warehouse/inventory/search")
def search_warehouse_inventory(payload: WarehouseInventorySearchRequest) -> dict:
    item_id = (payload.item_id or payload.sku or "").strip()
    warehouse_id = (payload.warehouse_id or "").strip()
    location_code = (payload.location_code or "").strip()
    category_id = normalize_category(payload.category_id or payload.category)
    batch_no = (payload.batch_no or "").strip()
    expiry_risk_filter = (payload.expiry_risk or "").strip()
    risk_level = (payload.risk_level or "").strip()
    limit = max(min(int(payload.limit or 50), 100), 1)
    rows = [
        enrich_batch_row(row)
        for row in load_batch_inventory_rows(
            item_id=item_id or None,
            warehouse_id=warehouse_id or None,
            location_code=location_code or None,
            category_id=category_id or None,
            batch_no=batch_no or None,
        )
    ]
    matches: list[dict[str, Any]] = []
    for row in rows:
        if expiry_risk_filter and row["expiry_risk"] != expiry_risk_filter:
            continue
        if risk_level and row["risk_level"] != risk_level:
            continue
        matches.append(row)
        if len(matches) >= limit:
            break
    return {
        "ok": True,
        "schema_id": "warehouse_batch_inventory",
        "item_id": item_id or None,
        "warehouse_id": warehouse_id or None,
        "location_code": location_code or None,
        "category_id": category_id or None,
        "expiry_risk": expiry_risk_filter or None,
        "risk_level": risk_level or None,
        "count": len(matches),
        "items": matches,
    }


@router.post("/warehouse/inventory/table-rows")
def get_warehouse_inventory_table_rows(payload: WarehouseInventorySearchRequest) -> dict[str, Any]:
    search_result = search_warehouse_inventory(payload)
    return {
        "ok": True,
        "schema_id": "warehouse_batch_inventory",
        "count": search_result["count"],
        "items": [
            {
                "batch_key": item["batch_key"],
                "item_id": item["item_id"],
                "batch_no": item["batch_no"],
                "fields": batch_inventory_table_fields(item),
            }
            for item in search_result["items"]
        ],
    }


@router.get("/warehouse/inventory/{item_id}")
def get_warehouse_inventory(item_id: str) -> dict:
    rows = [enrich_batch_row(row) for row in load_batch_inventory_rows(item_id=item_id)]
    if not rows:
        raise HTTPException(status_code=404, detail="inventory not found")
    total_on_hand = sum(int(row["quantity_on_hand"]) for row in rows)
    total_reserved = sum(int(row["quantity_reserved"]) for row in rows)
    total_available = sum(int(row["quantity_available"]) for row in rows)
    risk_order = {"high": 3, "medium": 2, "low": 1}
    risk_level = max((row["risk_level"] for row in rows), key=lambda value: risk_order[value])
    first = rows[0]
    return {
        "ok": True,
        "item_id": item_id,
        "item_name": first["item_name"],
        "category_id": first["category_id"],
        "category_name": first["category_name"],
        "total_quantity_on_hand": total_on_hand,
        "total_quantity_reserved": total_reserved,
        "total_quantity_available": total_available,
        "risk_level": risk_level,
        "recommendation": batch_recommendation(max(rows, key=lambda row: risk_order[row["risk_level"]])),
        "batches": rows,
    }


@router.post("/warehouse/exceptions/search")
def search_warehouse_exceptions(payload: dict) -> dict:
    item_id = str(payload.get("item_id") or payload.get("sku") or "").strip()
    expiry_risk_filter = str(payload.get("expiry_risk") or "").strip()
    if not item_id:
        return {"ok": False, "error": "missing_item_id", "matches": []}
    rows = [enrich_batch_row(row) for row in load_batch_inventory_rows(item_id=item_id)]
    matches = [
        row
        for row in rows
        if row["risk_level"] == "high"
        and (not expiry_risk_filter or row["expiry_risk"] == expiry_risk_filter)
    ]
    return {
        "ok": True,
        "item_id": item_id,
        "expiry_risk": expiry_risk_filter or None,
        "matches": matches,
    }


@router.post("/warehouse/fulfillment/check")
def check_warehouse_fulfillment(payload: dict) -> dict:
    item_id = str(payload.get("item_id") or payload.get("sku") or "").strip()
    if not item_id:
        return {
            "ok": False,
            "error": "missing_item_id",
            "can_ship": False,
            "blockers": ["missing_item_id"],
        }

    rows = [enrich_batch_row(row) for row in load_batch_inventory_rows(item_id=item_id)]
    if not rows:
        return {
            "ok": False,
            "error": "inventory_not_found",
            "item_id": item_id,
            "can_ship": False,
            "blockers": ["inventory_not_found"],
        }

    available = sum(int(row["quantity_available"]) for row in rows)
    reserved = sum(int(row["quantity_reserved"]) for row in rows)
    blockers: list[str] = []
    if any(row["quantity_available"] < row["reorder_threshold"] for row in rows):
        blockers.append("insufficient_available_stock")
    if not any(row["storage_status"] == "available" and int(row["quantity_available"]) > 0 for row in rows):
        blockers.append("missing_available_location")
    if any(row["storage_status"] == "quality_hold" for row in rows):
        blockers.append("quality_hold")
    if any(row["expiry_risk"] in {"expired", "expiring_soon"} for row in rows):
        blockers.append("expiry_risk")

    can_ship = not blockers
    next_action = (
        "release_to_pick"
        if can_ship
        else ("notify_procurement" if "insufficient_available_stock" in blockers else "manual_review")
    )
    return {
        "ok": True,
        "item_id": item_id,
        "can_ship": can_ship,
        "blockers": blockers,
        "available": available,
        "reserved": reserved,
        "batches": rows,
        "next_action": next_action,
    }


@router.get("/warehouse/stock/balances")
def get_warehouse_stock_balances(item_id: str, warehouse_id: str) -> dict[str, Any]:
    return aggregate_location_balances(
        item_id=item_id.strip(),
        warehouse_id=warehouse_id.strip(),
    )

