from pydantic import BaseModel


class PurchaseOrderCreateRequest(BaseModel):
    source: str = "warehouse"
    warehouse_id: str
    location_code: str | None = None
    item_id: str
    reason: str = "available_quantity_below_reorder_threshold"
    created_by: str = "warehouse"


class PurchaseOrderApproveRequest(BaseModel):
    created_by: str = "procurement"


class PurchaseOrderRejectRequest(BaseModel):
    reason: str = "procurement_rejected"
    updated_by: str = "procurement"


class PurchaseOrderApproveBatchRequest(BaseModel):
    created_by: str = "procurement"
    approval_status: str = "pending"


class PurchaseOrderConfirmArrivalBatchRequest(BaseModel):
    purchase_order_ids: list[str]
    received_by: str = "warehouse"


class PurchaseOrderTableRowsRequest(BaseModel):
    purchase_order_id: str | None = None
    approval_status: str | None = None
    warehouse_sync_status: str | None = None
    payment_status: str | None = None
    limit: int = 100
    offset: int = 0
