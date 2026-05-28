import re
from datetime import UTC, date, datetime, timedelta
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
DELIVERY_CASES: list[dict] = []
REPLENISHMENT_REQUESTS: list[dict] = []
PURCHASE_ORDER_DRAFTS: list[dict] = []
RECEIVED_INVENTORY_BATCHES: list[dict] = []
WAREHOUSE_INVENTORY_SYNC_JOBS: list[dict] = []
WAREHOUSE_REPOSITORY: WarehouseRepository | None | bool = False

PROCUREMENT_REPLENISHMENT_REQUEST_TABLE_SCHEMA = [
    {"name": "Request ID", "type": "text"},
    {"name": "Status", "type": "single_select", "options": [
        {"name": "pending_procurement_review", "color": 24},
        {"name": "purchase_order_draft_created", "color": 28},
        {"name": "rejected", "color": 17},
    ]},
    {"name": "Source", "type": "text"},
    {"name": "Warehouse", "type": "text"},
    {"name": "Warehouse ID", "type": "text"},
    {"name": "Location", "type": "text"},
    {"name": "Category", "type": "text"},
    {"name": "Category ID", "type": "text"},
    {"name": "Item ID", "type": "text"},
    {"name": "Item Name", "type": "text"},
    {"name": "Current Quantity", "type": "number"},
    {"name": "Reorder Threshold", "type": "number"},
    {"name": "Suggested Quantity", "type": "number"},
    {"name": "Reason", "type": "text"},
    {"name": "Created By", "type": "text"},
    {"name": "Created At", "type": "text"},
    {"name": "Updated At", "type": "text"},
    {"name": "Last Synced At", "type": "text"},
    {"name": "Sync Status", "type": "single_select", "options": [
        {"name": "synced", "color": 28},
        {"name": "pending", "color": 24},
        {"name": "failed", "color": 17},
    ]},
    {"name": "Source Version", "type": "text"},
]

PROCUREMENT_PURCHASE_ORDER_DRAFT_TABLE_SCHEMA = [
    {"name": "PO Draft ID", "type": "text"},
    {"name": "Request ID", "type": "text"},
    {"name": "Status", "type": "single_select", "options": [
        {"name": "draft", "color": 24},
        {"name": "submitted", "color": 28},
        {"name": "received_at_warehouse", "color": 28},
        {"name": "cancelled", "color": 17},
    ]},
    {"name": "Supplier ID", "type": "text"},
    {"name": "Supplier Name", "type": "text"},
    {"name": "Item ID", "type": "text"},
    {"name": "Quantity", "type": "number"},
    {"name": "Unit Price", "type": "number"},
    {"name": "Currency", "type": "text"},
    {"name": "Estimated Total Price", "type": "number"},
    {"name": "Lead Time Days", "type": "number"},
    {"name": "Estimated Arrival Date", "type": "text"},
    {"name": "Created By", "type": "text"},
    {"name": "Created At", "type": "text"},
    {"name": "Updated At", "type": "text"},
    {"name": "Last Synced At", "type": "text"},
    {"name": "Sync Status", "type": "single_select", "options": [
        {"name": "synced", "color": 28},
        {"name": "pending", "color": 24},
        {"name": "failed", "color": 17},
    ]},
    {"name": "Source Version", "type": "text"},
]


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


class ReplenishmentApproveRequest(BaseModel):
    created_by: str = "procurement"


class ReplenishmentRejectRequest(BaseModel):
    reason: str = "procurement_rejected"
    updated_by: str = "procurement"


class ReplenishmentApproveBatchRequest(BaseModel):
    created_by: str = "procurement"
    status: str = "pending_procurement_review"


class PurchaseOrderDraftConfirmArrivalBatchRequest(BaseModel):
    po_draft_ids: list[str]
    received_by: str = "warehouse"


class WarehouseInventorySyncJobUpdateRequest(BaseModel):
    processed_by: str = "warehouse-agent"
    result: dict[str, Any] | None = None
    error: str | None = None


