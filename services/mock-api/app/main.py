import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.store import FIXTURE_DIR, find_by_id, load_json
from app.warehouse_store import (
    WAREHOUSE_COLUMN_COMMENTS,
    WarehouseRepository,
    create_warehouse_repository_from_env,
)

app = FastAPI(title="Ecommerce Mock Enterprise API")

APPROVALS: list[dict] = []
TICKETS: list[dict] = []
RUN_LOGS: list[dict] = []
DEAD_LETTERS: list[dict] = []
REPLAYS: list[dict] = []
INTERNAL_NOTIFICATIONS: list[dict] = []
REPLENISHMENT_REQUESTS: list[dict] = []
WAREHOUSE_REPOSITORY: WarehouseRepository | None | bool = False


def get_warehouse_repository() -> WarehouseRepository | None:
    global WAREHOUSE_REPOSITORY
    if WAREHOUSE_REPOSITORY is False:
        WAREHOUSE_REPOSITORY = create_warehouse_repository_from_env(FIXTURE_DIR)
    return WAREHOUSE_REPOSITORY if isinstance(WAREHOUSE_REPOSITORY, WarehouseRepository) else None


class ApprovalRequest(BaseModel):
    event_id: str
    recommended_action: str
    explanation: str


class WarehouseInventorySearchRequest(BaseModel):
    item_id: str | None = None
    sku: str | None = None
    warehouse_id: str | None = None
    location_code: str | None = None
    category: str | None = None
    category_id: str | None = None
    batch_no: str | None = None
    expiry_risk: str | None = None
    risk_level: str | None = None
    limit: int = 50


class ReplenishmentRequestCreate(BaseModel):
    source: str = "warehouse"
    warehouse_id: str
    location_code: str | None = None
    item_id: str
    reason: str = "available_quantity_below_reorder_threshold"
    created_by: str = "warehouse"


class PolicySearchRequest(BaseModel):
    query: str
    locale: str = "zh"
    limit: int = 5


def parse_policy_markdown(path: Path) -> list[dict[str, str]]:
    document_title = ""
    section = ""
    current_clause: dict[str, str] | None = None
    clauses: list[dict[str, str]] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            document_title = line.removeprefix("# ").strip()
            continue
        if line.startswith("## "):
            section = line.removeprefix("## ").strip()
            continue
        if line.startswith("### "):
            match = re.match(r"###\s+([A-Z]+-\d+)\s+(.+)", line)
            if not match:
                current_clause = None
                continue
            current_clause = {
                "source_file": f"fixtures/policies/{path.name}",
                "document_title": document_title,
                "section": section,
                "clause_id": match.group(1),
                "clause_title": match.group(2),
                "text": "",
            }
            clauses.append(current_clause)
            continue
        if current_clause:
            current_clause["text"] = (current_clause["text"] + " " + line).strip()

    return clauses


def policy_keywords(query: str) -> set[str]:
    lowered = query.lower()
    keywords: set[str] = set()
    if any(token in lowered for token in ("退款", "refund", "退钱", "赔偿")):
        keywords.add("refund")
    if any(token in lowered for token in ("物流", "配送", "延迟", "shipment", "delivery", "package")):
        keywords.add("logistics")
    if any(token in lowered for token in ("差评", "评论", "review", "rating")):
        keywords.add("review")
    if any(token in lowered for token in ("库存", "补货", "inventory", "stock")):
        keywords.add("inventory")
    return keywords or {word for word in re.split(r"\W+", lowered) if len(word) >= 3}


def score_clause(clause: dict[str, str], keywords: set[str]) -> int:
    haystack = " ".join(
        [
            clause["section"],
            clause["clause_id"],
            clause["clause_title"],
            clause["text"],
        ]
    ).lower()
    aliases = {
        "refund": ("refund", "退款", "退钱", "赔偿"),
        "logistics": ("logistics", "shipment", "delivery", "物流", "配送", "包裹", "延迟"),
        "review": ("review", "rating", "评论", "差评"),
        "inventory": ("inventory", "stock", "库存", "补货"),
    }
    score = 0
    for keyword in keywords:
        terms = aliases.get(keyword, (keyword,))
        if any(term in haystack for term in terms):
            score += 2 if clause["clause_id"].lower().startswith(keyword) else 1
    return score


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.on_event("startup")
def startup_warehouse_store() -> None:
    get_warehouse_repository()


