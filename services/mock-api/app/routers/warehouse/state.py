from pathlib import Path

from app.store import FIXTURE_DIR
from app.warehouse_store import WarehouseRepository, create_warehouse_repository_from_env

RECEIVED_INVENTORY_BATCHES: list[dict] = []
WAREHOUSE_INVENTORY_SYNC_JOBS: list[dict] = []
WAREHOUSE_BATCH_QUANTITY_OVERRIDES: dict[str, dict[str, int]] = {}
WAREHOUSE_ORDERS: list[dict] = []
WAREHOUSE_ORDER_ITEMS: list[dict] = []
WAREHOUSE_REPOSITORY: WarehouseRepository | None | bool = False


def get_warehouse_repository(
    fixture_dir: Path = FIXTURE_DIR,
) -> WarehouseRepository | None:
    global WAREHOUSE_REPOSITORY
    if WAREHOUSE_REPOSITORY is False:
        WAREHOUSE_REPOSITORY = create_warehouse_repository_from_env(fixture_dir)
    return WAREHOUSE_REPOSITORY if isinstance(WAREHOUSE_REPOSITORY, WarehouseRepository) else None
