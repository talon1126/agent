import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.store import FIXTURE_DIR, find_by_id, load_json
from app.routers.procurement.router import router as procurement_router
from app.routers.procurement.state import PURCHASE_ORDERS, REPLENISHMENT_REQUESTS
from app.routers.warehouse.router import router as warehouse_router
from app.routers.warehouse.state import (
    RECEIVED_INVENTORY_BATCHES,
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES,
    WAREHOUSE_INVENTORY_SYNC_JOBS,
    WAREHOUSE_ORDER_ITEMS,
    WAREHOUSE_ORDERS,
    get_warehouse_repository,
)

app = FastAPI(title="Ecommerce Mock Enterprise API")
app.include_router(procurement_router)
app.include_router(warehouse_router)

__all__ = [
    "app",
    "RECEIVED_INVENTORY_BATCHES",
    "WAREHOUSE_BATCH_QUANTITY_OVERRIDES",
    "WAREHOUSE_INVENTORY_SYNC_JOBS",
    "WAREHOUSE_ORDER_ITEMS",
    "WAREHOUSE_ORDERS",
    "REPLENISHMENT_REQUESTS",
    "PURCHASE_ORDERS",
]

APPROVALS: list[dict] = []
TICKETS: list[dict] = []
RUN_LOGS: list[dict] = []
DEAD_LETTERS: list[dict] = []
REPLAYS: list[dict] = []
INTERNAL_NOTIFICATIONS: list[dict] = []
DELIVERY_CASES: list[dict] = []



class ApprovalRequest(BaseModel):
    event_id: str
    recommended_action: str
    explanation: str


class WarehouseInventorySyncJobUpdateRequest(BaseModel):
    processed_by: str = "warehouse-agent"
    result: dict[str, Any] | None = None
    error: str | None = None


class WarehouseOrderItemCreate(BaseModel):
    item_id: str
    warehouse_id: str
    quantity: int
    location_code: str | None = None


class WarehouseOrderCreate(BaseModel):
    order_id: str | None = None
    customer_id: str
    items: list[WarehouseOrderItemCreate]
    created_by: str = "warehouse-agent"


class WarehouseOrderStatusUpdateRequest(BaseModel):
    updated_by: str = "warehouse-agent"


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