class ReplenishmentRequestTableRowsRequest(BaseModel):
    status: str | None = None
    request_id: str | None = None
    limit: int = 100


class PurchaseOrderDraftTableRowsRequest(BaseModel):
    request_id: str | None = None
    po_draft_id: str | None = None
    limit: int = 100


class DeliveryStatusLookupRequest(BaseModel):
    order_id: str | None = None
    shipment_id: str | None = None
    query: str | None = None
    text: str | None = None
    input: str | None = None


class DeliveryExceptionSearchRequest(BaseModel):
    status: str | None = None
    carrier: str | None = None
    min_delay_days: int = 1
    limit: int = 50


class DeliveryCaseCreateRequest(BaseModel):
    shipment_id: str | None = None
    order_id: str | None = None
    case_type: str = "delivery_follow_up"
    reason: str = "delivery follow-up requested"
    created_by: str = "delivery-agent"


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


def extract_id_from_text(*values: str | None, prefix: str) -> str | None:
    for value in values:
        if not value:
            continue
        match = re.search(rf"\b{re.escape(prefix)}[0-9A-Za-z_]+\b", str(value), re.IGNORECASE)
        if match:
            return match.group(0).lower()
    return None


def find_order_by_shipment_id(shipment_id: str) -> dict[str, Any] | None:
    for order in load_json("orders.json"):
        if order.get("shipment_id") == shipment_id:
            return order
    return None


def delivery_risk_level(shipment: dict[str, Any]) -> str:
    delay_days = int(shipment.get("delay_days") or 0)
    status = str(shipment.get("status") or "").lower()
    if status in {"lost", "delayed"} or delay_days >= 5:
        return "high"
    if delay_days > 0 or status in {"exception", "in_transit"}:
        return "medium"
    return "low"


def delivery_exception_type(shipment: dict[str, Any]) -> str:
    status = str(shipment.get("status") or "").lower()
    if status == "lost":
        return "package_lost"
    if status == "delayed" or int(shipment.get("delay_days") or 0) > 0:
        return "delivery_delay"
    return "none"


def delivery_recommendation(shipment: dict[str, Any]) -> str:
    status = str(shipment.get("status") or "").lower()
    delay_days = int(shipment.get("delay_days") or 0)
    if status == "lost":
        return "建议创建物流丢件 case，并通知客服同步客户。"
    if status == "delayed" or delay_days >= 5:
        return "物流已明显延迟，建议创建物流跟进 case，并让客服同步安抚客户。"
    if delay_days > 0:
        return "物流存在轻微延迟，建议继续跟踪承运商状态。"
    return "物流状态正常，无需创建处理 case。"


def build_delivery_status(order: dict[str, Any] | None, shipment: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "system": "mock-delivery",
        "order": order,
        "shipment": shipment,
        "risk_level": delivery_risk_level(shipment),
        "exception_type": delivery_exception_type(shipment),
        "recommendation": delivery_recommendation(shipment),
    }


@app.post("/delivery/status/lookup")
def delivery_status_lookup(payload: DeliveryStatusLookupRequest) -> dict[str, Any]:
    order_id = payload.order_id or extract_id_from_text(payload.query, payload.text, payload.input, prefix="ord_")
    shipment_id = payload.shipment_id or extract_id_from_text(payload.query, payload.text, payload.input, prefix="ship_")

    order: dict[str, Any] | None = None
    if order_id:
        order = get_order(order_id)
        shipment_id = str(order.get("shipment_id") or shipment_id or "")

    if not shipment_id:
        raise HTTPException(status_code=400, detail="order_id or shipment_id is required")

    shipment = get_shipment(shipment_id)
    if order is None:
        order = find_order_by_shipment_id(shipment_id)

    return build_delivery_status(order, shipment)


