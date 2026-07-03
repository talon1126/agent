from fastapi import APIRouter

from .mock import router as mock_router
from .purchase_orders import router as purchase_orders_router

router = APIRouter()
router.include_router(mock_router)
router.include_router(purchase_orders_router)
