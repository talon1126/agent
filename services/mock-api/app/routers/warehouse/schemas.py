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


class WarehouseStockBalanceTableRowsRequest(BaseModel):
    item_id: str | None = None
    warehouse_id: str | None = None
    location_code: str | None = None
    cursor: str | None = None
    limit: int = 500


class WarehouseInventorySyncJobUpdateRequest(BaseModel):
    processed_by: str = "warehouse-agent"
    result: dict[str, Any] | None = None
    error: str | None = None


class WarehouseOrderItemCreate(BaseModel):
    item_id: str
    warehouse_id: str | None = None
    quantity: int
    location_code: str | None = None


class WarehouseOrderCreate(BaseModel):
    order_id: str | None = None
    customer_id: str
    delivery_provider_id: str = "sf"
    courier_phone: str = ""
    tracking_no: str = ""
    shipping_address: str = ""
    items: list[WarehouseOrderItemCreate]
    created_by: str = "warehouse-agent"


class WarehouseOrderStatusUpdateRequest(BaseModel):
    updated_by: str = "warehouse-agent"


class WarehouseOrderFulfillmentConfirmRequest(BaseModel):
    warehouse_id: str
    delivery_provider_id: str | None = None
    courier_phone: str = ""
    tracking_no: str = ""
    updated_by: str = "warehouse-agent"


class WarehouseOrderReleaseExpiredRequest(BaseModel):
    processed_by: str = "warehouse-timeout-release"
    now: str | None = None
    limit: int = 100


class WarehousePurchaseOrderArrivalSyncRequest(BaseModel):
    processed_by: str = "warehouse-agent"
    limit: int = 50


class WarehousePurchaseOrderArrivalNotifyRequest(BaseModel):
    processed_by: str = "warehouse-arrival-notify"
    target_date: str | None = None
    chat_id: str = ""
    limit: int = 50