@app.post("/delivery/exceptions/search")
def delivery_exceptions_search(payload: DeliveryExceptionSearchRequest) -> dict[str, Any]:
    status = str(payload.status or "").lower()
    carrier = str(payload.carrier or "").lower()
    min_delay_days = max(int(payload.min_delay_days or 1), 0)
    limit = max(min(int(payload.limit or 50), 500), 1)
    items: list[dict[str, Any]] = []

    for shipment in load_json("shipments.json"):
        shipment_status = str(shipment.get("status") or "").lower()
        shipment_carrier = str(shipment.get("carrier") or "").lower()
        delay_days = int(shipment.get("delay_days") or 0)
        exception_type = delivery_exception_type(shipment)
        if exception_type == "none":
            continue
        if status and shipment_status != status:
            continue
        if carrier and shipment_carrier != carrier:
            continue
        if delay_days < min_delay_days and shipment_status != "lost":
            continue
        order = find_order_by_shipment_id(str(shipment["shipment_id"]))
        items.append({
            "shipment_id": shipment["shipment_id"],
            "order_id": order["order_id"] if order else None,
            "carrier": shipment.get("carrier"),
            "status": shipment.get("status"),
            "delay_days": delay_days,
            "exception_type": exception_type,
            "risk_level": delivery_risk_level(shipment),
            "recommendation": delivery_recommendation(shipment),
        })

    return {
        "ok": True,
        "system": "mock-delivery",
        "count": len(items[:limit]),
        "items": items[:limit],
    }


@app.post("/delivery/cases")
def create_delivery_case(payload: DeliveryCaseCreateRequest) -> dict[str, Any]:
    shipment_id = payload.shipment_id
    order: dict[str, Any] | None = None
    if payload.order_id:
        order = get_order(payload.order_id)
        shipment_id = str(order.get("shipment_id") or shipment_id or "")
    if not shipment_id:
        raise HTTPException(status_code=400, detail="shipment_id or order_id is required")

    shipment = get_shipment(shipment_id)
    if order is None:
        order = find_order_by_shipment_id(shipment_id)
    now = datetime.now(UTC).isoformat()
    case = {
        "case_id": f"DCASE-{len(DELIVERY_CASES) + 1:04d}",
        "case_type": payload.case_type,
        "status": "open",
        "shipment_id": shipment_id,
        "order_id": order["order_id"] if order else None,
        "carrier": shipment.get("carrier"),
        "reason": payload.reason,
        "risk_level": delivery_risk_level(shipment),
        "created_by": payload.created_by,
        "created_at": now,
        "updated_at": now,
    }
    DELIVERY_CASES.append(case)
    return {
        "ok": True,
        "system": "mock-delivery",
        "case": case,
        "recommendation": "物流 case 已创建，请跟进承运商并同步客服。",
    }


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


def next_purchase_order_draft_id(repository: WarehouseRepository | None = None) -> str:
    existing_count = (
        repository.count_purchase_order_drafts()
        if repository
        else len(PURCHASE_ORDER_DRAFTS)
    )
    return f"POD-{existing_count + 5001}"


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


