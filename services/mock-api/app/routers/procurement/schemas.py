from pydantic import BaseModel


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
    status: str = "未审批"


class PurchaseOrderConfirmArrivalBatchRequest(BaseModel):
    purchase_order_ids: list[str]
    received_by: str = "warehouse"


class ReplenishmentRequestTableRowsRequest(BaseModel):
    status: str | None = None
    request_id: str | None = None
    limit: int = 100


class PurchaseOrderTableRowsRequest(BaseModel):
    request_id: str | None = None
    purchase_order_id: str | None = None
    warehouse_sync_status: str | None = None
    limit: int = 100
