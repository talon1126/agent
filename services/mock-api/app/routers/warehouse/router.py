from fastapi import APIRouter

from .inventory import router as inventory_router
from .orders import router as orders_router
from .purchase_orders import router as purchase_orders_router
from .sync_jobs import router as sync_jobs_router

router = APIRouter()
router.include_router(inventory_router)
router.include_router(orders_router)
router.include_router(purchase_orders_router)
router.include_router(sync_jobs_router)
