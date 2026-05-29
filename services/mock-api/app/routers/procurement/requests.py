from typing import Any

from fastapi import APIRouter, HTTPException

from app.routers.warehouse.state import get_warehouse_repository

from .schemas import (
    ReplenishmentApproveBatchRequest,
    ReplenishmentApproveRequest,
    ReplenishmentRejectRequest,
    ReplenishmentRequestCreate,
    ReplenishmentRequestTableRowsRequest,
)
from .service import (
    PROCUREMENT_REPLENISHMENT_REQUEST_TABLE_SCHEMA,
    REPLENISHMENT_STATUS_UNAPPROVED,
    approve_replenishment_request_data,
    build_replenishment_request,
    find_replenishment_request,
    normalize_replenishment_status,
    procurement_replenishment_request_table_fields,
    update_replenishment_request_status,
)
from .state import REPLENISHMENT_REQUESTS

router = APIRouter()


@router.post("/procurement/replenishment-requests")
def create_replenishment_request(payload: ReplenishmentRequestCreate) -> dict[str, Any]:
    repository = get_warehouse_repository()
    request = build_replenishment_request(payload, repository)
    if repository:
        request = repository.create_replenishment_request(request)
    else:
        REPLENISHMENT_REQUESTS.append(request)
    return {"ok": True, "request": request}


@router.get("/procurement/replenishment-requests")
def list_replenishment_requests(status: str | None = None) -> dict[str, Any]:
    repository = get_warehouse_repository()
    normalized_status = normalize_replenishment_status(status)
    if repository:
        items = repository.list_replenishment_requests(status=normalized_status)
    else:
        items = [
            request
            for request in REPLENISHMENT_REQUESTS
            if not normalized_status or request["status"] == normalized_status
        ]
    return {"ok": True, "count": len(items), "items": items}


@router.post("/procurement/replenishment-requests/{request_id}/approve")
def approve_replenishment_request(
    request_id: str,
    payload: ReplenishmentApproveRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    request = find_replenishment_request(request_id, repository)
    if not request:
        raise HTTPException(status_code=404, detail="replenishment request not found")

    try:
        updated, order, _created = approve_replenishment_request_data(request, payload, repository)
    except ValueError as error:
        if str(error) == "default_supplier_not_found":
            raise HTTPException(status_code=400, detail="default supplier not found for item") from error
        raise
    return {"ok": True, "request": updated, "purchase_order": order}


@router.post("/procurement/replenishment-requests/approve-batch")
def approve_replenishment_requests_batch(
    payload: ReplenishmentApproveBatchRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    normalized_status = normalize_replenishment_status(payload.status)
    requests = (
        repository.list_replenishment_requests(status=normalized_status)
        if repository
        else [
            request
            for request in REPLENISHMENT_REQUESTS
            if request["status"] == normalized_status
        ]
    )
    approved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    approve_payload = ReplenishmentApproveRequest(created_by=payload.created_by)
    for request in requests:
        try:
            updated, order, created = approve_replenishment_request_data(
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
                "purchase_order_id": order["purchase_order_id"],
                "item_id": order["item_id"],
                "warehouse_id": order["warehouse_id"],
                "location_code": order.get("location_code") or "",
                "supplier_id": order["supplier_id"],
                "supplier_name": order["supplier_name"],
                "quantity": order["quantity"],
                "payment_status": order["payment_status"],
                "warehouse_sync_status": order["warehouse_sync_status"],
                "estimated_arrival_date": order["estimated_arrival_date"],
                "action": "created" if created else "reused",
            }
        )
    return {
        "ok": True,
        "status": normalized_status,
        "processed_count": len(requests),
        "approved_count": len(approved),
        "skipped_count": len(errors),
        "created_or_reused_orders": approved,
        "errors": errors,
    }


@router.post("/procurement/replenishment-requests/{request_id}/reject")
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
        status=REPLENISHMENT_STATUS_UNAPPROVED,
        reason=payload.reason,
        repository=repository,
    )
    return {"ok": True, "request": updated}


@router.get("/procurement/replenishment-requests/table-schema")
def get_procurement_replenishment_request_table_schema() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_id": "procurement_replenishment_requests",
        "source": "mock-api",
        "fields": PROCUREMENT_REPLENISHMENT_REQUEST_TABLE_SCHEMA,
    }


@router.post("/procurement/replenishment-requests/table-rows")
def get_procurement_replenishment_request_table_rows(
    payload: ReplenishmentRequestTableRowsRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    normalized_status = normalize_replenishment_status(payload.status)
    if repository:
        items = repository.list_replenishment_requests(status=normalized_status)
    else:
        items = [
            request
            for request in REPLENISHMENT_REQUESTS
            if not normalized_status or request["status"] == normalized_status
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
