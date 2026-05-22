import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.store import FIXTURE_DIR, find_by_id, load_json
from app.warehouse_store import WarehouseRepository, create_warehouse_repository_from_env

app = FastAPI(title="Ecommerce Mock Enterprise API")

APPROVALS: list[dict] = []
TICKETS: list[dict] = []
RUN_LOGS: list[dict] = []
DEAD_LETTERS: list[dict] = []
REPLAYS: list[dict] = []
INTERNAL_NOTIFICATIONS: list[dict] = []
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


def load_locations_for_sku(sku: str) -> list[dict[str, Any]]:
    repository = get_warehouse_repository()
    if repository:
        return repository.list_locations_for_sku(sku)
    return [item for item in load_json("warehouse_locations.json") if item.get("sku") == sku]


def load_exceptions_for_sku(sku: str, status: str | None = None) -> list[dict[str, Any]]:
    repository = get_warehouse_repository()
    if repository:
        return repository.list_exceptions_for_sku(sku, status)
    records = [item for item in load_json("warehouse_exceptions.json") if item.get("sku") == sku]
    if status:
        records = [item for item in records if item.get("status") == status]
    return records


def load_inventory_for_sku(sku: str) -> dict[str, Any] | None:
    repository = get_warehouse_repository()
    if repository:
        return repository.get_inventory(sku)
    return find_by_id("inventory.json", "sku", sku)


def warehouse_risk_level(inventory: dict, open_exceptions: list[dict[str, Any]]) -> str:
    available = int(inventory.get("available", 0))
    reserved = int(inventory.get("reserved", 0))
    pending_orders = int(inventory.get("pending_orders", 0))
    reorder_threshold = int(inventory.get("reorder_threshold", 0))
    if any(item.get("severity") == "high" for item in open_exceptions):
        return "high"
    if available - reserved < pending_orders or available < reorder_threshold:
        return "high"
    if open_exceptions:
        return "medium"
    return "low"


@app.get("/warehouse/inventory/{sku}")
def get_warehouse_inventory(sku: str) -> dict:
    inventory = load_inventory_for_sku(sku)
    if not inventory:
        raise HTTPException(status_code=404, detail="inventory not found")
    locations = load_locations_for_sku(sku)
    open_exceptions = load_exceptions_for_sku(sku, "open")
    risk_level = warehouse_risk_level(inventory, open_exceptions)
    return {
        "ok": True,
        **inventory,
        "locations": locations,
        "open_exceptions": open_exceptions,
        "risk_level": risk_level,
        "recommendation": (
            "库存或异常存在履约风险，建议仓库复核并通知采购。"
            if risk_level == "high"
            else "库存状态正常，可继续履约。"
        ),
    }


@app.post("/warehouse/exceptions/search")
def search_warehouse_exceptions(payload: dict) -> dict:
    sku = str(payload.get("sku") or "").strip()
    status = str(payload.get("status") or "").strip() or None
    if not sku:
        return {"ok": False, "error": "missing_sku", "matches": []}
    matches = load_exceptions_for_sku(sku, status)
    return {"ok": True, "sku": sku, "status": status, "matches": matches}


@app.post("/warehouse/fulfillment/check")
def check_warehouse_fulfillment(payload: dict) -> dict:
    sku = str(payload.get("sku") or "").strip()
    if not sku:
        return {
            "ok": False,
            "error": "missing_sku",
            "can_ship": False,
            "blockers": ["missing_sku"],
        }

    inventory = load_inventory_for_sku(sku)
    if not inventory:
        return {
            "ok": False,
            "error": "inventory_not_found",
            "sku": sku,
            "can_ship": False,
            "blockers": ["inventory_not_found"],
        }

    locations = load_locations_for_sku(sku)
    open_exceptions = load_exceptions_for_sku(sku, "open")
    available = int(inventory.get("available", 0))
    reserved = int(inventory.get("reserved", 0))
    pending_orders = int(inventory.get("pending_orders", 0))
    blockers: list[str] = []
    if available - reserved < pending_orders:
        blockers.append("insufficient_available_stock")
    if not any(
        item.get("status") == "available" and int(item.get("quantity", 0)) > 0
        for item in locations
    ):
        blockers.append("missing_available_location")
    if any(item.get("severity") in {"high", "medium"} for item in open_exceptions):
        blockers.append("open_exception")

    can_ship = not blockers
    next_action = (
        "release_to_pick"
        if can_ship
        else ("notify_procurement" if "insufficient_available_stock" in blockers else "manual_review")
    )
    return {
        "ok": True,
        "sku": sku,
        "can_ship": can_ship,
        "blockers": blockers,
        "available": available,
        "reserved": reserved,
        "pending_orders": pending_orders,
        "locations": locations,
        "open_exceptions": open_exceptions,
        "next_action": next_action,
    }


@app.post("/procurement/mock")
def procurement_mock(payload: dict) -> dict:
    sku = str(payload.get("sku") or "").strip()
    inventory = load_inventory_for_sku(sku) if sku else None
    if not inventory:
        return {
            "ok": False,
            "system": "mock-procurement",
            "sku": sku,
            "recommendation": "request_valid_sku",
            "message": "未找到 SKU，需要提供有效 SKU。",
        }

    available = int(inventory.get("available", 0))
    pending_orders = int(inventory.get("pending_orders", 0))
    reorder_threshold = int(inventory.get("reorder_threshold", 0))
    should_replenish = available < reorder_threshold or available < pending_orders
    return {
        "ok": True,
        "system": "mock-procurement",
        "sku": sku,
        "available": available,
        "pending_orders": pending_orders,
        "reorder_threshold": reorder_threshold,
        "recommendation": "create_purchase_request" if should_replenish else "no_action",
        "message": "库存低于阈值，建议创建采购申请。" if should_replenish else "当前库存无需补货。",
    }


@app.post("/operations/summary/mock")
def operations_summary_mock(payload: dict) -> dict:
    query = str(payload.get("query") or payload.get("text") or "").strip()
    return {
        "ok": True,
        "system": "mock-operations",
        "query": query,
        "summary": "今日主要运营异常集中在低库存 SKU 和待跟进售后订单。",
        "incidents": [
            {
                "domain": "warehouse",
                "severity": "medium",
                "message": "sku_bag_1 可用库存低于补货阈值。",
            },
            {
                "domain": "customer_support",
                "severity": "low",
                "message": "退款咨询需要引用售后政策。",
            },
        ],
        "next_actions": ["检查低库存 SKU", "汇总客服退款问题", "确认采购补货计划"],
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
