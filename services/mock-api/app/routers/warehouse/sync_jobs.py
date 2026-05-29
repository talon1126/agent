from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from app.warehouse_store import WarehouseRepository

from .schemas import WarehouseInventorySyncJobUpdateRequest
from .state import WAREHOUSE_INVENTORY_SYNC_JOBS, get_warehouse_repository

router = APIRouter()


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



@router.get("/warehouse/inventory-sync-jobs")
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


@router.post("/warehouse/inventory-sync-jobs/{job_id}/complete")
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


@router.post("/warehouse/inventory-sync-jobs/{job_id}/fail")
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