def find_replenishment_request(
    request_id: str,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    if repository:
        return repository.get_replenishment_request(request_id)
    return next(
        (request for request in REPLENISHMENT_REQUESTS if request["request_id"] == request_id),
        None,
    )


def update_replenishment_request_status(
    request_id: str,
    *,
    status: str,
    reason: str | None = None,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    updated_at = datetime.now(UTC).isoformat()
    if repository:
        return repository.update_replenishment_request(
            request_id,
            status=status,
            reason=reason,
            updated_at=updated_at,
        )
    request = find_replenishment_request(request_id)
    if not request:
        return None
    request["status"] = status
    if reason is not None:
        request["reason"] = reason
    request["updated_at"] = updated_at
    return request


def find_default_supplier(
    item_id: str,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    if repository:
        return repository.get_default_supplier(item_id)
    return next(
        (supplier for supplier in load_json("procurement_suppliers.json") if supplier["item_id"] == item_id),
        None,
    )


def list_purchase_order_drafts_for_request(
    request_id: str | None = None,
    repository: WarehouseRepository | None = None,
) -> list[dict[str, Any]]:
    if repository:
        return repository.list_purchase_order_drafts(request_id=request_id)
    return [
        draft
        for draft in PURCHASE_ORDER_DRAFTS
        if not request_id or draft["request_id"] == request_id
    ]


def estimated_arrival_date_for_request(request: dict[str, Any], supplier: dict[str, Any]) -> str:
    created_at = str(request.get("created_at") or "")
    try:
        request_date = datetime.fromisoformat(created_at).date()
    except ValueError:
        request_date = datetime.now(UTC).date()
    return (request_date + timedelta(days=int(supplier["lead_time_days"]))).isoformat()


def create_purchase_order_draft(
    request: dict[str, Any],
    supplier: dict[str, Any],
    payload: ReplenishmentApproveRequest,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    draft = {
        "po_draft_id": next_purchase_order_draft_id(repository),
        "request_id": request["request_id"],
        "supplier_id": supplier["supplier_id"],
        "supplier_name": supplier["supplier_name"],
        "item_id": request["item_id"],
        "quantity": int(request["suggested_quantity"]),
        "unit_price": int(supplier["unit_price"]),
        "currency": supplier["currency"],
        "estimated_total_price": int(request["suggested_quantity"]) * int(supplier["unit_price"]),
        "lead_time_days": int(supplier["lead_time_days"]),
        "estimated_arrival_date": estimated_arrival_date_for_request(request, supplier),
        "status": "draft",
        "created_by": payload.created_by,
        "created_at": now,
        "updated_at": now,
    }
    if repository:
        return repository.create_purchase_order_draft(draft)
    PURCHASE_ORDER_DRAFTS.append(draft)
    return draft


def approve_replenishment_request_data(
    request: dict[str, Any],
    payload: ReplenishmentApproveRequest,
    repository: WarehouseRepository | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    drafts = list_purchase_order_drafts_for_request(request["request_id"], repository)
    draft = drafts[0] if drafts else None
    created = False
    if not draft:
        supplier = find_default_supplier(request["item_id"], repository)
        if not supplier:
            raise ValueError("default_supplier_not_found")
        draft = create_purchase_order_draft(request, supplier, payload, repository)
        created = True

    updated = update_replenishment_request_status(
        request["request_id"],
        status="purchase_order_draft_created",
        repository=repository,
    )
    return updated or request, draft, created


def get_purchase_order_draft_by_id(
    po_draft_id: str,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    if repository:
        return repository.get_purchase_order_draft(po_draft_id)
    return next(
        (draft for draft in PURCHASE_ORDER_DRAFTS if draft["po_draft_id"] == po_draft_id),
        None,
    )


def update_purchase_order_draft_status(
    po_draft_id: str,
    *,
    status: str,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    updated_at = datetime.now(UTC).isoformat()
    if repository:
        return repository.update_purchase_order_draft_status(
            po_draft_id,
            status=status,
            updated_at=updated_at,
        )
    draft = get_purchase_order_draft_by_id(po_draft_id)
    if not draft:
        return None
    draft["status"] = status
    draft["updated_at"] = updated_at
    return draft


def shelf_life_days_for_category(category_id: str) -> int:
    return {
        "dairy": 45,
        "beverage": 365,
        "paper": 730,
        "daily_chemical": 1095,
        "office_supply": 1460,
    }.get(category_id, 365)


def build_receipt_inventory_batch(
    draft: dict[str, Any],
    request: dict[str, Any],
    *,
    received_at: date,
) -> dict[str, Any]:
    expiry_date = received_at + timedelta(days=shelf_life_days_for_category(request["category_id"]))
    return {
        "warehouse_id": request["warehouse_id"],
        "location_code": request.get("location_code") or "A1",
        "item_id": draft["item_id"],
        "batch_no": f"RCV-{draft['po_draft_id']}",
        "production_date": received_at.isoformat(),
        "expiry_date": expiry_date.isoformat(),
        "quantity_on_hand": int(draft["quantity"]),
        "quantity_reserved": 0,
        "reorder_threshold": int(request["reorder_threshold"]),
        "storage_status": "available",
    }


def find_inventory_batch_by_batch_no(
    batch_no: str,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    if repository:
        return repository.get_inventory_batch_by_batch_no(batch_no)
    return next(
        (batch for batch in RECEIVED_INVENTORY_BATCHES if batch["batch_no"] == batch_no),
        None,
    )


def create_inventory_receipt_batch(
    batch: dict[str, Any],
    repository: WarehouseRepository | None = None,
) -> dict[str, Any]:
    if repository:
        return repository.create_inventory_batch(batch)
    if "batch_id" not in batch:
        batch = {**batch, "batch_id": next_fallback_inventory_batch_id()}
    RECEIVED_INVENTORY_BATCHES.append(batch)
    return batch


def next_fallback_inventory_batch_id() -> int:
    fixture_batches = load_json("inventory_batches.json")
    received_ids = [
        int(batch["batch_id"])
        for batch in RECEIVED_INVENTORY_BATCHES
        if isinstance(batch.get("batch_id"), int)
    ]
    return max([len(fixture_batches), *received_ids]) + 1


def warehouse_sync_request_for_receipt(
    draft: dict[str, Any],
    request: dict[str, Any],
    batch: dict[str, Any],
) -> dict[str, Any]:
    item_id = draft["item_id"]
    return {
        "team": "warehouse",
        "event": "warehouse_inventory_sync_requested",
        "po_draft_id": draft["po_draft_id"],
        "request_id": draft["request_id"],
        "item_id": item_id,
        "warehouse_id": request["warehouse_id"],
        "warehouse_name": request["warehouse_name"],
        "location_code": request.get("location_code") or "",
        "batch_no": batch["batch_no"],
        "quantity": int(draft["quantity"]),
        "next_action": "notify_warehouse_to_sync_inventory_table",
        "suggested_message": f"@warehouse 同步 {item_id} 库存到飞书",
    }


def upsert_warehouse_inventory_sync_job(
    sync_request: dict[str, Any],
    *,
    created_by: str,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any]:
    job_id = f"WSJ-{sync_request['po_draft_id']}"
    now = datetime.now(UTC).isoformat()
    if repository:
        return repository.upsert_warehouse_inventory_sync_job(
            {
                "job_id": job_id,
                **sync_request,
                "status": "pending",
                "created_by": created_by,
                "created_at": now,
                "updated_at": now,
                "processed_by": "",
                "processed_at": "",
                "result": {},
                "error": None,
            }
        )
    existing = next(
        (job for job in WAREHOUSE_INVENTORY_SYNC_JOBS if job["job_id"] == job_id),
        None,
    )
    if existing:
        existing.update(
            {
                **sync_request,
                "status": existing["status"] if existing["status"] in {"pending", "processing"} else "pending",
                "updated_at": now,
                "error": None,
            }
        )
        return existing

    job = {
        "job_id": job_id,
        **sync_request,
        "status": "pending",
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
        "processed_by": "",
        "processed_at": "",
        "result": {},
        "error": None,
    }
    WAREHOUSE_INVENTORY_SYNC_JOBS.append(job)
    return job


def update_warehouse_inventory_sync_job(
    job_id: str,
    *,
    status: str,
    processed_by: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    if repository:
        return repository.update_warehouse_inventory_sync_job(
            job_id,
            status=status,
            processed_by=processed_by,
            processed_at=now,
            updated_at=now,
            result=result,
            error=error,
        )
    job = next(
        (item for item in WAREHOUSE_INVENTORY_SYNC_JOBS if item["job_id"] == job_id),
        None,
    )
    if not job:
        return None
    job.update(
        {
            "status": status,
            "processed_by": processed_by,
            "processed_at": now,
            "updated_at": now,
            "result": result or {},
            "error": error,
        }
    )
    return job


def confirm_purchase_order_draft_arrival(
    po_draft_id: str,
    *,
    repository: WarehouseRepository | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, str]:
    draft = get_purchase_order_draft_by_id(po_draft_id, repository)
    if not draft:
        return None, None, None, "purchase_order_draft_not_found"
    request = find_replenishment_request(draft["request_id"], repository)
    if not request:
        return draft, None, None, "replenishment_request_not_found"

    batch_no = f"RCV-{draft['po_draft_id']}"
    existing_batch = find_inventory_batch_by_batch_no(batch_no, repository)
    action = "reused"
    batch = existing_batch
    if not batch:
        receipt_batch = build_receipt_inventory_batch(
            draft,
            request,
            received_at=datetime.now(UTC).date(),
        )
        batch = create_inventory_receipt_batch(receipt_batch, repository)
        action = "created"

    updated_draft = update_purchase_order_draft_status(
        po_draft_id,
        status="received_at_warehouse",
        repository=repository,
    )
    return updated_draft or draft, request, batch, action


def procurement_replenishment_request_table_fields(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "Request ID": request["request_id"],
        "Status": request["status"],
        "Source": request["source"],
        "Warehouse": request["warehouse_name"],
        "Warehouse ID": request["warehouse_id"],
        "Location": request.get("location_code") or "",
        "Category": request["category_name"],
        "Category ID": request["category_id"],
        "Item ID": request["item_id"],
        "Item Name": request["item_name"],
        "Current Quantity": int(request["current_quantity"]),
        "Reorder Threshold": int(request["reorder_threshold"]),
        "Suggested Quantity": int(request["suggested_quantity"]),
        "Reason": request["reason"],
        "Created By": request["created_by"],
        "Created At": request["created_at"],
        "Updated At": request["updated_at"],
        "Last Synced At": datetime.now(UTC).isoformat(),
        "Sync Status": "synced",
        "Source Version": f"mock-api:{request['request_id']}:{request['updated_at']}",
    }


def procurement_purchase_order_draft_table_fields(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "PO Draft ID": draft["po_draft_id"],
        "Request ID": draft["request_id"],
        "Status": draft["status"],
        "Supplier ID": draft["supplier_id"],
        "Supplier Name": draft["supplier_name"],
        "Item ID": draft["item_id"],
        "Quantity": int(draft["quantity"]),
        "Unit Price": int(draft["unit_price"]),
        "Currency": draft["currency"],
        "Estimated Total Price": int(draft["estimated_total_price"]),
        "Lead Time Days": int(draft["lead_time_days"]),
        "Estimated Arrival Date": draft["estimated_arrival_date"],
        "Created By": draft["created_by"],
        "Created At": draft["created_at"],
        "Updated At": draft["updated_at"],
        "Last Synced At": datetime.now(UTC).isoformat(),
        "Sync Status": "synced",
        "Source Version": f"mock-api:{draft['po_draft_id']}:{draft['updated_at']}",
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


@app.post("/procurement/replenishment-requests/{request_id}/approve")
def approve_replenishment_request(
    request_id: str,
    payload: ReplenishmentApproveRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    request = find_replenishment_request(request_id, repository)
    if not request:
        raise HTTPException(status_code=404, detail="replenishment request not found")

    try:
        updated, draft, _created = approve_replenishment_request_data(request, payload, repository)
    except ValueError as error:
        if str(error) == "default_supplier_not_found":
            raise HTTPException(status_code=400, detail="default supplier not found for item") from error
        raise
    return {"ok": True, "request": updated, "draft": draft}


@app.post("/procurement/replenishment-requests/approve-batch")
def approve_replenishment_requests_batch(
    payload: ReplenishmentApproveBatchRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    requests = (
        repository.list_replenishment_requests(status=payload.status)
        if repository
        else [
            request
            for request in REPLENISHMENT_REQUESTS
            if request["status"] == payload.status
        ]
    )
    approved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    approve_payload = ReplenishmentApproveRequest(created_by=payload.created_by)
    for request in requests:
        try:
            updated, draft, created = approve_replenishment_request_data(
                request,
                approve_payload,
                repository,
            )
        except ValueError as error:
            error_code = str(error)
            errors.append(
                {
                    "request_id": request["request_id"],
                    "item_id": request["item_id"],
                    "error": error_code,
                    "message": "default supplier not found for item"
                    if error_code == "default_supplier_not_found"
                    else error_code,
                }
            )
            continue
        approved.append(
            {
                "request_id": updated["request_id"],
                "status": updated["status"],
                "po_draft_id": draft["po_draft_id"],
                "item_id": draft["item_id"],
                "supplier_id": draft["supplier_id"],
                "supplier_name": draft["supplier_name"],
                "quantity": draft["quantity"],
                "estimated_arrival_date": draft["estimated_arrival_date"],
                "action": "created" if created else "reused",
            }
        )
    return {
        "ok": True,
        "status": payload.status,
        "processed_count": len(requests),
        "approved_count": len(approved),
        "skipped_count": len(errors),
        "created_or_reused_drafts": approved,
        "errors": errors,
    }


@app.post("/procurement/replenishment-requests/{request_id}/reject")
def reject_replenishment_request(
    request_id: str,
    payload: ReplenishmentRejectRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    request = find_replenishment_request(request_id, repository)
    if not request:
        raise HTTPException(status_code=404, detail="replenishment request not found")
    updated = update_replenishment_request_status(
        request_id,
        status="rejected",
        reason=payload.reason,
        repository=repository,
    )
    return {"ok": True, "request": updated}


@app.get("/procurement/purchase-order-drafts")
def list_purchase_order_drafts(request_id: str | None = None) -> dict[str, Any]:
    repository = get_warehouse_repository()
    items = list_purchase_order_drafts_for_request(request_id, repository)
    return {"ok": True, "count": len(items), "items": items}


@app.post("/procurement/purchase-order-drafts/confirm-arrival-batch")
def confirm_purchase_order_draft_arrival_batch(
    payload: PurchaseOrderDraftConfirmArrivalBatchRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    po_draft_ids = []
    for value in payload.po_draft_ids:
        normalized = str(value).strip().upper()
        if normalized and normalized not in po_draft_ids:
            po_draft_ids.append(normalized)
    if not po_draft_ids:
        raise HTTPException(status_code=400, detail="po_draft_ids required")

    confirmed_items: list[dict[str, Any]] = []
    sync_requests: list[dict[str, Any]] = []
    sync_jobs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for po_draft_id in po_draft_ids:
        draft, request, batch, action = confirm_purchase_order_draft_arrival(
            po_draft_id,
            repository=repository,
        )
        if not draft or not request or not batch:
            errors.append(
                {
                    "po_draft_id": po_draft_id,
                    "error": action,
                    "message": action.replace("_", " "),
                }
            )
            continue

        sync_request = warehouse_sync_request_for_receipt(draft, request, batch)
        sync_requests.append(sync_request)
        sync_job = upsert_warehouse_inventory_sync_job(
            sync_request,
            created_by=payload.received_by,
            repository=repository,
        )
        sync_jobs.append(sync_job)
        INTERNAL_NOTIFICATIONS.append(
            {
                "event_id": f"warehouse_inventory_sync_requested:{po_draft_id}",
                "team": "warehouse",
                "status": "pending",
                "payload": sync_request,
                "created_by": payload.received_by,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        confirmed_items.append(
            {
                "po_draft_id": draft["po_draft_id"],
                "request_id": draft["request_id"],
                "status": draft["status"],
                "item_id": draft["item_id"],
                "warehouse_id": request["warehouse_id"],
                "warehouse_name": request["warehouse_name"],
                "location_code": request.get("location_code") or "",
                "quantity": int(draft["quantity"]),
                "batch_no": batch["batch_no"],
                "sync_job_id": sync_job["job_id"],
                "action": action,
            }
        )

    return {
        "ok": True,
        "processed_count": len(po_draft_ids),
        "confirmed_count": len(confirmed_items),
        "skipped_count": len(errors),
        "confirmed_items": confirmed_items,
        "warehouse_inventory_sync_requests": sync_requests,
        "warehouse_inventory_sync_jobs": sync_jobs,
        "errors": errors,
        "next_action": "通知 Warehouse 根据 warehouse_inventory_sync_requests 同步库存飞书视图。",
    }


@app.get("/procurement/replenishment-requests/table-schema")
def get_procurement_replenishment_request_table_schema() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_id": "procurement_replenishment_requests",
        "source": "mock-api",
        "fields": PROCUREMENT_REPLENISHMENT_REQUEST_TABLE_SCHEMA,
    }


@app.post("/procurement/replenishment-requests/table-rows")
def get_procurement_replenishment_request_table_rows(
    payload: ReplenishmentRequestTableRowsRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        items = repository.list_replenishment_requests(status=payload.status)
    else:
        items = [
            request
            for request in REPLENISHMENT_REQUESTS
            if not payload.status or request["status"] == payload.status
        ]
    if payload.request_id:
        items = [request for request in items if request["request_id"] == payload.request_id]
    limit = max(min(int(payload.limit or 100), 500), 1)
    items = items[:limit]
    return {
        "ok": True,
        "schema_id": "procurement_replenishment_requests",
        "count": len(items),
        "items": [
            {
                "request_id": request["request_id"],
                "fields": procurement_replenishment_request_table_fields(request),
            }
            for request in items
        ],
    }


@app.get("/procurement/purchase-order-drafts/table-schema")
def get_procurement_purchase_order_draft_table_schema() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_id": "procurement_purchase_order_drafts",
        "source": "mock-api",
        "fields": PROCUREMENT_PURCHASE_ORDER_DRAFT_TABLE_SCHEMA,
    }


@app.post("/procurement/purchase-order-drafts/table-rows")
def get_procurement_purchase_order_draft_table_rows(
    payload: PurchaseOrderDraftTableRowsRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    items = list_purchase_order_drafts_for_request(payload.request_id, repository)
    if payload.po_draft_id:
        items = [draft for draft in items if draft["po_draft_id"] == payload.po_draft_id]
    limit = max(min(int(payload.limit or 100), 500), 1)
    items = items[:limit]
    return {
        "ok": True,
        "schema_id": "procurement_purchase_order_drafts",
        "count": len(items),
        "items": [
            {
                "po_draft_id": draft["po_draft_id"],
                "request_id": draft["request_id"],
                "fields": procurement_purchase_order_draft_table_fields(draft),
            }
            for draft in items
        ],
    }


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


@app.get("/warehouse/inventory-sync-jobs")
def list_warehouse_inventory_sync_jobs(status: str | None = None) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        items = repository.list_warehouse_inventory_sync_jobs(status=status)
    else:
        items = [
            job
            for job in WAREHOUSE_INVENTORY_SYNC_JOBS
            if not status or job["status"] == status
        ]
    return {"ok": True, "count": len(items), "items": items}


@app.post("/warehouse/inventory-sync-jobs/{job_id}/complete")
def complete_warehouse_inventory_sync_job(
    job_id: str,
    payload: WarehouseInventorySyncJobUpdateRequest,
) -> dict[str, Any]:
    if payload.result and (
        payload.result.get("ok") is False
        or (payload.result.get("error") and payload.result.get("ok") is not True)
    ):
        raise HTTPException(
            status_code=400,
            detail="warehouse inventory sync result is not successful",
        )
    job = update_warehouse_inventory_sync_job(
        job_id,
        status="completed",
        processed_by=payload.processed_by,
        result=payload.result,
        repository=get_warehouse_repository(),
    )
    if not job:
        raise HTTPException(status_code=404, detail="warehouse inventory sync job not found")
    return {"ok": True, "job": job}


@app.post("/warehouse/inventory-sync-jobs/{job_id}/fail")
def fail_warehouse_inventory_sync_job(
    job_id: str,
    payload: WarehouseInventorySyncJobUpdateRequest,
) -> dict[str, Any]:
    job = update_warehouse_inventory_sync_job(
        job_id,
        status="failed",
        processed_by=payload.processed_by,
        error=payload.error or "warehouse inventory sync failed",
        repository=get_warehouse_repository(),
    )
    if not job:
        raise HTTPException(status_code=404, detail="warehouse inventory sync job not found")
    return {"ok": True, "job": job}


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
