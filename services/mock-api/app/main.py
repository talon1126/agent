import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.store import FIXTURE_DIR, find_by_id
from app.routers.cart import CART_ITEMS, router as cart_router
from app.routers.delivery_addresses import DEFAULT_DELIVERY_ADDRESSES, router as delivery_addresses_router
from app.routers.delivery.router import router as delivery_router
from app.routers.flash_sales import router as flash_sales_router
from app.routers.delivery.state import DELIVERY_CASES
from app.routers.procurement.router import router as procurement_router
from app.routers.product_details import router as product_details_router
from app.routers.procurement.state import PURCHASE_ORDERS, REPLENISHMENT_REQUESTS
from app.routers.search import router as search_router
from app.routers.warehouse.router import router as warehouse_router
from app.routers.warehouse.state import (
    RECEIVED_INVENTORY_BATCHES,
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES,
    WAREHOUSE_INVENTORY_MOVEMENTS,
    WAREHOUSE_INVENTORY_SYNC_JOBS,
    WAREHOUSE_ORDER_ITEMS,
    WAREHOUSE_ORDERS,
    get_warehouse_repository,
)

app = FastAPI(title="Ecommerce Mock Enterprise API")
app.include_router(delivery_router)
app.include_router(procurement_router)
app.include_router(cart_router)
app.include_router(delivery_addresses_router)
app.include_router(flash_sales_router)
app.include_router(product_details_router)
app.include_router(search_router)
app.include_router(warehouse_router)

__all__ = [
    "app",
    "CART_ITEMS",
    "DEFAULT_DELIVERY_ADDRESSES",
    "RECEIVED_INVENTORY_BATCHES",
    "WAREHOUSE_BATCH_QUANTITY_OVERRIDES",
    "WAREHOUSE_INVENTORY_MOVEMENTS",
    "WAREHOUSE_INVENTORY_SYNC_JOBS",
    "WAREHOUSE_ORDER_ITEMS",
    "WAREHOUSE_ORDERS",
    "REPLENISHMENT_REQUESTS",
    "PURCHASE_ORDERS",
    "DELIVERY_CASES",
]

APPROVALS: list[dict] = []
TICKETS: list[dict] = []
RUN_LOGS: list[dict] = []
DEAD_LETTERS: list[dict] = []
REPLAYS: list[dict] = []
INTERNAL_NOTIFICATIONS: list[dict] = []


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
