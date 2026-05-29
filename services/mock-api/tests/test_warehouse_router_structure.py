from app.main import app
from app.routers.warehouse.router import router as warehouse_router


def test_warehouse_routes_are_registered_from_module_router() -> None:
    route_paths = {route.path for route in warehouse_router.routes}

    assert "/warehouse/inventory/{item_id}" in route_paths
    assert "/warehouse/orders/{order_id}/pay" in route_paths
    assert "/warehouse/purchase-orders/sync-arrivals" in route_paths
    assert "/warehouse/inventory-sync-jobs/{job_id}/complete" in route_paths

    app_paths = {route.path for route in app.routes}
    assert route_paths.issubset(app_paths)