@app.get("/orders/{order_id}")
def get_order(order_id: str) -> dict:
    order = find_by_id("orders.json", "order_id", order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@app.get("/customers/{customer_id}")
def get_customer(customer_id: str) -> dict:
    customer = find_by_id("customers.json", "customer_id", customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    return customer


@app.get("/shipments/{shipment_id}")
def get_shipment(shipment_id: str) -> dict:
    shipment = find_by_id("shipments.json", "shipment_id", shipment_id)
    if not shipment:
        raise HTTPException(status_code=404, detail="shipment not found")
    return shipment


@app.get("/inventory/{sku}")
def get_inventory(sku: str) -> dict:
    inventory = find_by_id("inventory.json", "sku", sku)
    if not inventory:
        raise HTTPException(status_code=404, detail="inventory not found")
    return inventory


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


def normalize_category(value: str | None) -> str:
    raw = (value or "").strip().casefold()
    for category_id, aliases in CATEGORY_ALIASES.items():
        if raw == category_id or raw in {alias.casefold() for alias in aliases}:
            return category_id
    return raw


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
    for batch in load_json("inventory_batches.json"):
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
        rows,
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
    return max(int(row.get("quantity_on_hand", 0)) - int(row.get("quantity_reserved", 0)), 0)


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


def next_replenishment_request_id(repository: WarehouseRepository | None = None) -> str:
    existing_count = (
        repository.count_replenishment_requests()
        if repository
        else len(REPLENISHMENT_REQUESTS)
    )
    return f"REQ-{existing_count + 1001}"


def build_replenishment_request(
    payload: ReplenishmentRequestCreate,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any]:
    rows = [
        enrich_batch_row(row)
        for row in load_batch_inventory_rows(
            item_id=payload.item_id,
            warehouse_id=payload.warehouse_id,
            location_code=payload.location_code,
        )
    ]
    if not rows:
        raise HTTPException(status_code=404, detail="inventory item not found for replenishment")

    current_quantity = sum(int(row["quantity_available"]) for row in rows)
    reorder_threshold = max(int(row["reorder_threshold"]) for row in rows)
    suggested_quantity = max((reorder_threshold * 2) - current_quantity, reorder_threshold)
    first = rows[0]
    return {
        "request_id": next_replenishment_request_id(repository),
        "source": payload.source,
        "status": "pending_procurement_review",
        "warehouse_id": payload.warehouse_id,
        "warehouse_name": first["warehouse_name"],
        "location_code": payload.location_code,
        "item_id": payload.item_id,
        "item_name": first["item_name"],
        "category_id": first["category_id"],
        "category_name": first["category_name"],
        "current_quantity": current_quantity,
        "reorder_threshold": reorder_threshold,
        "suggested_quantity": suggested_quantity,
        "reason": payload.reason,
        "created_by": payload.created_by,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def enrich_batch_row(row: dict[str, Any]) -> dict[str, Any]:
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


@app.get("/warehouse/inventory/table-schema")
def get_warehouse_inventory_table_schema() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_id": "warehouse_batch_inventory",
        "source": "mock-api",
        "fields": WAREHOUSE_INVENTORY_TABLE_SCHEMA,
    }


@app.post("/warehouse/inventory/search")
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


@app.post("/warehouse/inventory/table-rows")
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


@app.get("/warehouse/inventory/{item_id}")
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


@app.post("/warehouse/exceptions/search")
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


@app.post("/warehouse/fulfillment/check")
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


@app.post("/procurement/mock")
def procurement_mock(payload: dict) -> dict:
    item_id = str(payload.get("item_id") or payload.get("sku") or "").strip()
    rows = [enrich_batch_row(row) for row in load_batch_inventory_rows(item_id=item_id)] if item_id else []
    if not rows:
        return {
            "ok": False,
            "system": "mock-procurement",
            "item_id": item_id,
            "recommendation": "request_valid_item",
            "message": "未找到商品，需要提供有效 item_id。",
        }

    available = sum(int(row["quantity_available"]) for row in rows)
    reorder_threshold = max(int(row["reorder_threshold"]) for row in rows)
    should_replenish = any(row["quantity_available"] < row["reorder_threshold"] for row in rows)
    return {
        "ok": True,
        "system": "mock-procurement",
        "item_id": item_id,
        "available": available,
        "reorder_threshold": reorder_threshold,
        "recommendation": "create_purchase_request" if should_replenish else "no_action",
        "message": "库存低于阈值，建议创建采购申请。" if should_replenish else "当前库存无需补货。",
    }


@app.post("/procurement/replenishment-requests")
def create_replenishment_request(payload: ReplenishmentRequestCreate) -> dict[str, Any]:
    repository = get_warehouse_repository()
    request = build_replenishment_request(payload, repository)
    if repository:
        request = repository.create_replenishment_request(request)
    else:
        REPLENISHMENT_REQUESTS.append(request)
    return {"ok": True, "request": request}


@app.get("/procurement/replenishment-requests")
def list_replenishment_requests(status: str | None = None) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        items = repository.list_replenishment_requests(status=status)
    else:
        items = [
            request
            for request in REPLENISHMENT_REQUESTS
            if not status or request["status"] == status
        ]
    return {"ok": True, "count": len(items), "items": items}


@app.post("/operations/summary/mock")
def operations_summary_mock(payload: dict) -> dict:
    query = str(payload.get("query") or payload.get("text") or "").strip()
    return {
        "ok": True,
        "system": "mock-operations",
        "query": query,
        "summary": "今日主要运营异常集中在低库存批次、临期乳制品和待跟进售后订单。",
        "incidents": [
            {
                "domain": "warehouse",
                "severity": "medium",
                "message": "item_vinda_tissue 在深圳仓 A1 的可用库存低于补货阈值。",
            },
            {
                "domain": "customer_support",
                "severity": "low",
                "message": "退款咨询需要引用售后政策。",
            },
        ],
        "next_actions": ["检查低库存批次", "汇总客服退款问题", "确认采购补货计划"],
    }


@app.post("/policies/search")
def search_policies(request: PolicySearchRequest) -> dict[str, Any]:
    filename = "after_sales_policy.zh.md" if request.locale.startswith("zh") else "after_sales_policy.md"
    policy_path = FIXTURE_DIR / "policies" / filename
    if not policy_path.exists():
        raise HTTPException(status_code=404, detail="policy document not found")

    keywords = policy_keywords(request.query)
    scored = [
        (score_clause(clause, keywords), clause)
        for clause in parse_policy_markdown(policy_path)
    ]
    matches = [
        clause
        for score, clause in sorted(scored, key=lambda item: item[0], reverse=True)
        if score > 0
    ][: request.limit]

    return {
        "ok": True,
        "query": request.query,
        "source": f"fixtures/policies/{filename}",
        "matches": matches,
    }


@app.post("/approval-requests")
def create_approval(request: ApprovalRequest) -> dict:
    record = request.model_dump() | {"status": "pending"}
    APPROVALS.append(record)
    return record


@app.get("/approval-requests")
def list_approvals() -> list[dict]:
    return APPROVALS


@app.post("/tickets")
def create_ticket(payload: dict) -> dict:
    record = payload | {"status": "open"}
    TICKETS.append(record)
    return record


@app.get("/tickets")
def list_tickets() -> list[dict]:
    return TICKETS


@app.post("/internal-notifications")
def create_notification(payload: dict) -> dict:
    record = payload | {"status": "sent"}
    INTERNAL_NOTIFICATIONS.append(record)
    return record


@app.get("/internal-notifications")
def list_notifications() -> list[dict]:
    return INTERNAL_NOTIFICATIONS


@app.post("/run-logs")
def create_run_log(payload: dict) -> dict:
    RUN_LOGS.append(payload)
    return payload


@app.get("/run-logs")
def list_run_logs() -> list[dict]:
    return RUN_LOGS


@app.post("/dead-letter")
def create_dead_letter(payload: dict) -> dict:
    record = payload | {"status": "dead_lettered"}
    DEAD_LETTERS.append(record)
    return record


@app.get("/dead-letter")
def list_dead_letters() -> list[dict]:
    return DEAD_LETTERS


@app.post("/replay/{event_id}")
def replay_event(event_id: str) -> dict:
    record = {"event_id": event_id, "status": "queued_for_replay"}
    REPLAYS.append(record)
    return record


@app.get("/replay")
def list_replays() -> list[dict]:
    return REPLAYS
