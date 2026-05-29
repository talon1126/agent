from typing import Any

from pydantic import BaseModel


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


class WarehousePurchaseOrderArrivalSyncRequest(BaseModel):
    processed_by: str = "warehouse-agent"
    limit: int = 50

