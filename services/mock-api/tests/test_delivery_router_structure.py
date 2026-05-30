from app.main import app
from app.routers.delivery.router import router as delivery_router


def test_delivery_routes_are_registered_from_module_router() -> None:
    route_paths = {route.path for route in delivery_router.routes}

    assert "/delivery/providers" in route_paths
    assert "/delivery/status/lookup" in route_paths
    assert "/delivery/exceptions/search" in route_paths
    assert "/delivery/cases" in route_paths

    app_paths = {route.path for route in app.routes}
    assert route_paths.issubset(app_paths)
