from pathlib import Path

from app.main import app
from app.routers.procurement.router import router as procurement_router


def test_procurement_routes_are_registered_from_module_router() -> None:
    route_paths = {route.path for route in procurement_router.routes}

    assert "/procurement/mock" in route_paths
    assert "/procurement/purchase-orders" in route_paths
    assert "/procurement/purchase-orders/{purchase_order_id}/approve" in route_paths
    assert "/procurement/purchase-orders/{purchase_order_id}/reject" in route_paths
    assert "/procurement/purchase-orders/approve-batch" in route_paths
    assert "/procurement/purchase-orders/confirm-arrival-batch" in route_paths
    assert "/procurement/purchase-orders/table-rows" in route_paths
    assert not any(path.startswith("/procurement/replenishment-requests") for path in route_paths)

    app_paths = {route.path for route in app.routes}
    assert route_paths.issubset(app_paths)


def test_main_does_not_define_procurement_routes_directly() -> None:
    main_source = (Path(__file__).parents[1] / "app/main.py").read_text(encoding="utf-8")

    assert '@app.get("/procurement/' not in main_source
    assert '@app.post("/procurement/' not in main_source
