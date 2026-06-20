import base64
import hashlib
import hmac
import mimetypes
import os
import logging
import threading
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, field_validator, model_validator

from app.feishu_client import FEISHU_API_BASE_URL, get_tenant_access_token, reply_text_message, send_group_text_message
from app.feishu_events import normalize_feishu_event, to_n8n_payload
from app.feishu_long_connection import start_long_connection_listener
from app.intent_router import route_warehouse_intent
from app.view_template_builder import (
    load_warehouse_view_templates,
    match_warehouse_view_template,
    render_warehouse_view_plan,
)

DEFAULT_N8N_WEBHOOK_URL = "http://n8n:5678/webhook/chat-agent-inbound"
MAX_INVENTORY_RECORD_LOOKUP_FILTER_LENGTH = 900
logger = logging.getLogger("feishu_adapter")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(handler)


@dataclass(frozen=True)
class BotConfig:
    name: str
    app_id: str
    app_secret: str
    n8n_webhook_url: str
    api_base_url: str
    bot_open_id: str = ""
    enabled: bool = True


class InventoryTableSyncRequest(BaseModel):
    item_id: str
    warehouse_id: str | None = None
    location_code: str | None = None
    batch_no: str | None = None


class InventoryTableSyncFilterRequest(BaseModel):
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


class OrderFulfillmentReviewNotificationRequest(BaseModel):
    chat_id: str = ""
    order: dict[str, Any]
    items: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    delivery_providers: list[dict[str, Any]] = []


class PurchaseArrivalNotificationRequest(BaseModel):
    chat_id: str = ""
    target_date: str
    items: list[dict[str, Any]] = []


class InventoryTableSyncJobItem(BaseModel):
    job_id: str
    item_id: str
    warehouse_id: str | None = None
    location_code: str | None = None
    batch_no: str | None = None


class InventoryTableSyncJobsRequest(BaseModel):
    jobs: list[InventoryTableSyncJobItem]
    table_name: str = "Warehouse Inventory Snapshot"
    limit_per_job: int = 1


class InventoryTableProvisionRequest(BaseModel):
    table_name: str = "Warehouse Inventory Snapshot"


class InventoryBalancesTableProvisionRequest(BaseModel):
    table_name: str = "Warehouse Inventory Balances"


class InventoryBalancesTableSyncRequest(BaseModel):
    table_name: str = "Warehouse Inventory Balances"
    item_id: str | None = None
    warehouse_id: str | None = None
    location_code: str | None = None
    limit: int = 500
    max_pages: int = 50


class InventoryMovementsTableSyncRequest(BaseModel):
    table_name: str = "Warehouse Inventory Movements"
    order_id: str | None = None
    movement_type: str | None = None
    item_id: str | None = None
    warehouse_id: str | None = None
    limit: int = 500
    max_pages: int = 50


class ProcurementTableProvisionRequest(BaseModel):
    table_name: str | None = None


class ProcurementPurchaseOrderTableSyncRequest(BaseModel):
    purchase_order_id: str | None = None
    approval_status: str | None = None
    warehouse_sync_status: str | None = None
    payment_status: str | None = None
    limit: int = 100


class OrderFulfillmentTableSyncRequest(BaseModel):
    """Request payload for syncing the Order Fulfillment Feishu read model.

    Args:
        order_id: Optional business order id used for a single-row refresh.
        status: Optional order lifecycle status filter for scheduled syncs.
        limit: Maximum number of rows requested from mock-api.
    """

    order_id: str | None = None
    status: str | None = None
    limit: int = 100


class ProductOperationsTableSyncRequest(BaseModel):
    """Request payload for syncing the Product Operations Feishu read model.

    Args:
        category_id: Optional category id used by Feishu page filters.
        limit: Maximum number of product rows requested from mock-api.
    """

    category_id: str | None = None
    limit: int = 100


class OrderItemsTableSyncRequest(BaseModel):
    """Request payload for syncing the Order Items Feishu read model.

    Args:
        order_id: Optional order id filter for manual backfill or focused
            operator refresh.
        status: Optional order-line status filter passed through to mock-api.
        limit: Maximum number of order item rows requested from mock-api. The
            endpoint caps this value again before calling the shared sync path.
    """

    order_id: str | None = None
    status: str | None = None
    limit: int = 100


class ItemsTableSyncRequest(BaseModel):
    """Request payload for syncing the standalone Items Feishu read model.

    Args:
        category_id: Optional catalog category filter for Feishu application
            pages that need to refresh one department at a time.
        limit: Maximum number of product rows requested from mock-api.
    """

    category_id: str | None = None
    limit: int = 100


class FlashSalesTableSyncRequest(BaseModel):
    """Request payload for syncing the Flash Sales Feishu read model.

    Args:
        status: Optional activity status filter such as active, paused, or
            ended.
        limit: Maximum number of flash-sale rows requested from mock-api.
    """

    status: str | None = None
    limit: int = 100


class FlashSaleClaimsTableSyncRequest(BaseModel):
    """Request payload for syncing the Flash Sale Claims Feishu read model.

    Args:
        flash_sale_id: Optional activity id for focused claim-result refreshes.
        status: Optional claim status filter such as ordered or failed.
        limit: Maximum number of claim rows requested from mock-api.
    """

    flash_sale_id: int | None = None
    status: str | None = None
    limit: int = 100


class InventoryTableViewFilter(BaseModel):
    field: str
    operator: str = "is"
    value: Any | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_filter(cls, value: Any) -> Any:
        if isinstance(value, dict):
            normalized = dict(value)
            normalized["operator"] = str(normalized.get("operator") or "is")
            return normalized
        if isinstance(value, str):
            if "=" in value:
                field, filter_value = value.split("=", 1)
                return {"field": field.strip(), "operator": "is", "value": filter_value.strip()}
            return {"field": value.strip(), "operator": "is", "value": None}
        return value


class InventoryTableViewSort(BaseModel):
    field: str
    order: str = "asc"

    @model_validator(mode="before")
    @classmethod
    def normalize_sort(cls, value: Any) -> Any:
        if isinstance(value, dict):
            normalized = dict(value)
            normalized["order"] = str(normalized.get("order") or normalized.get("direction") or "asc")
            return normalized
        if isinstance(value, str):
            parts = value.rsplit(" ", 1)
            if len(parts) == 2 and parts[1].lower() in {"asc", "desc"}:
                return {"field": parts[0].strip(), "order": parts[1].lower()}
            return {"field": value.strip(), "order": "asc"}
        return value


class InventoryTableViewCreateRequest(BaseModel):
    view_name: str
    table_name: str = "Warehouse Inventory Snapshot"
    view_type: str = "grid"
    visible_fields: list[str] = []
    filters: list[InventoryTableViewFilter] = []
    sorts: list[InventoryTableViewSort] = []

    @field_validator("visible_fields", mode="before")
    @classmethod
    def normalize_visible_fields(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [field.strip() for field in value.replace("，", ",").split(",") if field.strip()]
        return value

    @field_validator("filters", mode="before")
    @classmethod
    def normalize_filters(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, dict):
            if "field" in value:
                return [value]
            return [
                {"field": str(field), "operator": "is", "value": filter_value}
                for field, filter_value in value.items()
            ]
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value

    @field_validator("sorts", mode="before")
    @classmethod
    def normalize_sorts(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value


class InventoryTableViewFromTemplateRequest(BaseModel):
    message: str
    view_name: str | None = None


class WarehouseIntentRouteRequest(BaseModel):
    message: str


INVENTORY_TABLE_FIELD_SPECS = [
    {"field_name": "Warehouse", "type": 1},
    {"field_name": "Warehouse ID", "type": 1},
    {"field_name": "Location", "type": 1},
    {"field_name": "Category", "type": 1},
    {"field_name": "Category ID", "type": 1},
    {"field_name": "Item ID", "type": 1},
    {"field_name": "Item Name", "type": 1},
    {"field_name": "Brand", "type": 1},
    {"field_name": "Spec", "type": 1},
    {"field_name": "Unit", "type": 1},
    {"field_name": "Batch No", "type": 1},
    {"field_name": "Quantity On Hand", "type": 2},
    {"field_name": "Quantity Available", "type": 2},
    {"field_name": "Quantity Reserved", "type": 2},
    {"field_name": "Reorder Threshold", "type": 2},
    {"field_name": "Production Date", "type": 1},
    {"field_name": "Expiry Date", "type": 1},
    {"field_name": "Days To Expiry", "type": 2},
    {
        "field_name": "Expiry Risk",
        "type": 3,
        "property": {
            "options": [
                {"name": "normal", "color": 28},
                {"name": "expiring_soon", "color": 24},
                {"name": "expired", "color": 17},
            ]
        },
    },
    {
        "field_name": "Risk Level",
        "type": 3,
        "property": {
            "options": [
                {"name": "low", "color": 28},
                {"name": "medium", "color": 24},
                {"name": "high", "color": 17},
                {"name": "unknown", "color": 0},
            ]
        },
    },
    {
        "field_name": "Storage Status",
        "type": 3,
        "property": {
            "options": [
                {"name": "available", "color": 28},
                {"name": "quality_hold", "color": 17},
            ]
        },
    },
    {"field_name": "Recommendation", "type": 1},
    {"field_name": "Last Synced At", "type": 1},
    {
        "field_name": "Sync Status",
        "type": 3,
        "property": {
            "options": [
                {"name": "synced", "color": 28},
                {"name": "pending", "color": 24},
                {"name": "failed", "color": 17},
            ]
        },
    },
    {"field_name": "Source Version", "type": 1},
]
DEFAULT_INVENTORY_TABLE_NAME = "Warehouse Inventory Snapshot"
DEFAULT_INVENTORY_TABLE_ALIASES = ["库存表", DEFAULT_INVENTORY_TABLE_NAME]
DEFAULT_INVENTORY_BALANCE_TABLE_NAME = "Warehouse Inventory Balances"
DEFAULT_INVENTORY_BALANCE_TABLE_ALIASES = ["库存余额表", DEFAULT_INVENTORY_BALANCE_TABLE_NAME]
DEFAULT_INVENTORY_MOVEMENT_TABLE_NAME = "Warehouse Inventory Movements"


def load_feishu_table_state(state_path: str) -> dict[str, dict[str, str]]:
    """Load durable Feishu Bitable identifiers from a local JSON state file.

    The adapter can create Feishu tables automatically when the operator has
    not configured table ids through environment variables. Created table ids
    must survive process restarts, otherwise a renamed Feishu table will no
    longer be found by name and the next sync will create a duplicate table.

    Args:
        state_path: Absolute or relative JSON file path from
            `FEISHU_TABLE_STATE_PATH`. An empty value disables durable state and
            keeps the adapter compatible with tests or deployments that manage
            table ids only through environment variables.

    Returns:
        A mapping keyed by internal table family. Each value contains string
        fields such as `table_id`, `view_id`, and `table_url`. Invalid or
        missing files return an empty mapping so the sync path can fall back to
        normal table discovery.
    """

    if not state_path:
        return {}
    path = Path(state_path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("failed to load Feishu table state from %s: %s", path, error)
        return {}
    if not isinstance(payload, dict):
        return {}
    state: dict[str, dict[str, str]] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        state[key] = {
            field: str(raw_value)
            for field, raw_value in value.items()
            if field in {"table_id", "view_id", "table_url"} and raw_value is not None
        }
    return state


def save_feishu_table_state(state_path: str, state: dict[str, dict[str, str]]) -> None:
    """Persist Feishu Bitable identifiers with an atomic replace operation.

    Args:
        state_path: JSON file path from `FEISHU_TABLE_STATE_PATH`.
        state: Complete durable table-state mapping to write.

    Side Effects:
        Creates the parent directory when needed and replaces the previous JSON
        file. Write failures are logged by callers so table syncs can continue
        even when local state persistence is temporarily unavailable.
    """

    if not state_path:
        return
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp_path.replace(path)


def load_feishu_image_token_cache(cache_path: str) -> dict[str, dict[str, str]]:
    """Load persisted Feishu image tokens keyed by source image URL.

    Feishu file uploads are rate-limited and relatively expensive. Product
    table syncs may run every few minutes, so the adapter keeps the last known
    content hash and Feishu `file_token` for each source image URL in a local
    JSON file. A missing or malformed cache degrades to an empty mapping because
    image upload failures must not block business table synchronization.

    Args:
        cache_path: JSON path from `FEISHU_IMAGE_TOKEN_CACHE_PATH`. An empty
            path disables durable cache persistence while still allowing in
            memory reuse during the current process.

    Returns:
        A mapping of image URL to cache entries containing `content_hash` and
        `file_token` strings.
    """

    if not cache_path:
        return {}
    path = Path(cache_path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("failed to load Feishu image token cache from %s: %s", path, error)
        return {}
    if not isinstance(payload, dict):
        return {}
    cache: dict[str, dict[str, str]] = {}
    for image_url, value in payload.items():
        if not isinstance(image_url, str) or not isinstance(value, dict):
            continue
        file_token = str(value.get("file_token") or "").strip()
        content_hash = str(value.get("content_hash") or "").strip()
        if image_url and file_token and content_hash:
            cache[image_url] = {
                "content_hash": content_hash,
                "file_token": file_token,
            }
    return cache


def save_feishu_image_token_cache(cache_path: str, cache: dict[str, dict[str, str]]) -> None:
    """Persist the Feishu image-token cache without interrupting table syncs.

    Args:
        cache_path: JSON path from `FEISHU_IMAGE_TOKEN_CACHE_PATH`. An empty
            path intentionally disables disk writes.
        cache: Complete in-memory cache to persist.

    Side Effects:
        Creates the parent directory and atomically replaces the JSON cache
        file. Callers catch any `OSError` so a local disk problem cannot make a
        Feishu table sync fail after the row itself was already uploaded.
    """

    if not cache_path:
        return
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _clean_bot_name(value: str) -> str:
    name = value.strip().lower().replace("-", "_")
    return name or "default"


def parse_bot_configs(
    *,
    bots_json: str | None,
    fallback_webhook_url: str,
    fallback_app_id: str,
    fallback_app_secret: str,
    api_base_url: str,
) -> list[BotConfig]:
    if not bots_json:
        return [
            BotConfig(
                name="default",
                app_id=fallback_app_id,
                app_secret=fallback_app_secret,
                n8n_webhook_url=fallback_webhook_url,
                api_base_url=api_base_url,
            )
        ]

    try:
        raw_bots = json.loads(bots_json)
    except json.JSONDecodeError as error:
        raise ValueError("FEISHU_BOTS_JSON must be valid JSON") from error
    if not isinstance(raw_bots, list):
        raise ValueError("FEISHU_BOTS_JSON must be a JSON array")

    bots: list[BotConfig] = []
    names: set[str] = set()
    for raw_bot in raw_bots:
        if not isinstance(raw_bot, dict):
            raise ValueError("Each Feishu bot config must be an object")
        if raw_bot.get("enabled", True) is False:
            continue
        name = _clean_bot_name(str(raw_bot.get("name") or ""))
        if name in names:
            raise ValueError(f"Duplicate Feishu bot name: {name}")
        names.add(name)
        app_id = str(raw_bot.get("app_id") or "").strip()
        app_secret = str(raw_bot.get("app_secret") or "").strip()
        webhook_url = str(raw_bot.get("n8n_webhook_url") or "").strip()
        if not app_id or not app_secret or not webhook_url:
            raise ValueError(f"Feishu bot {name} requires app_id, app_secret, and n8n_webhook_url")
        bots.append(
            BotConfig(
                name=name,
                app_id=app_id,
                app_secret=app_secret,
                n8n_webhook_url=webhook_url,
                api_base_url=str(raw_bot.get("api_base_url") or api_base_url).strip(),
                bot_open_id=str(raw_bot.get("bot_open_id") or "").strip(),
            )
        )
    if not bots:
        raise ValueError("FEISHU_BOTS_JSON did not include any enabled bots")
    return bots


def describe_http_error(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        try:
            payload = error.response.json()
        except ValueError:
            return str(error)
        return f"{error}; response={payload}"
    return str(error)


def create_app(
    *,
    http_client: httpx.Client | None = None,
    n8n_webhook_url: str | None = None,
    feishu_app_id: str | None = None,
    feishu_app_secret: str | None = None,
    feishu_api_base_url: str | None = None,
    feishu_event_mode: str | None = None,
    feishu_bots_json: str | None = None,
    run_log_url: str | None = None,
    mock_api_url: str | None = None,
    inventory_table_app_id: str | None = None,
    inventory_table_app_secret: str | None = None,
    inventory_table_app_token: str | None = None,
    inventory_table_id: str | None = None,
    inventory_table_view_id: str | None = None,
    inventory_table_url: str | None = None,
    inventory_balance_table_id: str | None = None,
    inventory_balance_table_view_id: str | None = None,
    inventory_balance_table_url: str | None = None,
    inventory_movement_table_id: str | None = None,
    inventory_movement_table_view_id: str | None = None,
    inventory_movement_table_url: str | None = None,
    procurement_purchase_order_table_id: str | None = None,
    procurement_purchase_order_table_view_id: str | None = None,
    procurement_purchase_order_table_url: str | None = None,
    procurement_purchase_order_draft_table_id: str | None = None,
    procurement_purchase_order_draft_table_view_id: str | None = None,
    procurement_purchase_order_draft_table_url: str | None = None,
    order_fulfillment_table_id: str | None = None,
    order_fulfillment_table_view_id: str | None = None,
    order_fulfillment_table_url: str | None = None,
    order_items_table_id: str | None = None,
    order_items_table_view_id: str | None = None,
    order_items_table_url: str | None = None,
    items_table_id: str | None = None,
    items_table_view_id: str | None = None,
    items_table_url: str | None = None,
    product_operations_table_id: str | None = None,
    product_operations_table_view_id: str | None = None,
    product_operations_table_url: str | None = None,
    flash_sales_table_id: str | None = None,
    flash_sales_table_view_id: str | None = None,
    flash_sales_table_url: str | None = None,
    flash_sale_claims_table_id: str | None = None,
    flash_sale_claims_table_view_id: str | None = None,
    flash_sale_claims_table_url: str | None = None,
    long_connection_starter: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="Feishu Adapter")
    timeout_seconds = float(os.getenv("FEISHU_ADAPTER_HTTP_TIMEOUT_SECONDS", "90"))
    client = http_client or httpx.Client(timeout=timeout_seconds)
    webhook_url = n8n_webhook_url or os.getenv("N8N_CHAT_WEBHOOK_URL", DEFAULT_N8N_WEBHOOK_URL)
    app_id = feishu_app_id if feishu_app_id is not None else os.getenv("FEISHU_APP_ID", "")
    app_secret = (
        feishu_app_secret if feishu_app_secret is not None else os.getenv("FEISHU_APP_SECRET", "")
    )
    api_base_url = feishu_api_base_url or os.getenv("FEISHU_API_BASE_URL", FEISHU_API_BASE_URL)
    event_mode = feishu_event_mode or os.getenv("FEISHU_EVENT_MODE", "http")
    bots_json = feishu_bots_json if feishu_bots_json is not None else os.getenv("FEISHU_BOTS_JSON", "")
    runtime_run_log_url = run_log_url if run_log_url is not None else os.getenv("FEISHU_RUN_LOG_URL", "")
    fulfillment_review_chat_id = os.getenv("FEISHU_FULFILLMENT_REVIEW_CHAT_ID", "").strip()
    purchase_arrival_chat_id = os.getenv(
        "FEISHU_PURCHASE_ARRIVAL_NOTIFY_CHAT_ID",
        fulfillment_review_chat_id,
    ).strip()
    runtime_mock_api_url = (mock_api_url if mock_api_url is not None else os.getenv("MOCK_API_URL", "http://mock-api:8000")).rstrip("/")
    table_app_id = inventory_table_app_id if inventory_table_app_id is not None else os.getenv("FEISHU_INVENTORY_TABLE_APP_ID", app_id)
    table_app_secret = (
        inventory_table_app_secret
        if inventory_table_app_secret is not None
        else os.getenv("FEISHU_INVENTORY_TABLE_APP_SECRET", app_secret)
    )
    table_app_token = (
        inventory_table_app_token
        if inventory_table_app_token is not None
        else os.getenv("FEISHU_INVENTORY_TABLE_APP_TOKEN", "")
    )
    table_state_path = os.getenv("FEISHU_TABLE_STATE_PATH", "").strip()
    durable_table_state = load_feishu_table_state(table_state_path)
    durable_table_state_lock = threading.RLock()
    image_token_cache_path = os.getenv("FEISHU_IMAGE_TOKEN_CACHE_PATH", "").strip()
    image_token_cache = load_feishu_image_token_cache(image_token_cache_path)
    image_token_cache_lock = threading.RLock()
    aliyun_oss_access_key_id = os.getenv("ALIYUN_OSS_ACCESS_KEY_ID", "").strip()
    aliyun_oss_access_key_secret = os.getenv("ALIYUN_OSS_ACCESS_KEY_SECRET", "").strip()
    aliyun_oss_endpoint = os.getenv("ALIYUN_OSS_ENDPOINT", "").strip()
    aliyun_oss_bucket = os.getenv("ALIYUN_OSS_BUCKET", "").strip()

    def table_state_value(
        *,
        explicit_value: str | None,
        env_name: str,
        state_key: str,
        field_name: str,
        fallback_env_names: tuple[str, ...] = (),
    ) -> str:
        """Resolve one table-state value from explicit config, env, or disk.

        Args:
            explicit_value: Optional value passed directly to `create_app` by a
                test or an embedding application.
            env_name: Environment variable name used by Docker/.env driven
                deployments.
            state_key: Durable JSON state key for the Feishu table family.
            field_name: Field inside that durable state entry.
            fallback_env_names: Compatibility environment variable names that
                should still override durable state when a legacy deployment
                has not migrated to the current variable name.

        Returns:
            The first configured string in priority order. Explicit values and
            environment variables intentionally override durable state so
            operators can correct a bad stored table id without editing JSON.
        """

        if explicit_value is not None:
            return explicit_value
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            return env_value
        for fallback_env_name in fallback_env_names:
            fallback_env_value = os.getenv(fallback_env_name, "").strip()
            if fallback_env_value:
                return fallback_env_value
        return str(durable_table_state.get(state_key, {}).get(field_name) or "")

    table_id = table_state_value(
        explicit_value=inventory_table_id,
        env_name="FEISHU_INVENTORY_TABLE_ID",
        state_key="inventory_table",
        field_name="table_id",
    )
    table_view_id = table_state_value(
        explicit_value=inventory_table_view_id,
        env_name="FEISHU_INVENTORY_TABLE_VIEW_ID",
        state_key="inventory_table",
        field_name="view_id",
    )
    table_url = inventory_table_url if inventory_table_url is not None else os.getenv("FEISHU_INVENTORY_TABLE_URL", "")
    inventory_table_state = {
        "_state_key": "inventory_table",
        "table_id": table_id,
        "view_id": table_view_id,
    }
    inventory_balance_table_state = {
        "_state_key": "inventory_balance_table",
        "table_id": table_state_value(
            explicit_value=inventory_balance_table_id,
            env_name="FEISHU_INVENTORY_BALANCE_TABLE_ID",
            state_key="inventory_balance_table",
            field_name="table_id",
        ),
        "view_id": table_state_value(
            explicit_value=inventory_balance_table_view_id,
            env_name="FEISHU_INVENTORY_BALANCE_TABLE_VIEW_ID",
            state_key="inventory_balance_table",
            field_name="view_id",
        ),
        "table_url": table_state_value(
            explicit_value=inventory_balance_table_url,
            env_name="FEISHU_INVENTORY_BALANCE_TABLE_URL",
            state_key="inventory_balance_table",
            field_name="table_url",
        ),
    }
    inventory_movement_table_state = {
        "_state_key": "inventory_movement_table",
        "table_id": table_state_value(
            explicit_value=inventory_movement_table_id,
            env_name="FEISHU_INVENTORY_MOVEMENT_TABLE_ID",
            state_key="inventory_movement_table",
            field_name="table_id",
        ),
        "view_id": table_state_value(
            explicit_value=inventory_movement_table_view_id,
            env_name="FEISHU_INVENTORY_MOVEMENT_TABLE_VIEW_ID",
            state_key="inventory_movement_table",
            field_name="view_id",
        ),
        "table_url": table_state_value(
            explicit_value=inventory_movement_table_url,
            env_name="FEISHU_INVENTORY_MOVEMENT_TABLE_URL",
            state_key="inventory_movement_table",
            field_name="table_url",
        ),
    }
    inventory_table_lock = threading.RLock()
    procurement_purchase_order_table_state = {
        "_state_key": "procurement_purchase_order_table",
        "table_id": (
            procurement_purchase_order_table_id
            if procurement_purchase_order_table_id is not None
            else (
                procurement_purchase_order_draft_table_id
                if procurement_purchase_order_draft_table_id is not None
                else table_state_value(
                    explicit_value=None,
                    env_name="FEISHU_PROCUREMENT_PURCHASE_ORDER_TABLE_ID",
                    state_key="procurement_purchase_order_table",
                    field_name="table_id",
                    fallback_env_names=("FEISHU_PROCUREMENT_PURCHASE_ORDER_DRAFT_TABLE_ID",),
                )
            )
        ),
        "view_id": (
            procurement_purchase_order_table_view_id
            if procurement_purchase_order_table_view_id is not None
            else (
                procurement_purchase_order_draft_table_view_id
                if procurement_purchase_order_draft_table_view_id is not None
                else table_state_value(
                    explicit_value=None,
                    env_name="FEISHU_PROCUREMENT_PURCHASE_ORDER_TABLE_VIEW_ID",
                    state_key="procurement_purchase_order_table",
                    field_name="view_id",
                    fallback_env_names=("FEISHU_PROCUREMENT_PURCHASE_ORDER_DRAFT_TABLE_VIEW_ID",),
                )
            )
        ),
        "table_url": (
            procurement_purchase_order_table_url
            if procurement_purchase_order_table_url is not None
            else (
                procurement_purchase_order_draft_table_url
                if procurement_purchase_order_draft_table_url is not None
                else table_state_value(
                    explicit_value=None,
                    env_name="FEISHU_PROCUREMENT_PURCHASE_ORDER_TABLE_URL",
                    state_key="procurement_purchase_order_table",
                    field_name="table_url",
                    fallback_env_names=("FEISHU_PROCUREMENT_PURCHASE_ORDER_DRAFT_TABLE_URL",),
                )
            )
        ),
    }
    order_fulfillment_table_state = {
        "_state_key": "order_fulfillment_table",
        "table_id": table_state_value(
            explicit_value=order_fulfillment_table_id,
            env_name="FEISHU_ORDER_FULFILLMENT_TABLE_ID",
            state_key="order_fulfillment_table",
            field_name="table_id",
        ),
        "view_id": table_state_value(
            explicit_value=order_fulfillment_table_view_id,
            env_name="FEISHU_ORDER_FULFILLMENT_TABLE_VIEW_ID",
            state_key="order_fulfillment_table",
            field_name="view_id",
        ),
        "table_url": table_state_value(
            explicit_value=order_fulfillment_table_url,
            env_name="FEISHU_ORDER_FULFILLMENT_TABLE_URL",
            state_key="order_fulfillment_table",
            field_name="table_url",
        ),
    }
    order_items_table_state = {
        "_state_key": "order_items_table",
        "table_id": table_state_value(
            explicit_value=order_items_table_id,
            env_name="FEISHU_ORDER_ITEMS_TABLE_ID",
            state_key="order_items_table",
            field_name="table_id",
        ),
        "view_id": table_state_value(
            explicit_value=order_items_table_view_id,
            env_name="FEISHU_ORDER_ITEMS_TABLE_VIEW_ID",
            state_key="order_items_table",
            field_name="view_id",
        ),
        "table_url": table_state_value(
            explicit_value=order_items_table_url,
            env_name="FEISHU_ORDER_ITEMS_TABLE_URL",
            state_key="order_items_table",
            field_name="table_url",
        ),
    }
    items_table_state = {
        "_state_key": "items_table",
        "table_id": table_state_value(
            explicit_value=items_table_id,
            env_name="FEISHU_ITEMS_TABLE_ID",
            state_key="items_table",
            field_name="table_id",
        ),
        "view_id": table_state_value(
            explicit_value=items_table_view_id,
            env_name="FEISHU_ITEMS_TABLE_VIEW_ID",
            state_key="items_table",
            field_name="view_id",
        ),
        "table_url": table_state_value(
            explicit_value=items_table_url,
            env_name="FEISHU_ITEMS_TABLE_URL",
            state_key="items_table",
            field_name="table_url",
        ),
    }
    product_operations_table_state = {
        "_state_key": "product_operations_table",
        "table_id": table_state_value(
            explicit_value=product_operations_table_id,
            env_name="FEISHU_PRODUCT_OPERATIONS_TABLE_ID",
            state_key="product_operations_table",
            field_name="table_id",
        ),
        "view_id": table_state_value(
            explicit_value=product_operations_table_view_id,
            env_name="FEISHU_PRODUCT_OPERATIONS_TABLE_VIEW_ID",
            state_key="product_operations_table",
            field_name="view_id",
        ),
        "table_url": table_state_value(
            explicit_value=product_operations_table_url,
            env_name="FEISHU_PRODUCT_OPERATIONS_TABLE_URL",
            state_key="product_operations_table",
            field_name="table_url",
        ),
    }
    flash_sales_table_state = {
        "_state_key": "flash_sales_table",
        "table_id": table_state_value(
            explicit_value=flash_sales_table_id,
            env_name="FEISHU_FLASH_SALES_TABLE_ID",
            state_key="flash_sales_table",
            field_name="table_id",
        ),
        "view_id": table_state_value(
            explicit_value=flash_sales_table_view_id,
            env_name="FEISHU_FLASH_SALES_TABLE_VIEW_ID",
            state_key="flash_sales_table",
            field_name="view_id",
        ),
        "table_url": table_state_value(
            explicit_value=flash_sales_table_url,
            env_name="FEISHU_FLASH_SALES_TABLE_URL",
            state_key="flash_sales_table",
            field_name="table_url",
        ),
    }
    flash_sale_claims_table_state = {
        "_state_key": "flash_sale_claims_table",
        "table_id": table_state_value(
            explicit_value=flash_sale_claims_table_id,
            env_name="FEISHU_FLASH_SALE_CLAIMS_TABLE_ID",
            state_key="flash_sale_claims_table",
            field_name="table_id",
        ),
        "view_id": table_state_value(
            explicit_value=flash_sale_claims_table_view_id,
            env_name="FEISHU_FLASH_SALE_CLAIMS_TABLE_VIEW_ID",
            state_key="flash_sale_claims_table",
            field_name="view_id",
        ),
        "table_url": table_state_value(
            explicit_value=flash_sale_claims_table_url,
            env_name="FEISHU_FLASH_SALE_CLAIMS_TABLE_URL",
            state_key="flash_sale_claims_table",
            field_name="table_url",
        ),
    }
    bot_configs = parse_bot_configs(
        bots_json=bots_json,
        fallback_webhook_url=webhook_url,
        fallback_app_id=app_id,
        fallback_app_secret=app_secret,
        api_base_url=api_base_url,
    )
    start_listener = long_connection_starter or start_long_connection_listener
    processed_message_ids: set[str] = set()
    processed_message_ids_lock = threading.Lock()
    listener_count = 0

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/details")
    def health_details() -> dict[str, Any]:
        with processed_message_ids_lock:
            processed_count = len(processed_message_ids)
        return {
            "status": "ok",
            "event_mode": event_mode,
            "bot_count": len(bot_configs),
            "listener_count": getattr(app.state, "feishu_listener_count", listener_count),
            "run_log_enabled": bool(runtime_run_log_url),
            "processed_message_count": processed_count,
            "bots": [
                {
                    "name": bot.name,
                    "n8n_webhook_url": bot.n8n_webhook_url,
                    "n8n_webhook_status": "configured" if bot.n8n_webhook_url else "missing",
                    "has_app_id": bool(bot.app_id),
                    "has_app_secret": bool(bot.app_secret),
                    "has_bot_open_id": bool(bot.bot_open_id),
                }
                for bot in bot_configs
            ],
        }

    def build_order_fulfillment_review_text(payload: OrderFulfillmentReviewNotificationRequest) -> str:
        order = payload.order
        order_id = str(order.get("order_id") or "")
        shipping_city = str(order.get("shipping_city") or order.get("shipping_address") or "")
        selected_warehouse_id = str(order.get("selected_warehouse_id") or "")
        item_lines = [
            f"- {item.get('item_name') or item.get('item_id')} x {int(item.get('quantity') or 0)}"
            for item in payload.items
        ]
        candidate_lines = []
        for candidate in payload.candidates:
            status = "可发仓" if candidate.get("can_fulfill") else "库存不足"
            shortage = candidate.get("shortage") if isinstance(candidate.get("shortage"), dict) else {}
            shortage_text = (
                f"，缺口 {shortage.get('shortage_quantity')}"
                if shortage.get("shortage_quantity") is not None
                else ""
            )
            candidate_lines.append(
                "- "
                f"{candidate.get('warehouse_name') or candidate.get('warehouse_id')} "
                f"({candidate.get('warehouse_id')}): {status}{shortage_text}"
            )
        delivery_lines = [
            "- "
            f"{provider.get('name') or provider.get('provider_name') or provider.get('provider_id')} "
            f"({provider.get('provider_id')}): {provider.get('service_hotline') or '-'}"
            for provider in payload.delivery_providers
        ]
        return "\n".join(
            [
                "订单发仓确认",
                f"订单编号: {order_id}",
                f"收货城市: {shipping_city or '-'}",
                f"推荐发仓: {selected_warehouse_id or '-'}",
                "商品明细:",
                *(item_lines or ["- 无商品明细"]),
                "候选发仓:",
                *(candidate_lines or ["- 无候选仓"]),
                "物流选项:",
                *(delivery_lines or ["- 默认物流: sf"]),
                f"确认发仓: @warehouse 确认发仓 {order_id} {selected_warehouse_id or '<warehouse_id>'} 物流 <delivery_provider_id>",
            ]
        )

    def build_purchase_arrival_review_text(payload: PurchaseArrivalNotificationRequest) -> str:
        item_lines = [
            "- "
            f"{item.get('purchase_order_id')}: {item.get('item_id')} x {int(item.get('quantity') or 0)} | "
            f"{item.get('warehouse_name') or item.get('warehouse_id')} / {item.get('location_code') or '-'} | "
            f"预计到货 {item.get('estimated_arrival_date') or payload.target_date}"
            for item in payload.items
        ]
        purchase_order_ids = [
            str(item.get("purchase_order_id") or "").strip()
            for item in payload.items
            if str(item.get("purchase_order_id") or "").strip()
        ]
        return "\n".join(
            [
                "采购到货入库确认",
                f"目标日期: {payload.target_date}",
                "到货采购单:",
                *(item_lines or ["- 今日没有待确认入库采购单"]),
                "确认全部入库:",
                f"@procurement 确认采购到货 {' '.join(purchase_order_ids) if purchase_order_ids else '<purchase_order_id>'}",
                "确认部分入库:",
                "@procurement 确认采购到货 <purchase_order_id> [<purchase_order_id>...]",
            ]
        )

    def select_bot_config_by_name(name: str) -> BotConfig | None:
        """Return the enabled bot configuration matching a logical bot name.

        The adapter supports multiple Feishu bots in one process through
        FEISHU_BOTS_JSON. Proactive warehouse notifications should therefore
        use the warehouse bot credentials instead of the legacy top-level
        FEISHU_APP_ID/FEISHU_APP_SECRET pair.
        """
        normalized_name = name.strip().lower()
        for bot in bot_configs:
            if bot.enabled and bot.name.strip().lower() == normalized_name:
                return bot
        return None

    def fulfillment_review_credentials() -> tuple[str, str]:
        """Resolve Feishu credentials for proactive fulfillment review messages.

        Warehouse review messages are operational warehouse events, so the
        warehouse bot is the preferred identity. The legacy top-level app
        credentials remain as a fallback for older single-bot deployments.
        """
        warehouse_bot = select_bot_config_by_name("warehouse")
        if warehouse_bot and warehouse_bot.app_id and warehouse_bot.app_secret:
            return warehouse_bot.app_id, warehouse_bot.app_secret
        if app_id and app_secret:
            return app_id, app_secret
        raise HTTPException(status_code=503, detail="missing_feishu_app_credentials")

    @app.post("/warehouse/order-fulfillment-review/send")
    def send_order_fulfillment_review_message(
        payload: OrderFulfillmentReviewNotificationRequest,
    ) -> dict[str, Any]:
        chat_id = (payload.chat_id or fulfillment_review_chat_id).strip()
        if not chat_id:
            raise HTTPException(status_code=400, detail="missing_fulfillment_review_chat_id")
        credential_app_id, credential_app_secret = fulfillment_review_credentials()
        token = get_tenant_access_token(
            client=client,
            app_id=credential_app_id,
            app_secret=credential_app_secret,
            api_base_url=api_base_url,
        )
        text = build_order_fulfillment_review_text(payload)
        message_id = send_group_text_message(
            client=client,
            tenant_access_token=token,
            chat_id=chat_id,
            text=text,
            api_base_url=api_base_url,
        )
        return {"ok": True, "chat_id": chat_id, "message_id": message_id, "text": text}

    @app.post("/warehouse/purchase-arrival-review/send")
    def send_purchase_arrival_review_message(
        payload: PurchaseArrivalNotificationRequest,
    ) -> dict[str, Any]:
        chat_id = (payload.chat_id or purchase_arrival_chat_id).strip()
        if not chat_id:
            raise HTTPException(status_code=400, detail="missing_purchase_arrival_chat_id")
        credential_app_id, credential_app_secret = fulfillment_review_credentials()
        token = get_tenant_access_token(
            client=client,
            app_id=credential_app_id,
            app_secret=credential_app_secret,
            api_base_url=api_base_url,
        )
        text = build_purchase_arrival_review_text(payload)
        message_id = send_group_text_message(
            client=client,
            tenant_access_token=token,
            chat_id=chat_id,
            text=text,
            api_base_url=api_base_url,
        )
        return {"ok": True, "chat_id": chat_id, "message_id": message_id, "text": text}

    def tool_calls_from_n8n_payload(payload: dict[str, Any]) -> list[Any]:
        tool_calls = payload.get("tool_trace") or payload.get("tool_calls") or []
        return tool_calls if isinstance(tool_calls, list) else []

    def write_run_log(
        *,
        bot: BotConfig,
        message: Any,
        status: str,
        total_ms: float,
        n8n_ms: float = 0.0,
        token_ms: float = 0.0,
        reply_ms: float = 0.0,
        has_reply: bool = False,
        tool_calls: list[Any] | None = None,
        error: str | None = None,
        workflow: str | None = None,
    ) -> None:
        if not runtime_run_log_url:
            return
        try:
            client.post(
                runtime_run_log_url,
                json={
                    "event_id": message.message_id,
                    "message_id": message.message_id,
                    "bot_name": bot.name,
                    "workflow": workflow or bot.n8n_webhook_url,
                    "status": status,
                    "latency_ms": round(total_ms, 1),
                    "n8n_ms": round(n8n_ms, 1),
                    "token_ms": round(token_ms, 1),
                    "reply_ms": round(reply_ms, 1),
                    "has_reply": has_reply,
                    "tool_calls": tool_calls or [],
                    "error": error,
                },
            ).raise_for_status()
        except httpx.HTTPError as run_log_error:
            logger.warning(
                "failed to write feishu run log bot=%s message_id=%s error=%s",
                bot.name,
                message.message_id,
                run_log_error,
            )

    def purchase_order_ids_from_text(text: str) -> list[str]:
        matches = re.findall(r"\bPO-[0-9A-Za-z_-]+\b", text, flags=re.IGNORECASE)
        return list(dict.fromkeys(match.upper() for match in matches))

    def is_procurement_arrival_confirmation(text: str) -> bool:
        normalized = text.lower()
        return bool(
            purchase_order_ids_from_text(text)
            and (
                "arrived" in normalized
                or "arrival" in normalized
                or "到货" in text
                or "到仓" in text
                or "采购到货" in text
            )
        )

    def build_procurement_arrival_fast_path_reply(result: dict[str, Any]) -> str:
        confirmed_items = result.get("confirmed_items") if isinstance(result.get("confirmed_items"), list) else []
        if result.get("ok") is True and confirmed_items:
            lines = [
                "✅ 到仓确认成功",
                f"处理数量: {result.get('processed_count', len(confirmed_items))}",
                f"确认数量: {result.get('confirmed_count', len(confirmed_items))}",
                "到仓采购单:",
            ]
            for item in confirmed_items:
                lines.append(
                    "- "
                    f"{item.get('purchase_order_id')}: {item.get('item_id')} x {item.get('quantity')} | "
                    f"{item.get('warehouse_id')} / {item.get('location_code') or '-'} | "
                    f"{item.get('warehouse_sync_status')}"
                )
            lines.append("下一步: 通知 Warehouse 检查 arrived_unsynced 采购单并同步库存批次。")
            return "\n".join(lines)
        errors = result.get("errors") if isinstance(result.get("errors"), list) else []
        return "\n".join(
            [
                "❌ 到仓确认失败",
                f"处理数量: {result.get('processed_count', 0)}",
                f"确认数量: {result.get('confirmed_count', 0)}",
                f"错误: {errors or result.get('error') or 'unknown_error'}",
            ]
        )

    def procurement_arrival_fast_path_payload(bot: BotConfig, message: Any) -> dict[str, Any] | None:
        if bot.name != "procurement" or message.message_type != "text":
            return None
        if not is_procurement_arrival_confirmation(message.text):
            return None
        purchase_order_ids = purchase_order_ids_from_text(message.text)
        response = client.post(
            f"{runtime_mock_api_url}/procurement/purchase-orders/confirm-arrival-batch",
            json={
                "purchase_order_ids": purchase_order_ids,
                "received_by": f"feishu:{message.sender_id}",
            },
        )
        response.raise_for_status()
        result = response.json()
        return {
            "reply": build_procurement_arrival_fast_path_reply(result),
            "workflow": "/procurement/purchase-order-arrival-fast-path",
            "tool_trace": [
                {
                    "tool": "procurement_confirm_purchase_order_arrival_tool",
                    "input": {
                        "purchase_order_ids": purchase_order_ids,
                        "received_by": f"feishu:{message.sender_id}",
                    },
                    "output": result,
                }
            ],
        }

    def write_inventory_table_run_log(
        *,
        event_id: str,
        workflow: str,
        status: str,
        latency_ms: float,
        error: str | None = None,
        tool_calls: list[Any] | None = None,
    ) -> None:
        if not runtime_run_log_url:
            return
        try:
            client.post(
                runtime_run_log_url,
                json={
                    "event_id": event_id,
                    "message_id": "",
                    "bot_name": "warehouse",
                    "workflow": workflow,
                    "status": status,
                    "latency_ms": round(latency_ms, 1),
                    "tool_calls": tool_calls or [],
                    "error": error,
                },
            ).raise_for_status()
        except httpx.HTTPError as run_log_error:
            logger.warning(
                "failed to write inventory table run log event_id=%s error=%s",
                event_id,
                run_log_error,
            )

    def write_sync_run_log(
        *,
        sku: str,
        status: str,
        latency_ms: float,
        error: str | None = None,
        tool_calls: list[Any] | None = None,
    ) -> None:
        write_inventory_table_run_log(
            event_id=f"inventory_table_sync:{sku}",
            workflow="/warehouse/inventory-table/sync",
            status=status,
            latency_ms=latency_ms,
            error=error,
            tool_calls=tool_calls,
        )

    def inventory_table_sync_configured() -> bool:
        return bool(table_app_id and table_app_secret and table_app_token)

    def inventory_table_provision_configured() -> bool:
        return bool(table_app_id and table_app_secret and table_app_token)

    def procurement_table_configured() -> bool:
        return bool(table_app_id and table_app_secret and table_app_token)

    def inventory_table_url_for(table_identifier: str) -> str:
        if not table_url:
            return ""
        return table_url.replace("{table_id}", table_identifier)

    def procurement_table_url_for(state: dict[str, str], table_identifier: str) -> str:
        configured_url = state.get("table_url") or ""
        if not configured_url:
            return ""
        return configured_url.replace("{table_id}", table_identifier)

    def build_inventory_snapshot_fields(inventory: dict[str, Any]) -> dict[str, Any]:
        sku = str(inventory.get("sku") or "").strip()
        locations = inventory.get("locations") if isinstance(inventory.get("locations"), list) else []
        first_location = locations[0] if locations and isinstance(locations[0], dict) else {}
        warehouse_id = str(first_location.get("warehouse_id") or inventory.get("warehouse_id") or "unknown")
        open_exceptions = (
            inventory.get("open_exceptions") if isinstance(inventory.get("open_exceptions"), list) else []
        )
        synced_at = datetime.now(UTC).isoformat()
        return {
            "SKU": sku,
            "Product Name": str(inventory.get("product_name") or inventory.get("name") or sku),
            "Warehouse": warehouse_id,
            "Available": int(inventory.get("available", 0)),
            "Reserved": int(inventory.get("reserved", 0)),
            "Pending Orders": int(inventory.get("pending_orders", 0)),
            "Risk Level": str(inventory.get("risk_level") or "unknown"),
            "Open Exception Count": len(open_exceptions),
            "Recommendation": str(inventory.get("recommendation") or ""),
            "Last Synced At": synced_at,
            "Sync Status": "synced",
            "Source Version": f"mock-api:{sku}:{warehouse_id}",
        }

    def backend_field_to_feishu_field(field: dict[str, Any]) -> dict[str, Any]:
        type_mapping = {
            "text": 1,
            "number": 2,
            "single_select": 3,
            "date": 5,
            "datetime": 5,
            "image": 17,
            "attachment": 17,
            "button": 3001,
        }
        field_name = str(field.get("name") or field.get("field_name") or "").strip()
        field_type = str(field.get("type") or "text").strip()
        spec: dict[str, Any] = {
            "field_name": field_name,
            "type": type_mapping.get(field_type, 1),
        }
        options = field.get("options")
        if spec["type"] == 3 and isinstance(options, list):
            spec["property"] = {
                "options": [
                    {
                        "name": str(option.get("name") or ""),
                        "color": option.get("color", 0),
                    }
                    for option in options
                    if isinstance(option, dict) and option.get("name")
                ]
            }
        return spec

    def inventory_table_field_specs() -> list[dict[str, Any]]:
        try:
            response = client.get(f"{runtime_mock_api_url}/warehouse/inventory/table-schema")
            response.raise_for_status()
            payload = response.json()
            fields = payload.get("fields", [])
            if payload.get("ok") is not True or not isinstance(fields, list) or not fields:
                return INVENTORY_TABLE_FIELD_SPECS
            specs = [
                backend_field_to_feishu_field(field)
                for field in fields
                if isinstance(field, dict) and str(field.get("name") or field.get("field_name") or "").strip()
            ]
            return specs or INVENTORY_TABLE_FIELD_SPECS
        except (httpx.HTTPError, ValueError, TypeError) as error:
            logger.warning("failed to load warehouse inventory table schema from backend: %s", error)
            return INVENTORY_TABLE_FIELD_SPECS

    def inventory_balance_table_field_specs() -> list[dict[str, Any]]:
        response = client.get(f"{runtime_mock_api_url}/warehouse/stock/balances/table-schema")
        response.raise_for_status()
        payload = response.json()
        fields = payload.get("fields", [])
        if payload.get("ok") is not True or not isinstance(fields, list) or not fields:
            raise RuntimeError(f"warehouse inventory balance table schema lookup failed: {payload}")
        return [
            backend_field_to_feishu_field(field)
            for field in fields
            if isinstance(field, dict) and str(field.get("name") or field.get("field_name") or "").strip()
        ]

    def fetch_inventory_balance_table_rows(
        *,
        item_id: str | None = None,
        warehouse_id: str | None = None,
        location_code: str | None = None,
        cursor: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        response = client.post(
            f"{runtime_mock_api_url}/warehouse/stock/balances/table-rows",
            json={
                "item_id": item_id or None,
                "warehouse_id": warehouse_id or None,
                "location_code": location_code or None,
                "cursor": cursor or None,
                "limit": max(min(int(limit or 500), 500), 1),
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is not True:
            raise RuntimeError(f"warehouse inventory balance table rows lookup failed: {payload}")
        rows = payload.get("items", [])
        if not isinstance(rows, list):
            raise RuntimeError(f"warehouse inventory balance table rows returned invalid items: {payload}")
        return {
            "items": [row for row in rows if isinstance(row, dict) and isinstance(row.get("fields"), dict)],
            "next_cursor": str(payload.get("next_cursor") or ""),
        }

    def fetch_business_table_rows_paginated(
        *,
        rows_endpoint: str,
        request_payload: dict[str, Any],
        error_context: str,
        max_pages: int = 500,
    ) -> dict[str, Any]:
        """Fetch all pages from a mock-api read-model endpoint.

        Args:
            rows_endpoint: mock-api path that returns a Feishu read-model page.
            request_payload: Source filters supplied by the sync endpoint. The
                helper preserves these filters and injects `limit` and `offset`
                for each page request.
            error_context: Human-readable table family name used in runtime
                errors and run-log messages.
            max_pages: Safety cap that prevents an accidental infinite loop if
                a source endpoint returns a repeated `next_offset`.

        Returns:
            A dictionary with normalized `items` and `page_count`. Old source
            endpoints without `has_more` or `next_offset` are treated as a
            single page for backward compatibility.

        Raises:
            httpx.HTTPError: If the mock-api request fails.
            RuntimeError: If the source payload is not a successful read-model
                response or if pagination does not advance.
        """

        limit = max(min(int(request_payload.get("limit") or 100), 500), 1)
        offset = max(int(request_payload.get("offset") or 0), 0)
        page_count = 0
        rows: list[dict[str, Any]] = []
        seen_offsets: set[int] = set()
        while page_count < max_pages:
            if offset in seen_offsets:
                raise RuntimeError(f"{error_context} pagination did not advance at offset={offset}")
            seen_offsets.add(offset)
            page_payload = {**request_payload, "limit": limit, "offset": offset}
            rows_response = client.post(
                f"{runtime_mock_api_url}{rows_endpoint}",
                json=page_payload,
            )
            rows_response.raise_for_status()
            rows_payload = rows_response.json()
            if rows_payload.get("ok") is not True:
                raise RuntimeError(f"{error_context} rows lookup failed: {rows_payload}")
            page_rows = rows_payload.get("items", [])
            if not isinstance(page_rows, list):
                raise RuntimeError(f"{error_context} rows returned invalid items: {rows_payload}")
            rows.extend(row for row in page_rows if isinstance(row, dict) and isinstance(row.get("fields"), dict))
            page_count += 1
            has_more = bool(rows_payload.get("has_more"))
            raw_next_offset = rows_payload.get("next_offset")
            if not has_more or raw_next_offset is None:
                break
            offset = max(int(raw_next_offset), 0)
        else:
            raise RuntimeError(f"{error_context} pagination exceeded max_pages={max_pages}")
        return {"items": rows, "page_count": page_count}

    def fetch_inventory_table_rows(
        *,
        item_id: str | None = None,
        sku: str | None = None,
        warehouse_id: str | None = None,
        location_code: str | None = None,
        category: str | None = None,
        category_id: str | None = None,
        batch_no: str | None = None,
        expiry_risk: str | None = None,
        risk_level: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        response = client.post(
            f"{runtime_mock_api_url}/warehouse/inventory/table-rows",
            json={
                "item_id": item_id or None,
                "sku": sku or None,
                "warehouse_id": warehouse_id or None,
                "location_code": location_code or None,
                "category": category or None,
                "category_id": category_id or None,
                "batch_no": batch_no or None,
                "expiry_risk": expiry_risk or None,
                "risk_level": risk_level or None,
                "limit": limit,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is not True:
            raise RuntimeError(f"warehouse inventory table rows lookup failed: {payload}")
        rows = payload.get("items", [])
        if not isinstance(rows, list):
            raise RuntimeError(f"warehouse inventory table rows returned invalid items: {payload}")
        return [
            row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("fields"), dict)
        ]

    def legacy_inventory_rows_from_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "sku": str(item.get("sku") or ""),
                "fields": build_inventory_snapshot_fields(item),
            }
            for item in items
        ]

    def fetch_inventory_table_rows_with_fallback(
        *,
        item_id: str | None = None,
        sku: str | None = None,
        warehouse_id: str | None = None,
        location_code: str | None = None,
        category: str | None = None,
        category_id: str | None = None,
        batch_no: str | None = None,
        expiry_risk: str | None = None,
        risk_level: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        try:
            return fetch_inventory_table_rows(
                item_id=item_id,
                sku=sku,
                warehouse_id=warehouse_id,
                location_code=location_code,
                category=category,
                category_id=category_id,
                batch_no=batch_no,
                expiry_risk=expiry_risk,
                risk_level=risk_level,
                limit=limit,
            )
        except (httpx.HTTPError, RuntimeError) as error:
            logger.warning("failed to load warehouse table rows from backend contract: %s", error)
        if sku:
            inventory_response = client.get(
                f"{runtime_mock_api_url}/warehouse/inventory/{sku}",
            )
            inventory_response.raise_for_status()
            return legacy_inventory_rows_from_items([inventory_response.json()])
        search_payload = {
            "warehouse_id": warehouse_id or None,
            "location_code": location_code or None,
            "category": category or None,
            "category_id": category_id or None,
            "batch_no": batch_no or None,
            "expiry_risk": expiry_risk or None,
            "risk_level": risk_level or None,
            "limit": limit,
        }
        inventory_response = client.post(
            f"{runtime_mock_api_url}/warehouse/inventory/search",
            json=search_payload,
        )
        inventory_response.raise_for_status()
        return legacy_inventory_rows_from_items(inventory_response.json().get("items", []))

    def bitable_records_url(record_id: str | None = None, table_identifier: str | None = None) -> str:
        resolved_table_identifier = table_identifier or inventory_table_state["table_id"]
        base = (
            f"{api_base_url}/open-apis/bitable/v1/apps/{table_app_token}"
            f"/tables/{resolved_table_identifier}/records"
        )
        return f"{base}/{record_id}" if record_id else base

    def bitable_records_batch_create_url(table_identifier: str | None = None) -> str:
        return f"{bitable_records_url(table_identifier=table_identifier)}/batch_create"

    def bitable_records_batch_update_url(table_identifier: str | None = None) -> str:
        return f"{bitable_records_url(table_identifier=table_identifier)}/batch_update"

    def bitable_tables_url() -> str:
        return f"{api_base_url}/open-apis/bitable/v1/apps/{table_app_token}/tables"

    def bitable_fields_url(table_identifier: str) -> str:
        return f"{api_base_url}/open-apis/bitable/v1/apps/{table_app_token}/tables/{table_identifier}/fields"

    def bitable_views_url(table_identifier: str) -> str:
        return f"{api_base_url}/open-apis/bitable/v1/apps/{table_app_token}/tables/{table_identifier}/views"

    def bitable_view_url(table_identifier: str, view_id: str) -> str:
        return f"{bitable_views_url(table_identifier)}/{view_id}"

    def bitable_field_url(table_identifier: str, field_id: str) -> str:
        return f"{bitable_fields_url(table_identifier)}/{field_id}"

    def field_kind(type_value: Any) -> str:
        return {
            1: "text",
            2: "number",
            3: "single_select",
            5: "date",
            3001: "button",
        }.get(type_value, f"type_{type_value}")

    def inventory_field_options(field: dict[str, Any]) -> list[dict[str, Any]]:
        property_payload = field.get("property") or {}
        options = property_payload.get("options", [])
        if not isinstance(options, list):
            return []
        normalized_options = []
        for option in options:
            if not isinstance(option, dict) or not option.get("name"):
                continue
            normalized_option = {
                "name": str(option.get("name") or ""),
                "color": option.get("color"),
            }
            option_id = str(option.get("id") or option.get("option_id") or "")
            if option_id:
                normalized_option["id"] = option_id
            normalized_options.append(normalized_option)
        return normalized_options

    def serialize_inventory_field(field: dict[str, Any]) -> dict[str, Any]:
        type_value = field.get("type")
        return {
            "field_id": str(field.get("field_id") or ""),
            "field_name": str(field.get("field_name") or ""),
            "type": type_value,
            "kind": field_kind(type_value),
            "options": inventory_field_options(field),
        }

    def serialize_inventory_view(view: dict[str, Any]) -> dict[str, str]:
        return {
            "view_id": str(view.get("view_id") or view.get("id") or ""),
            "view_name": str(view.get("view_name") or view.get("name") or ""),
            "view_type": str(view.get("view_type") or view.get("type") or ""),
        }

    def field_signature(field: dict[str, Any]) -> dict[str, Any]:
        signature: dict[str, Any] = {
            "field_name": field["field_name"],
            "type": field["type"],
        }
        property_payload = field.get("property")
        if property_payload is not None:
            signature["property"] = property_payload
        return signature

    def desired_single_select_option_names(field: dict[str, Any]) -> set[str]:
        options = field.get("property", {}).get("options", [])
        if not isinstance(options, list):
            return set()
        return {
            str(option.get("name") or "")
            for option in options
            if isinstance(option, dict) and option.get("name")
        }

    def existing_single_select_option_names(field: dict[str, Any]) -> set[str]:
        return {
            option["name"]
            for option in inventory_field_options(field)
            if option.get("name")
        }

    def inventory_field_compatible(desired: dict[str, Any], existing: dict[str, Any]) -> bool:
        if desired.get("field_name") != existing.get("field_name"):
            return False
        if desired.get("type") != existing.get("type"):
            return False
        if desired.get("type") == 3:
            return desired_single_select_option_names(desired).issubset(
                existing_single_select_option_names(existing)
            )
        return True

    def fields_by_name_for_table(*, token: str, table_identifier: str) -> dict[str, dict[str, Any]]:
        response = client.get(
            bitable_fields_url(table_identifier),
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu inventory table field list failed: {payload}")
        items = payload.get("data", {}).get("items", [])
        return {
            str(item.get("field_name") or ""): item
            for item in items
            if isinstance(item, dict) and item.get("field_name")
        }

    def update_inventory_table_field(
        *,
        token: str,
        table_identifier: str,
        field_id: str,
        field: dict[str, Any],
    ) -> None:
        response = client.put(
            bitable_field_url(table_identifier, field_id),
            headers={"Authorization": f"Bearer {token}"},
            json=field,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, 1254606, None}:
            raise RuntimeError(f"Feishu inventory table field update failed: {payload}")

    def delete_inventory_table_field(
        *,
        token: str,
        table_identifier: str,
        field_id: str,
    ) -> None:
        response = client.delete(
            bitable_field_url(table_identifier, field_id),
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, 1254606, None}:
            raise RuntimeError(f"Feishu inventory table field delete failed: {payload}")

    def create_inventory_table_fields(
        *,
        token: str,
        table_identifier: str,
        field_specs: list[dict[str, Any]],
        existing_fields: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        existing = (
            existing_fields
            if existing_fields is not None
            else fields_by_name_for_table(token=token, table_identifier=table_identifier)
        )
        if field_specs:
            first_field = field_specs[0]
            first_field_name = str(first_field.get("field_name") or "").strip()
            primary_field = next(
                (field for field in existing.values() if field.get("is_primary")),
                None,
            )
            if (
                primary_field
                and first_field_name
                and first_field_name not in existing
                and int(primary_field.get("type") or 0) == 1
                and int(first_field.get("type") or 0) == 1
            ):
                field_id = str(primary_field.get("field_id") or "")
                if field_id:
                    update_inventory_table_field(
                        token=token,
                        table_identifier=table_identifier,
                        field_id=field_id,
                        field=first_field,
                    )
                    existing.pop(str(primary_field.get("field_name") or ""), None)
                    existing[first_field_name] = {
                        **primary_field,
                        **first_field,
                        "field_id": field_id,
                        "field_name": first_field_name,
                        "is_primary": True,
                    }
        for field in field_specs:
            field_type = int(field.get("type") or 0)
            existing_field = existing.get(str(field["field_name"]))
            if field_type == 3001:
                # Feishu's field OpenAPI cannot create or update button fields.
                # Keep any manually configured Sync Inventory field intact and
                # skip cell writes elsewhere so table automation owns the button.
                continue
            if existing_field:
                field_id = str(existing_field.get("field_id") or "")
                if field_id and not inventory_field_compatible(field, existing_field):
                    update_inventory_table_field(
                        token=token,
                        table_identifier=table_identifier,
                        field_id=field_id,
                        field=field,
                    )
                continue
            response = client.post(
                bitable_fields_url(table_identifier),
                headers={"Authorization": f"Bearer {token}"},
                json=field,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in {0, None}:
                raise RuntimeError(f"Feishu inventory table field create failed: {payload}")

    def list_inventory_tables(*, token: str) -> list[dict[str, Any]]:
        response = client.get(
            bitable_tables_url(),
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu inventory table list failed: {payload}")
        items = payload.get("data", {}).get("items", [])
        return [item for item in items if isinstance(item, dict)]

    def inventory_table_candidate_names(table_name: str) -> list[str]:
        if table_name == DEFAULT_INVENTORY_TABLE_NAME:
            return DEFAULT_INVENTORY_TABLE_ALIASES
        return [table_name]

    def list_inventory_table_views(*, token: str, table_identifier: str) -> list[dict[str, str]]:
        response = client.get(
            bitable_views_url(table_identifier),
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu inventory table view list failed: {payload}")
        data = payload.get("data", {})
        items = data.get("items") or data.get("views") or []
        return [serialize_inventory_view(item) for item in items if isinstance(item, dict)]

    def find_inventory_table_by_name(*, token: str, table_name: str) -> dict[str, str]:
        tables = list_inventory_tables(token=token)
        for candidate_name in inventory_table_candidate_names(table_name):
            for item in tables:
                if str(item.get("name") or item.get("table_name") or "") == candidate_name:
                    return {
                        "table_id": str(item.get("table_id") or ""),
                        "view_id": str(item.get("default_view_id") or item.get("view_id") or ""),
                    }
        return {"table_id": "", "view_id": ""}

    def find_inventory_table_by_name_safe(*, token: str, table_name: str) -> dict[str, str]:
        try:
            return find_inventory_table_by_name(token=token, table_name=table_name)
        except (httpx.HTTPError, RuntimeError) as error:
            logger.warning("failed to list inventory tables before resolving table id: %s", error)
            return {"table_id": "", "view_id": ""}

    def resolve_inventory_table_for_schema(*, token: str, table_name: str) -> dict[str, str]:
        existing = find_inventory_table_by_name_safe(token=token, table_name=table_name)
        if existing["table_id"]:
            remember_inventory_table(existing)
            return {**existing, "action": "existing"}
        if inventory_table_state["table_id"]:
            return {
                "table_id": inventory_table_state["table_id"],
                "view_id": inventory_table_state["view_id"],
                "action": "existing",
            }
        return create_or_reuse_inventory_table(token=token, table_name=table_name)

    def build_inventory_table_schema(*, token: str, table_name: str) -> dict[str, Any]:
        table_result = resolve_inventory_table_for_schema(token=token, table_name=table_name)
        fields_by_name = fields_by_name_for_table(token=token, table_identifier=table_result["table_id"])
        views = list_inventory_table_views(token=token, table_identifier=table_result["table_id"])
        return {
            "table_id": table_result["table_id"],
            "view_id": table_result["view_id"],
            "action": table_result["action"],
            "fields": [serialize_inventory_field(field) for field in fields_by_name.values()],
            "views": views,
        }

    def validated_inventory_view_plan(
        request: InventoryTableViewCreateRequest,
        fields_by_name: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Any], list[str]]:
        plan = {
            "view_name": request.view_name.strip(),
            "view_type": (request.view_type.strip() or "grid").lower(),
            "visible_fields": [field.strip() for field in request.visible_fields if field.strip()],
            "filters": [
                {
                    "field": item.field.strip(),
                    "operator": item.operator.strip(),
                    "value": item.value,
                }
                for item in request.filters
                if item.field.strip()
            ],
            "sorts": [
                {
                    "field": item.field.strip(),
                    "order": (item.order.strip() or "asc").lower(),
                }
                for item in request.sorts
                if item.field.strip()
            ],
        }
        referenced_fields: list[str] = []
        referenced_fields.extend(plan["visible_fields"])
        referenced_fields.extend(item["field"] for item in plan["filters"])
        referenced_fields.extend(item["field"] for item in plan["sorts"])
        missing_fields = list(dict.fromkeys(field for field in referenced_fields if field not in fields_by_name))
        return plan, missing_fields

    def ensure_inventory_table_fields(
        *,
        token: str,
        table_identifier: str,
        field_specs: list[dict[str, Any]],
    ) -> None:
        try:
            existing_fields = fields_by_name_for_table(token=token, table_identifier=table_identifier)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise
            existing_fields = {}
        create_inventory_table_fields(
            token=token,
            table_identifier=table_identifier,
            field_specs=field_specs,
            existing_fields=existing_fields,
        )

    def is_missing_inventory_table_error(error: Exception) -> bool:
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code == 404
        message = str(error).lower()
        return (
            "1254041" in message
            or "1254045" in message
            or "tableidnotfound" in message
            or "table not found" in message
        )

    def remember_feishu_table_state(state: dict[str, str], result: dict[str, str]) -> None:
        """Remember a resolved Feishu table id in memory and durable JSON state.

        Args:
            state: Mutable runtime state for one table family. The private
                `_state_key` field maps it to the durable JSON entry.
            result: Table resolution result returned by Feishu lookup or table
                creation. `table_id` is required to persist an entry; `view_id`
                and `table_url` are optional.

        Side Effects:
            Updates the process-local state immediately and writes the durable
            JSON state file when `FEISHU_TABLE_STATE_PATH` is configured. Write
            failures are logged but do not fail the sync, because table syncing
            should still succeed when the local state volume is temporarily
            unavailable.
        """

        if result.get("table_id"):
            state["table_id"] = result["table_id"]
        if result.get("view_id"):
            state["view_id"] = result["view_id"]
        if result.get("table_url"):
            state["table_url"] = result["table_url"]
        state_key = state.get("_state_key", "")
        if not table_state_path or not state_key or not state.get("table_id"):
            return
        with durable_table_state_lock:
            durable_table_state[state_key] = {
                "table_id": state.get("table_id", ""),
                "view_id": state.get("view_id", ""),
                "table_url": state.get("table_url", ""),
            }
            try:
                save_feishu_table_state(table_state_path, durable_table_state)
            except OSError as error:
                logger.warning("failed to persist Feishu table state for %s: %s", state_key, error)

    def clear_feishu_table_state_entry(state: dict[str, str]) -> None:
        """Clear a missing Feishu table id from runtime and durable state.

        Args:
            state: Mutable runtime state for one table family.

        Side Effects:
            Removes the table id from memory and from the optional durable JSON
            file. This prevents the next sync from retrying an id that Feishu
            has already reported as missing or deleted.
        """

        state["table_id"] = ""
        state["view_id"] = ""
        state_key = state.get("_state_key", "")
        if not table_state_path or not state_key:
            return
        with durable_table_state_lock:
            durable_table_state.pop(state_key, None)
            try:
                save_feishu_table_state(table_state_path, durable_table_state)
            except OSError as error:
                logger.warning("failed to clear Feishu table state for %s: %s", state_key, error)

    def remember_inventory_table(result: dict[str, str]) -> None:
        remember_feishu_table_state(inventory_table_state, result)

    def create_or_reuse_inventory_table(
        *,
        token: str,
        table_name: str,
        field_specs: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        with inventory_table_lock:
            field_specs = field_specs or inventory_table_field_specs()
            existing = find_inventory_table_by_name_safe(token=token, table_name=table_name)
            if existing["table_id"]:
                ensure_inventory_table_fields(
                    token=token,
                    table_identifier=existing["table_id"],
                    field_specs=field_specs,
                )
                remember_inventory_table(existing)
                return {**existing, "action": "existing"}
            response = client.post(
                bitable_tables_url(),
                headers={"Authorization": f"Bearer {token}"},
                json={"table": {"name": table_name}},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in {0, None}:
                if payload.get("code") == 1254013:
                    existing = find_inventory_table_by_name(token=token, table_name=table_name)
                    if existing["table_id"]:
                        ensure_inventory_table_fields(
                            token=token,
                            table_identifier=existing["table_id"],
                            field_specs=field_specs,
                        )
                        remember_inventory_table(existing)
                        return {**existing, "action": "existing"}
                raise RuntimeError(f"Feishu inventory table create failed: {payload}")
            data = payload.get("data", {})
            created_table_id = str(data.get("table_id") or "")
            if not created_table_id:
                raise RuntimeError(f"Feishu inventory table create returned no table_id: {payload}")
            create_inventory_table_fields(
                token=token,
                table_identifier=created_table_id,
                field_specs=field_specs,
            )
            result = {
                "table_id": created_table_id,
                "view_id": str(data.get("default_view_id") or data.get("view_id") or ""),
                "action": "created",
            }
            remember_inventory_table(result)
            return result

    def ensure_inventory_table(
        *,
        token: str,
        table_name: str,
        field_specs: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        with inventory_table_lock:
            field_specs = field_specs or inventory_table_field_specs()
            if inventory_table_state["table_id"]:
                result = {
                    "table_id": inventory_table_state["table_id"],
                    "view_id": inventory_table_state["view_id"],
                    "action": "existing",
                }
                try:
                    ensure_inventory_table_fields(
                        token=token,
                        table_identifier=result["table_id"],
                        field_specs=field_specs,
                    )
                    return result
                except (httpx.HTTPStatusError, RuntimeError) as error:
                    if not is_missing_inventory_table_error(error):
                        raise
                    clear_feishu_table_state_entry(inventory_table_state)
            existing = find_inventory_table_by_name_safe(token=token, table_name=table_name)
            if existing["table_id"]:
                ensure_inventory_table_fields(
                    token=token,
                    table_identifier=existing["table_id"],
                    field_specs=field_specs,
                )
                remember_inventory_table(existing)
                return {**existing, "action": "existing"}
            return create_or_reuse_inventory_table(
                token=token,
                table_name=table_name,
                field_specs=field_specs,
            )

    def create_inventory_table(*, token: str, table_name: str) -> dict[str, str]:
        result = create_or_reuse_inventory_table(token=token, table_name=table_name)
        return {
            "table_id": result["table_id"],
            "view_id": result["view_id"],
            "action": result["action"],
        }

    def inventory_balance_table_candidate_names(table_name: str) -> list[str]:
        if table_name == DEFAULT_INVENTORY_BALANCE_TABLE_NAME:
            return DEFAULT_INVENTORY_BALANCE_TABLE_ALIASES
        return [table_name]

    def remember_inventory_balance_table(result: dict[str, str]) -> None:
        remember_feishu_table_state(inventory_balance_table_state, result)

    def find_inventory_balance_table_by_name(*, token: str, table_name: str) -> dict[str, str]:
        tables = list_inventory_tables(token=token)
        for candidate_name in inventory_balance_table_candidate_names(table_name):
            for item in tables:
                if str(item.get("name") or item.get("table_name") or "") == candidate_name:
                    return {
                        "table_id": str(item.get("table_id") or ""),
                        "view_id": str(item.get("default_view_id") or item.get("view_id") or ""),
                    }
        return {"table_id": "", "view_id": ""}

    def find_inventory_balance_table_by_name_safe(*, token: str, table_name: str) -> dict[str, str]:
        try:
            return find_inventory_balance_table_by_name(token=token, table_name=table_name)
        except (httpx.HTTPError, RuntimeError) as error:
            logger.warning("failed to list inventory balance tables before resolving table id: %s", error)
            return {"table_id": "", "view_id": ""}

    def create_or_reuse_inventory_balance_table(
        *,
        token: str,
        table_name: str,
        field_specs: list[dict[str, Any]],
    ) -> dict[str, str]:
        with inventory_table_lock:
            existing = find_inventory_balance_table_by_name_safe(token=token, table_name=table_name)
            if existing["table_id"]:
                ensure_inventory_table_fields(
                    token=token,
                    table_identifier=existing["table_id"],
                    field_specs=field_specs,
                )
                remember_inventory_balance_table(existing)
                return {**existing, "action": "existing"}
            response = client.post(
                bitable_tables_url(),
                headers={"Authorization": f"Bearer {token}"},
                json={"table": {"name": table_name}},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in {0, None}:
                if payload.get("code") == 1254013:
                    existing = find_inventory_balance_table_by_name(token=token, table_name=table_name)
                    if existing["table_id"]:
                        ensure_inventory_table_fields(
                            token=token,
                            table_identifier=existing["table_id"],
                            field_specs=field_specs,
                        )
                        remember_inventory_balance_table(existing)
                        return {**existing, "action": "existing"}
                raise RuntimeError(f"Feishu inventory balance table create failed: {payload}")
            data = payload.get("data", {})
            created_table_id = str(data.get("table_id") or "")
            if not created_table_id:
                raise RuntimeError(f"Feishu inventory balance table create returned no table_id: {payload}")
            create_inventory_table_fields(
                token=token,
                table_identifier=created_table_id,
                field_specs=field_specs,
            )
            result = {
                "table_id": created_table_id,
                "view_id": str(data.get("default_view_id") or data.get("view_id") or ""),
                "action": "created",
            }
            remember_inventory_balance_table(result)
            return result

    def ensure_inventory_balance_table(
        *,
        token: str,
        table_name: str,
        field_specs: list[dict[str, Any]],
    ) -> dict[str, str]:
        if inventory_balance_table_state["table_id"]:
            result = {
                "table_id": inventory_balance_table_state["table_id"],
                "view_id": inventory_balance_table_state["view_id"],
                "action": "existing",
            }
            try:
                ensure_inventory_table_fields(
                    token=token,
                    table_identifier=result["table_id"],
                    field_specs=field_specs,
                )
                return result
            except (httpx.HTTPStatusError, RuntimeError) as error:
                if not is_missing_inventory_table_error(error):
                    raise
                clear_feishu_table_state_entry(inventory_balance_table_state)
        existing = find_inventory_balance_table_by_name_safe(token=token, table_name=table_name)
        if existing["table_id"]:
            ensure_inventory_table_fields(
                token=token,
                table_identifier=existing["table_id"],
                field_specs=field_specs,
            )
            remember_inventory_balance_table(existing)
            return {**existing, "action": "existing"}
        return create_or_reuse_inventory_balance_table(
            token=token,
            table_name=table_name,
            field_specs=field_specs,
        )

    def ensure_inventory_balance_table_views(
        *,
        token: str,
        table_identifier: str,
        fields_by_name: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        desired_plans = [
            {
                "view_name": "库存余额总览",
                "view_type": "grid",
                "visible_fields": [
                    "id",
                    "warehouse_id",
                    "location_code",
                    "item_id",
                    "batch_no",
                    "quantity_on_hand",
                    "storage_status",
                    "updated_at",
                ],
                "filters": [],
                "sorts": [{"field": "warehouse_id", "order": "asc"}, {"field": "item_id", "order": "asc"}],
            },
            {
                "view_name": "低库存余额",
                "view_type": "grid",
                "visible_fields": [
                    "id",
                    "warehouse_id",
                    "location_code",
                    "item_id",
                    "batch_no",
                    "quantity_on_hand",
                    "reorder_threshold",
                    "updated_at",
                ],
                "filters": [],
                "sorts": [{"field": "quantity_on_hand", "order": "asc"}],
            },
            {
                "view_name": "可售库存",
                "view_type": "grid",
                "visible_fields": [
                    "id",
                    "warehouse_id",
                    "location_code",
                    "item_id",
                    "batch_no",
                    "quantity_on_hand",
                    "storage_status",
                    "updated_at",
                ],
                "filters": [{"field": "storage_status", "operator": "is", "value": "available"}],
                "sorts": [{"field": "warehouse_id", "order": "asc"}, {"field": "item_id", "order": "asc"}],
            },
        ]
        existing_views = {
            view["view_name"]: view
            for view in list_inventory_table_views(token=token, table_identifier=table_identifier)
        }
        results = []
        for plan in desired_plans:
            view = existing_views.get(plan["view_name"])
            if view:
                action = "existing"
                view_id = view["view_id"]
            else:
                created = create_inventory_table_view(
                    token=token,
                    table_identifier=table_identifier,
                    view_name=plan["view_name"],
                    view_type=plan["view_type"],
                )
                action = created["action"]
                view_id = created["view_id"]
            applied_property = apply_inventory_view_plan(
                token=token,
                table_identifier=table_identifier,
                view_id=view_id,
                plan=plan,
                fields_by_name=fields_by_name,
            )
            results.append(
                {
                    "view_id": view_id,
                    "view_name": plan["view_name"],
                    "action": action,
                    "applied_view_property": applied_property,
                }
            )
        return results

    def procurement_table_field_specs(schema_endpoint: str) -> list[dict[str, Any]]:
        response = client.get(f"{runtime_mock_api_url}{schema_endpoint}")
        response.raise_for_status()
        payload = response.json()
        fields = payload.get("fields", [])
        if payload.get("ok") is not True or not isinstance(fields, list) or not fields:
            raise RuntimeError(f"procurement table schema lookup failed: {payload}")
        return [
            backend_field_to_feishu_field(field)
            for field in fields
            if isinstance(field, dict) and str(field.get("name") or field.get("field_name") or "").strip()
        ]

    def remember_procurement_table(state: dict[str, str], result: dict[str, str]) -> None:
        remember_feishu_table_state(state, result)

    def ensure_procurement_table(
        *,
        token: str,
        table_name: str,
        state: dict[str, str],
        schema_endpoint: str,
        field_specs: list[dict[str, Any]] | None = None,
        prune_extra_fields_enabled: bool = False,
    ) -> dict[str, str]:
        field_specs = field_specs if field_specs is not None else procurement_table_field_specs(schema_endpoint)
        def prune_extra_fields(table_identifier: str) -> None:
            desired_names = {
                str(field.get("field_name") or "").strip()
                for field in field_specs
                if str(field.get("field_name") or "").strip()
            }
            existing_fields = fields_by_name_for_table(
                token=token,
                table_identifier=table_identifier,
            )
            for field_name, field in existing_fields.items():
                if field_name in desired_names or field.get("is_primary"):
                    continue
                field_id = str(field.get("field_id") or "")
                if field_id:
                    delete_inventory_table_field(
                        token=token,
                        table_identifier=table_identifier,
                        field_id=field_id,
                    )

        if state["table_id"]:
            result = {
                "table_id": state["table_id"],
                "view_id": state["view_id"],
                "action": "existing",
            }
            try:
                ensure_inventory_table_fields(
                    token=token,
                    table_identifier=result["table_id"],
                    field_specs=field_specs,
                )
                if prune_extra_fields_enabled:
                    prune_extra_fields(result["table_id"])
                return result
            except (httpx.HTTPStatusError, RuntimeError) as error:
                if not is_missing_inventory_table_error(error):
                    raise
                clear_feishu_table_state_entry(state)
        existing = find_inventory_table_by_name(token=token, table_name=table_name)
        if existing["table_id"]:
            ensure_inventory_table_fields(
                token=token,
                table_identifier=existing["table_id"],
                field_specs=field_specs,
            )
            if prune_extra_fields_enabled:
                prune_extra_fields(existing["table_id"])
            remember_procurement_table(state, existing)
            return {**existing, "action": "existing"}
        response = client.post(
            bitable_tables_url(),
            headers={"Authorization": f"Bearer {token}"},
            json={"table": {"name": table_name}},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, None}:
            if payload.get("code") == 1254013:
                existing = find_inventory_table_by_name(token=token, table_name=table_name)
                if existing["table_id"]:
                    ensure_inventory_table_fields(
                        token=token,
                        table_identifier=existing["table_id"],
                        field_specs=field_specs,
                    )
                    if prune_extra_fields_enabled:
                        prune_extra_fields(existing["table_id"])
                    remember_procurement_table(state, existing)
                    return {**existing, "action": "existing"}
            raise RuntimeError(f"Feishu procurement table create failed: {payload}")
        data = payload.get("data", {})
        table_id = str(data.get("table_id") or "")
        if not table_id:
            raise RuntimeError(f"Feishu procurement table create returned no table_id: {payload}")
        create_inventory_table_fields(
            token=token,
            table_identifier=table_id,
            field_specs=field_specs,
        )
        result = {
            "table_id": table_id,
            "view_id": str(data.get("default_view_id") or data.get("view_id") or ""),
            "action": "created",
        }
        remember_procurement_table(state, result)
        return result

    def procurement_records_url(
        *,
        table_identifier: str,
        record_id: str | None = None,
    ) -> str:
        base = (
            f"{api_base_url}/open-apis/bitable/v1/apps/{table_app_token}"
            f"/tables/{table_identifier}/records"
        )
        return f"{base}/{record_id}" if record_id else base

    def find_procurement_table_record(
        *,
        token: str,
        table_identifier: str,
        identity_field: str,
        identity_value: str,
    ) -> str:
        params = {
            "page_size": 20,
            "filter": (
                f'AND(CurrentValue.[{identity_field}]="'
                f'{bitable_filter_literal(identity_value)}")'
            ),
        }
        response = client.get(
            procurement_records_url(table_identifier=table_identifier),
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu procurement table record lookup failed: {payload}")
        items = payload.get("data", {}).get("items", [])
        if not items:
            return ""
        return str(items[0].get("record_id") or items[0].get("id") or "")

    def normalize_procurement_record_fields_by_specs(
        fields: dict[str, Any],
        field_specs: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Normalize outgoing Feishu record values from schema-derived specs.

        Args:
            fields: Business read-model fields from mock-api.
            field_specs: Feishu field specifications produced from backend
                schema. The specs include Feishu numeric field types, allowing
                date/datetime values to be converted before record upsert.

        Returns:
            Field values normalized with the same single-select and date logic
            used by inventory table sync. Empty numeric and date values are
            omitted because Feishu rejects blank strings for typed fields.
            Missing specs leave fields unchanged.
        """

        if not field_specs:
            return fields
        fields_by_name = {
            str(field.get("field_name") or ""): field
            for field in field_specs
            if str(field.get("field_name") or "").strip()
        }
        normalized = normalize_inventory_record_fields(fields, fields_by_name)
        return {
            field_name: value
            for field_name, value in normalized.items()
            if not (
                fields_by_name.get(field_name, {}).get("type") in {2, 5}
                and value in {None, ""}
            )
        }

    def upsert_procurement_table_record(
        *,
        token: str,
        table_identifier: str,
        identity_field: str,
        fields: dict[str, Any],
        field_specs: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        identity_value = str(fields.get(identity_field) or "").strip()
        if not identity_value:
            raise RuntimeError(f"procurement table row missing identity field: {identity_field}")
        normalized_fields = normalize_procurement_record_fields_by_specs(fields, field_specs)
        record_id = find_procurement_table_record(
            token=token,
            table_identifier=table_identifier,
            identity_field=identity_field,
            identity_value=identity_value,
        )
        if record_id:
            response = client.put(
                procurement_records_url(table_identifier=table_identifier, record_id=record_id),
                headers={"Authorization": f"Bearer {token}"},
                json={"fields": normalized_fields},
            )
            action = "updated"
        else:
            response = client.post(
                procurement_records_url(table_identifier=table_identifier),
                headers={"Authorization": f"Bearer {token}"},
                json={"fields": normalized_fields},
            )
            action = "created"
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu procurement table upsert failed: {payload}")
        data = payload.get("data", {})
        record = data.get("record") if isinstance(data.get("record"), dict) else {}
        returned_record_id = str(record.get("record_id") or data.get("record_id") or record_id)
        return {"action": action, "record_id": returned_record_id}

    def inventory_record_identity(fields: dict[str, Any]) -> dict[str, str]:
        source_version = str(fields.get("Source Version") or "").strip()
        if source_version:
            return {"Source Version": source_version}
        batch_identity = {
            "Warehouse ID": str(fields.get("Warehouse ID") or "").strip(),
            "Location": str(fields.get("Location") or "").strip(),
            "Item Name": str(fields.get("Item Name") or "").strip(),
            "Batch No": str(fields.get("Batch No") or "").strip(),
        }
        if all(batch_identity.values()):
            return batch_identity
        legacy_identity = {
            "SKU": str(fields.get("SKU") or "").strip(),
            "Warehouse": str(fields.get("Warehouse") or "").strip(),
        }
        if all(legacy_identity.values()):
            return legacy_identity
        raise RuntimeError(
            "inventory table row is missing batch identity fields: "
            "Source Version or Warehouse ID, Location, Item Name, Batch No"
        )

    def inventory_identity_key(identity: dict[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(identity.items())

    def bitable_filter_literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def inventory_record_filter_expression(identity: dict[str, str]) -> str:
        conditions = [
            f'CurrentValue.[{field_name}]="{bitable_filter_literal(value)}"'
            for field_name, value in identity.items()
        ]
        return f"AND({','.join(conditions)})"

    def inventory_record_filter_chunks(
        identities: list[dict[str, str]],
    ) -> list[tuple[list[dict[str, str]], str]]:
        chunks: list[tuple[list[dict[str, str]], str]] = []
        current_identities: list[dict[str, str]] = []
        current_expressions: list[str] = []

        for identity in identities:
            expression = inventory_record_filter_expression(identity)
            candidate_expressions = [*current_expressions, expression]
            candidate_filter = (
                candidate_expressions[0]
                if len(candidate_expressions) == 1
                else f"OR({','.join(candidate_expressions)})"
            )
            if (
                current_expressions
                and len(candidate_filter) > MAX_INVENTORY_RECORD_LOOKUP_FILTER_LENGTH
            ):
                chunks.append(
                    (
                        current_identities,
                        current_expressions[0]
                        if len(current_expressions) == 1
                        else f"OR({','.join(current_expressions)})",
                    )
                )
                current_identities = [identity]
                current_expressions = [expression]
                continue
            current_identities.append(identity)
            current_expressions.append(expression)

        if current_expressions:
            chunks.append(
                (
                    current_identities,
                    current_expressions[0]
                    if len(current_expressions) == 1
                    else f"OR({','.join(current_expressions)})",
                )
            )
        return chunks

    def single_select_record_value(field: dict[str, Any], value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text_value = value.strip()
        if not text_value:
            return value
        for option in inventory_field_options(field):
            option_id = str(option.get("id") or "")
            option_name = str(option.get("name") or "")
            if text_value in {option_id, option_name}:
                return option_name or option_id
        return value

    def date_record_value(value: Any) -> Any:
        if value in {None, ""}:
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text_value = str(value).strip()
        if not text_value:
            return value
        if text_value.isdigit():
            return int(text_value)
        normalized = text_value.replace("Z", "+00:00")
        try:
            if "T" in normalized:
                parsed = datetime.fromisoformat(normalized)
            else:
                parsed = datetime.fromisoformat(f"{normalized}T00:00:00+00:00")
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)

    def normalize_inventory_record_fields(
        fields: dict[str, Any],
        table_fields: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = dict(fields)
        for field_name, value in fields.items():
            field = table_fields.get(field_name)
            if isinstance(field, dict) and field.get("type") == 3001:
                normalized.pop(field_name, None)
                continue
            if isinstance(field, dict) and field.get("type") == 3:
                normalized[field_name] = single_select_record_value(field, value)
            if isinstance(field, dict) and field.get("type") == 5:
                normalized[field_name] = date_record_value(value)
        return normalized

    def find_inventory_table_records(
        *,
        token: str,
        identities: list[dict[str, str]],
    ) -> dict[tuple[tuple[str, str], ...], str]:
        if not identities:
            return {}
        records: dict[tuple[tuple[str, str], ...], str] = {}
        for identity_chunk, filter_expression in inventory_record_filter_chunks(identities):
            params: dict[str, Any] = {
                "page_size": min(max(len(identity_chunk), 20), 500),
                "filter": filter_expression,
            }
            if table_view_id:
                params["view_id"] = table_view_id
            response = client.get(
                bitable_records_url(),
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in {0, None}:
                raise RuntimeError(f"Feishu inventory table record lookup failed: {payload}")
            items = payload.get("data", {}).get("items", [])
            for item in items:
                if not isinstance(item, dict):
                    continue
                record_fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
                record_id = str(item.get("record_id") or item.get("id") or "")
                if not record_id:
                    continue
                if len(identity_chunk) == 1 and not record_fields:
                    records[inventory_identity_key(identity_chunk[0])] = record_id
                    continue
                for identity in identity_chunk:
                    if all(
                        str(record_fields.get(field) or "").strip() == value
                        for field, value in identity.items()
                    ):
                        records[inventory_identity_key(identity)] = record_id
                        break
        return records

    def find_inventory_table_record(*, token: str, identity: dict[str, str]) -> str:
        return find_inventory_table_records(token=token, identities=[identity]).get(
            inventory_identity_key(identity),
            "",
        )

    def upsert_inventory_table_record(*, token: str, fields: dict[str, Any]) -> dict[str, str]:
        identity = inventory_record_identity(fields)
        record_id = find_inventory_table_record(token=token, identity=identity)
        if record_id:
            response = client.put(
                bitable_records_url(record_id),
                headers={"Authorization": f"Bearer {token}"},
                json={"fields": fields},
            )
            action = "updated"
        else:
            response = client.post(
                bitable_records_url(),
                headers={"Authorization": f"Bearer {token}"},
                json={"fields": fields},
            )
            action = "created"
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu inventory table upsert failed: {payload}")
        data = payload.get("data", {})
        record = data.get("record") if isinstance(data.get("record"), dict) else {}
        returned_record_id = str(record.get("record_id") or data.get("record_id") or record_id)
        return {"action": action, "record_id": returned_record_id}

    def response_record_ids(payload: dict[str, Any]) -> list[str]:
        data = payload.get("data", {})
        records = data.get("records") or data.get("items") or []
        if not isinstance(records, list):
            return []
        return [
            str(record.get("record_id") or record.get("id") or "")
            for record in records
            if isinstance(record, dict)
        ]

    def create_inventory_table_records_batch(
        *,
        token: str,
        records: list[dict[str, Any]],
        table_identifier: str | None = None,
    ) -> list[str]:
        if not records:
            return []
        try:
            response = client.post(
                bitable_records_batch_create_url(table_identifier=table_identifier),
                headers={"Authorization": f"Bearer {token}"},
                json={"records": [{"fields": fields} for fields in records]},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in {404, 405}:
                raise
            created_ids = []
            for fields in records:
                response = client.post(
                    bitable_records_url(table_identifier=table_identifier),
                    headers={"Authorization": f"Bearer {token}"},
                    json={"fields": fields},
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") not in {0, None}:
                    raise RuntimeError(f"Feishu inventory table create failed: {payload}")
                data = payload.get("data", {})
                record = data.get("record") if isinstance(data.get("record"), dict) else {}
                created_ids.append(str(record.get("record_id") or data.get("record_id") or ""))
            return created_ids
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu inventory table batch create failed: {payload}")
        return response_record_ids(payload)

    def update_inventory_table_records_batch(
        *,
        token: str,
        records: list[dict[str, Any]],
        table_identifier: str | None = None,
    ) -> list[str]:
        if not records:
            return []
        try:
            response = client.post(
                bitable_records_batch_update_url(table_identifier=table_identifier),
                headers={"Authorization": f"Bearer {token}"},
                json={"records": records},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in {404, 405}:
                raise
            updated_ids = []
            for record in records:
                record_id = str(record.get("record_id") or "")
                response = client.put(
                    bitable_records_url(record_id, table_identifier=table_identifier),
                    headers={"Authorization": f"Bearer {token}"},
                    json={"fields": record.get("fields", {})},
                )
                response.raise_for_status()
                updated_ids.append(record_id)
            return updated_ids
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu inventory table batch update failed: {payload}")
        returned_ids = response_record_ids(payload)
        if returned_ids:
            return returned_ids
        return [str(record.get("record_id") or "") for record in records]

    def upsert_inventory_table_records(
        *,
        token: str,
        field_rows: list[dict[str, Any]],
        table_fields: dict[str, dict[str, Any]],
    ) -> list[dict[str, str]]:
        prepared = []
        for index, fields in enumerate(field_rows):
            identity = inventory_record_identity(fields)
            prepared.append(
                {
                    "index": index,
                    "identity": identity,
                    "fields": normalize_inventory_record_fields(fields, table_fields),
                }
            )
        existing_records = find_inventory_table_records(
            token=token,
            identities=[item["identity"] for item in prepared],
        )
        create_items = []
        update_items = []
        results: list[dict[str, str]] = [{} for _ in prepared]
        for item in prepared:
            record_id = existing_records.get(inventory_identity_key(item["identity"]), "")
            if record_id:
                update_items.append(
                    {
                        "index": item["index"],
                        "record_id": record_id,
                        "fields": item["fields"],
                    }
                )
            else:
                create_items.append(item)

        updated_ids = update_inventory_table_records_batch(
            token=token,
            records=[
                {"record_id": item["record_id"], "fields": item["fields"]}
                for item in update_items
            ],
        )
        for index, item in enumerate(update_items):
            record_id = updated_ids[index] if index < len(updated_ids) else item["record_id"]
            results[item["index"]] = {"action": "updated", "record_id": record_id or item["record_id"]}

        created_ids = create_inventory_table_records_batch(
            token=token,
            records=[item["fields"] for item in create_items],
        )
        for index, item in enumerate(create_items):
            record_id = created_ids[index] if index < len(created_ids) else ""
            results[item["index"]] = {"action": "created", "record_id": record_id}

        return results

    def balance_identity_key(balance_id: str) -> tuple[tuple[str, str], ...]:
        return (("id", balance_id),)

    def balance_record_filter_expression(balance_id: str) -> str:
        return f'CurrentValue.[id]="{bitable_filter_literal(balance_id)}"'

    def balance_record_filter_chunks(balance_ids: list[str]) -> list[tuple[list[str], str]]:
        chunks: list[tuple[list[str], str]] = []
        current_keys: list[str] = []
        current_expressions: list[str] = []
        for balance_id_value in balance_ids:
            expression = balance_record_filter_expression(balance_id_value)
            candidate_expressions = [*current_expressions, expression]
            candidate_filter = (
                candidate_expressions[0]
                if len(candidate_expressions) == 1
                else f"OR({','.join(candidate_expressions)})"
            )
            if (
                current_expressions
                and len(candidate_filter) > MAX_INVENTORY_RECORD_LOOKUP_FILTER_LENGTH
            ):
                chunks.append(
                    (
                        current_keys,
                        current_expressions[0]
                        if len(current_expressions) == 1
                        else f"OR({','.join(current_expressions)})",
                    )
                )
                current_keys = [balance_id_value]
                current_expressions = [expression]
                continue
            current_keys.append(balance_id_value)
            current_expressions.append(expression)
        if current_expressions:
            chunks.append(
                (
                    current_keys,
                    current_expressions[0]
                    if len(current_expressions) == 1
                    else f"OR({','.join(current_expressions)})",
                )
            )
        return chunks

    def find_balance_table_records(
        *,
        token: str,
        table_identifier: str,
        balance_ids: list[str],
    ) -> dict[tuple[tuple[str, str], ...], str]:
        records: dict[tuple[tuple[str, str], ...], str] = {}
        if not balance_ids:
            return records
        for key_chunk, filter_expression in balance_record_filter_chunks(balance_ids):
            response = client.get(
                bitable_records_url(table_identifier=table_identifier),
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "page_size": min(max(len(key_chunk), 20), 500),
                    "filter": filter_expression,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in {0, None}:
                raise RuntimeError(f"Feishu inventory balance table record lookup failed: {payload}")
            items = payload.get("data", {}).get("items", [])
            for item in items:
                if not isinstance(item, dict):
                    continue
                fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
                record_id = str(item.get("record_id") or item.get("id") or "")
                balance_id_value = str(fields.get("id") or "").strip()
                if record_id and balance_id_value:
                    records[balance_identity_key(balance_id_value)] = record_id
        return records

    def upsert_balance_table_records(
        *,
        token: str,
        table_identifier: str,
        field_rows: list[dict[str, Any]],
        table_fields: dict[str, dict[str, Any]],
    ) -> list[dict[str, str]]:
        prepared = []
        for index, fields in enumerate(field_rows):
            balance_id_value = str(fields.get("id") or "").strip()
            if not balance_id_value:
                raise RuntimeError("inventory balance table row is missing id")
            prepared.append(
                {
                    "index": index,
                    "balance_id": balance_id_value,
                    "fields": normalize_inventory_record_fields(fields, table_fields),
                }
            )
        existing_records = find_balance_table_records(
            token=token,
            table_identifier=table_identifier,
            balance_ids=[item["balance_id"] for item in prepared],
        )
        create_items = []
        update_items = []
        results: list[dict[str, str]] = [{} for _ in prepared]
        for item in prepared:
            record_id = existing_records.get(balance_identity_key(item["balance_id"]), "")
            if record_id:
                update_items.append(
                    {
                        "index": item["index"],
                        "record_id": record_id,
                        "fields": item["fields"],
                    }
                )
            else:
                create_items.append(item)
        updated_ids = update_inventory_table_records_batch(
            token=token,
            table_identifier=table_identifier,
            records=[
                {"record_id": item["record_id"], "fields": item["fields"]}
                for item in update_items
            ],
        )
        for index, item in enumerate(update_items):
            record_id = updated_ids[index] if index < len(updated_ids) else item["record_id"]
            results[item["index"]] = {"action": "updated", "record_id": record_id or item["record_id"]}
        created_ids = create_inventory_table_records_batch(
            token=token,
            table_identifier=table_identifier,
            records=[item["fields"] for item in create_items],
        )
        for index, item in enumerate(create_items):
            record_id = created_ids[index] if index < len(created_ids) else ""
            results[item["index"]] = {"action": "created", "record_id": record_id}
        return results

    def inventory_sync_result_item(row: dict[str, Any], result: dict[str, str]) -> dict[str, Any]:
        fields = row["fields"]
        return {
            "batch_key": row.get("batch_key"),
            "item_id": fields.get("Item ID") or row.get("item_id"),
            "warehouse_id": fields.get("Warehouse ID"),
            "location_code": fields.get("Location"),
            "batch_no": fields.get("Batch No") or row.get("batch_no"),
            "risk_level": fields.get("Risk Level"),
            "action": result.get("action"),
            "record_id": result.get("record_id"),
            "source_version": fields.get("Source Version", ""),
        }

    def inventory_sync_job_payload(job: InventoryTableSyncJobItem) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "item_id": job.item_id,
            "warehouse_id": job.warehouse_id,
            "location_code": job.location_code,
            "batch_no": job.batch_no,
        }

    def create_inventory_table_view(
        *,
        token: str,
        table_identifier: str,
        view_name: str,
        view_type: str,
    ) -> dict[str, str]:
        response = client.post(
            bitable_views_url(table_identifier),
            headers={"Authorization": f"Bearer {token}"},
            json={
                "view_name": view_name,
                "view_type": view_type,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu inventory table view create failed: {payload}")
        data = payload.get("data", {})
        view = data.get("view") if isinstance(data.get("view"), dict) else {}
        view_id = str(view.get("view_id") or data.get("view_id") or "")
        if not view_id:
            raise RuntimeError(f"Feishu inventory table view create returned no view_id: {payload}")
        return {"view_id": view_id, "action": "created"}

    def normalize_view_filter_value(value: Any, field: dict[str, Any]) -> str:
        if isinstance(value, list):
            values = value
        elif value is None:
            values = []
        else:
            values = [value]
        if field.get("type") == 3:
            values = [single_select_filter_value(field, item) for item in values]
        return json.dumps(values, ensure_ascii=False)

    def single_select_filter_value(field: dict[str, Any], value: Any) -> Any:
        text_value = str(value)
        for option in inventory_field_options(field):
            option_id = option.get("id") or ""
            option_name = option.get("name") or ""
            if text_value in {option_id, option_name}:
                return option_id or option_name
        return value

    def feishu_view_filter_operator(operator: str) -> str:
        return {
            "eq": "is",
            "=": "is",
            "ne": "isNot",
            "!=": "isNot",
            "lt": "isLess",
            "<": "isLess",
            "lte": "isLessEqual",
            "<=": "isLessEqual",
            "gt": "isGreater",
            ">": "isGreater",
            "gte": "isGreaterEqual",
            ">=": "isGreaterEqual",
            "contains": "contains",
            "not_contains": "doesNotContain",
            "does_not_contain": "doesNotContain",
            "is_empty": "isEmpty",
            "is_not_empty": "isNotEmpty",
        }.get(operator, operator)

    def is_primary_inventory_field(field: dict[str, Any], index: int) -> bool:
        return bool(
            field.get("is_primary")
            or field.get("primary")
            or field.get("isPrimary")
            or index == 0
        )

    def build_inventory_view_property(
        *,
        plan: dict[str, Any],
        fields_by_name: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        property_payload: dict[str, Any] = {}
        conditions: list[dict[str, Any]] = []
        for filter_rule in plan["filters"]:
            field = fields_by_name[filter_rule["field"]]
            conditions.append(
                {
                    "field_id": str(field.get("field_id") or ""),
                    "operator": feishu_view_filter_operator(filter_rule["operator"]),
                    "value": normalize_view_filter_value(filter_rule["value"], field),
                }
            )
        if conditions:
            property_payload["filter_info"] = {
                "conditions": conditions,
                "conjunction": "and",
            }
        if plan["visible_fields"]:
            visible = set(plan["visible_fields"])
            property_payload["hidden_fields"] = [
                str(field.get("field_id") or "")
                for index, (field_name, field) in enumerate(fields_by_name.items())
                if field_name not in visible
                and field.get("field_id")
                and not is_primary_inventory_field(field, index)
            ]
        return property_payload

    def apply_inventory_view_plan(
        *,
        token: str,
        table_identifier: str,
        view_id: str,
        plan: dict[str, Any],
        fields_by_name: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        property_payload = build_inventory_view_property(
            plan=plan,
            fields_by_name=fields_by_name,
        )
        request_payload: dict[str, Any] = {"view_name": plan["view_name"]}
        if property_payload:
            request_payload["property"] = property_payload
        response = client.patch(
            bitable_view_url(table_identifier, view_id),
            headers={"Authorization": f"Bearer {token}"},
            json=request_payload,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu inventory table view update failed: {payload}")
        return request_payload

    @app.get("/warehouse/inventory-table/schema")
    def get_inventory_table_schema(table_name: str = "Warehouse Inventory Snapshot") -> dict[str, Any]:
        started = perf_counter()
        if not inventory_table_provision_configured():
            return {
                "ok": False,
                "configured": False,
                "error": "missing_feishu_inventory_table_provision_config",
                "message": "Feishu inventory table schema requires app credentials and app token.",
            }
        normalized_table_name = table_name.strip() or "Warehouse Inventory Snapshot"
        try:
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            schema = build_inventory_table_schema(token=token, table_name=normalized_table_name)
            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id=f"inventory_table_schema:{schema['table_id']}",
                workflow="/warehouse/inventory-table/schema",
                status="succeeded",
                latency_ms=latency_ms,
                tool_calls=[
                    {
                        "tool": "warehouse_table_schema_tool",
                        "input": {"table_name": normalized_table_name},
                        "output": {
                            "table_id": schema["table_id"],
                            "field_count": len(schema["fields"]),
                            "view_count": len(schema["views"]),
                        },
                    }
                ],
            )
            return {
                "ok": True,
                "configured": True,
                "table_id": schema["table_id"],
                "table_name": normalized_table_name,
                "table_url": inventory_table_url_for(schema["table_id"]),
                "fields": schema["fields"],
                "views": schema["views"],
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
            write_inventory_table_run_log(
                event_id="inventory_table_schema:failed",
                workflow="/warehouse/inventory-table/schema",
                status="failed",
                latency_ms=latency_ms,
                error=message,
            )
            return {
                "ok": False,
                "configured": True,
                "error": "feishu_inventory_table_schema_failed",
                "message": message,
            }

    @app.get("/warehouse/inventory-table/view-templates")
    def list_inventory_view_templates() -> dict[str, Any]:
        templates = load_warehouse_view_templates()
        return {
            "ok": True,
            "templates": [
                {
                    "template_id": template.template_id,
                    "display_name": template.display_name,
                    "aliases": template.aliases,
                    "slots": template.slots,
                }
                for template in templates
            ],
        }

    @app.post("/warehouse/intents/route")
    def route_warehouse_intent_request(
        request: WarehouseIntentRouteRequest,
    ) -> dict[str, Any]:
        route = route_warehouse_intent(request.message).to_dict()
        route["payload"] = {"message": request.message}
        return route

    @app.post("/warehouse/inventory-table/views/create")
    def create_inventory_view(request: InventoryTableViewCreateRequest) -> dict[str, Any]:
        started = perf_counter()
        if not inventory_table_provision_configured():
            return {
                "ok": False,
                "configured": False,
                "error": "missing_feishu_inventory_table_provision_config",
                "message": "Feishu inventory view creation requires app credentials and app token.",
            }
        table_name = request.table_name.strip() or "Warehouse Inventory Snapshot"
        try:
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            table_result = resolve_inventory_table_for_schema(token=token, table_name=table_name)
            fields_by_name = fields_by_name_for_table(token=token, table_identifier=table_result["table_id"])
            views = list_inventory_table_views(token=token, table_identifier=table_result["table_id"])
            plan, missing_fields = validated_inventory_view_plan(request, fields_by_name)
            if not plan["view_name"]:
                missing_fields = [*missing_fields, "view_name"]
            if missing_fields:
                latency_ms = (perf_counter() - started) * 1000
                write_inventory_table_run_log(
                    event_id="inventory_table_view_create:invalid",
                    workflow="/warehouse/inventory-table/views/create",
                    status="failed",
                    latency_ms=latency_ms,
                    error=f"missing fields: {missing_fields}",
                    tool_calls=[
                        {
                            "tool": "warehouse_view_create_tool",
                            "input": request.model_dump(),
                            "output": {"missing_fields": missing_fields},
                        }
                    ],
                )
                return {
                    "ok": False,
                    "configured": True,
                    "error": "invalid_inventory_view_plan",
                    "message": "The requested view references fields that do not exist in the inventory table.",
                    "table_id": table_result["table_id"],
                    "missing_fields": missing_fields,
                    "available_fields": list(fields_by_name.keys()),
                    "validated_plan": plan,
                }
            for view in views:
                if view["view_name"] == plan["view_name"]:
                    applied_view_property = apply_inventory_view_plan(
                        token=token,
                        table_identifier=table_result["table_id"],
                        view_id=view["view_id"],
                        plan=plan,
                        fields_by_name=fields_by_name,
                    )
                    latency_ms = (perf_counter() - started) * 1000
                    write_inventory_table_run_log(
                        event_id=f"inventory_table_view_create:{view['view_id']}",
                        workflow="/warehouse/inventory-table/views/create",
                        status="succeeded",
                        latency_ms=latency_ms,
                        tool_calls=[
                            {
                                "tool": "warehouse_view_create_tool",
                                "input": request.model_dump(),
                                "output": {
                                    "action": "existing",
                                    "view_id": view["view_id"],
                                    "applied_view_property": applied_view_property,
                                },
                            }
                        ],
                    )
                    return {
                        "ok": True,
                        "configured": True,
                        "action": "existing",
                        "task_complete": True,
                        "table_id": table_result["table_id"],
                        "table_name": table_name,
                        "table_url": inventory_table_url_for(table_result["table_id"]),
                        "view_id": view["view_id"],
                        "view_name": view["view_name"],
                        "view_type": view["view_type"],
                        "validated_plan": plan,
                        "applied_view_property": applied_view_property,
                        "completion_message": (
                            f"Feishu inventory view {view['view_name']} already exists "
                            f"with view_id={view['view_id']}. Reply to the user now; "
                            "do not call warehouse_view_create_tool again for this view."
                        ),
                    }
            result = create_inventory_table_view(
                token=token,
                table_identifier=table_result["table_id"],
                view_name=plan["view_name"],
                view_type=plan["view_type"],
            )
            applied_view_property = apply_inventory_view_plan(
                token=token,
                table_identifier=table_result["table_id"],
                view_id=result["view_id"],
                plan=plan,
                fields_by_name=fields_by_name,
            )
            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id=f"inventory_table_view_create:{result['view_id']}",
                workflow="/warehouse/inventory-table/views/create",
                status="succeeded",
                latency_ms=latency_ms,
                tool_calls=[
                    {
                        "tool": "warehouse_view_create_tool",
                        "input": request.model_dump(),
                        "output": {
                            **result,
                            "applied_view_property": applied_view_property,
                        },
                    }
                ],
            )
            return {
                "ok": True,
                "configured": True,
                **result,
                "task_complete": True,
                "table_id": table_result["table_id"],
                "table_name": table_name,
                "table_url": inventory_table_url_for(table_result["table_id"]),
                "view_name": plan["view_name"],
                "view_type": plan["view_type"],
                "validated_plan": plan,
                "applied_view_property": applied_view_property,
                "completion_message": (
                    f"Feishu inventory view {plan['view_name']} was created "
                    f"with view_id={result['view_id']}. Reply to the user now; "
                    "do not call warehouse_view_create_tool again for this view."
                ),
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
            write_inventory_table_run_log(
                event_id="inventory_table_view_create:failed",
                workflow="/warehouse/inventory-table/views/create",
                status="failed",
                latency_ms=latency_ms,
                error=message,
            )
            return {
                "ok": False,
                "configured": True,
                "error": "feishu_inventory_table_view_create_failed",
                "message": message,
            }

    @app.post("/warehouse/inventory-table/views/from-template")
    def create_inventory_view_from_template(
        request: InventoryTableViewFromTemplateRequest,
    ) -> dict[str, Any]:
        match = match_warehouse_view_template(request.message)
        if not match.matched or not match.template_id:
            suggestions = match.suggestions or []
            suggestion_text = "、".join(suggestions)
            return {
                "ok": False,
                "matched": False,
                "error": match.error or "unknown_view_template",
                "message": f"未匹配到视图模板。可尝试：{suggestion_text}。",
                "suggestions": suggestions,
            }

        try:
            plan = render_warehouse_view_plan(
                template_id=match.template_id,
                view_name=request.view_name or match.view_name,
                slots=match.slots or {},
            )
        except ValueError as error:
            return {
                "ok": False,
                "matched": True,
                "template_id": match.template_id,
                "slots": match.slots or {},
                "error": "invalid_view_template_slots",
                "message": str(error),
            }

        create_request = InventoryTableViewCreateRequest.model_validate(plan)
        result = create_inventory_view(create_request)
        return {
            **result,
            "matched": True,
            "template_id": match.template_id,
            "slots": match.slots or {},
        }

    def inventory_balance_table_not_configured_response() -> dict[str, Any]:
        return {
            "ok": False,
            "configured": False,
            "error": "missing_feishu_inventory_balance_table_config",
            "message": "Feishu inventory balance table sync requires app credentials and app token.",
        }

    @app.post("/warehouse/inventory-balances-table/provision")
    def provision_inventory_balances_table(
        request: InventoryBalancesTableProvisionRequest,
    ) -> dict[str, Any]:
        started = perf_counter()
        if not inventory_table_provision_configured():
            return inventory_balance_table_not_configured_response()
        table_name = request.table_name.strip() or DEFAULT_INVENTORY_BALANCE_TABLE_NAME
        try:
            field_specs = inventory_balance_table_field_specs()
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            table_result = ensure_inventory_balance_table(
                token=token,
                table_name=table_name,
                field_specs=field_specs,
            )
            fields_by_name = fields_by_name_for_table(
                token=token,
                table_identifier=table_result["table_id"],
            )
            views = ensure_inventory_balance_table_views(
                token=token,
                table_identifier=table_result["table_id"],
                fields_by_name=fields_by_name,
            )
            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id=f"inventory_balance_table_provision:{table_result['table_id']}",
                workflow="/warehouse/inventory-balances-table/provision",
                status="succeeded",
                latency_ms=latency_ms,
                tool_calls=[
                    {
                        "tool": "warehouse_inventory_balances_table_provision_tool",
                        "input": {"table_name": table_name},
                        "output": table_result,
                    }
                ],
            )
            return {
                "ok": True,
                "configured": True,
                "table_name": table_name,
                "table_url": inventory_table_url_for(table_result["table_id"]),
                "views": views,
                **table_result,
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
            write_inventory_table_run_log(
                event_id="inventory_balance_table_provision:failed",
                workflow="/warehouse/inventory-balances-table/provision",
                status="failed",
                latency_ms=latency_ms,
                error=message,
            )
            return {
                "ok": False,
                "configured": True,
                "error": "feishu_inventory_balance_table_provision_failed",
                "message": message,
            }

    @app.post("/warehouse/inventory-balances-table/sync")
    def sync_inventory_balances_table(
        request: InventoryBalancesTableSyncRequest,
    ) -> dict[str, Any]:
        started = perf_counter()
        if not inventory_table_provision_configured():
            return inventory_balance_table_not_configured_response()
        table_name = request.table_name.strip() or DEFAULT_INVENTORY_BALANCE_TABLE_NAME
        limit = max(min(int(request.limit or 500), 500), 1)
        max_pages = max(min(int(request.max_pages or 50), 500), 1)
        try:
            field_specs = inventory_balance_table_field_specs()
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            table_result = ensure_inventory_balance_table(
                token=token,
                table_name=table_name,
                field_specs=field_specs,
            )
            fields_by_name = fields_by_name_for_table(
                token=token,
                table_identifier=table_result["table_id"],
            )
            views = ensure_inventory_balance_table_views(
                token=token,
                table_identifier=table_result["table_id"],
                fields_by_name=fields_by_name,
            )

            paginated_rows = fetch_business_table_rows_paginated(
                rows_endpoint="/warehouse/stock/balances/table-rows",
                request_payload={
                    "item_id": (request.item_id or "").strip() or None,
                    "warehouse_id": (request.warehouse_id or "").strip() or None,
                    "location_code": (request.location_code or "").strip() or None,
                    "limit": limit,
                },
                error_context="warehouse inventory balance table",
                max_pages=max_pages,
            )
            page_rows = paginated_rows["items"]
            page_count = int(paginated_rows["page_count"])
            upsert_results = upsert_balance_table_records(
                token=token,
                table_identifier=table_result["table_id"],
                field_rows=[row["fields"] for row in page_rows],
                table_fields=fields_by_name,
            )
            synced_items: list[dict[str, Any]] = []
            for row, upsert_result in zip(page_rows, upsert_results, strict=False):
                fields = row["fields"]
                synced_items.append(
                    {
                        "balance_id": fields.get("id") or row.get("balance_id"),
                        "item_id": fields.get("item_id") or row.get("item_id"),
                        "warehouse_id": fields.get("warehouse_id") or row.get("warehouse_id"),
                        "location_code": fields.get("location_code") or row.get("location_code"),
                        "quantity_on_hand": fields.get("quantity_on_hand"),
                        "action": upsert_result.get("action"),
                        "record_id": upsert_result.get("record_id"),
                    }
                )

            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id=f"inventory_balance_table_sync:{table_result['table_id']}",
                workflow="/warehouse/inventory-balances-table/sync",
                status="succeeded",
                latency_ms=latency_ms,
                tool_calls=[
                    {
                        "tool": "warehouse_inventory_balances_table_sync_tool",
                        "input": request.model_dump(),
                        "output": {"synced_count": len(synced_items), "page_count": page_count},
                    }
                ],
            )
            return {
                "ok": True,
                "configured": True,
                "synced_count": len(synced_items),
                "page_count": page_count,
                "items": synced_items,
                "views": views,
                "table_id": table_result["table_id"],
                "table_name": table_name,
                "table_url": inventory_table_url_for(table_result["table_id"]),
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
            write_inventory_table_run_log(
                event_id="inventory_balance_table_sync:failed",
                workflow="/warehouse/inventory-balances-table/sync",
                status="failed",
                latency_ms=latency_ms,
                error=message,
            )
            return {
                "ok": False,
                "configured": True,
                "error": "feishu_inventory_balance_table_sync_failed",
                "message": message,
            }

    @app.post("/warehouse/inventory-movements-table/sync")
    def sync_inventory_movements_table(request: InventoryMovementsTableSyncRequest) -> dict[str, Any]:
        return sync_procurement_table(
            request_payload={
                "order_id": (request.order_id or "").strip() or None,
                "movement_type": (request.movement_type or "").strip() or None,
                "item_id": (request.item_id or "").strip() or None,
                "warehouse_id": (request.warehouse_id or "").strip() or None,
                "limit": max(min(int(request.limit or 500), 500), 1),
            },
            table_name=request.table_name.strip() or DEFAULT_INVENTORY_MOVEMENT_TABLE_NAME,
            state=inventory_movement_table_state,
            schema_endpoint="/warehouse/inventory-movements/table-schema",
            rows_endpoint="/warehouse/inventory-movements/table-rows",
            identity_field="movement_id",
            workflow="/warehouse/inventory-movements-table/sync",
            tool_name="warehouse_inventory_movements_table_sync_tool",
            max_pages=max(min(int(request.max_pages or 50), 500), 1),
        )

    @app.post("/warehouse/inventory-table/provision")
    def provision_inventory_table(request: InventoryTableProvisionRequest) -> dict[str, Any]:
        started = perf_counter()
        if not inventory_table_provision_configured():
            return {
                "ok": False,
                "configured": False,
                "error": "missing_feishu_inventory_table_provision_config",
                "message": "Feishu inventory table provisioning requires app credentials and app token.",
            }
        table_name = request.table_name.strip() or "Warehouse Inventory Snapshot"
        try:
            field_specs = inventory_table_field_specs()
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            result = ensure_inventory_table(
                token=token,
                table_name=table_name,
                field_specs=field_specs,
            )
            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id=f"inventory_table_provision:{result['table_id']}",
                workflow="/warehouse/inventory-table/provision",
                status="succeeded",
                latency_ms=latency_ms,
                tool_calls=[
                    {
                        "tool": "warehouse_inventory_table_provision_tool",
                        "input": {"table_name": table_name},
                        "output": result,
                    }
                ],
            )
            return {
                "ok": True,
                "configured": True,
                "action": result["action"],
                **result,
                "table_url": inventory_table_url_for(result["table_id"]),
                "fields": [field["field_name"] for field in field_specs],
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
            write_inventory_table_run_log(
                event_id="inventory_table_provision:failed",
                workflow="/warehouse/inventory-table/provision",
                status="failed",
                latency_ms=latency_ms,
                error=message,
            )
            return {
                "ok": False,
                "configured": True,
                "error": "feishu_inventory_table_provision_failed",
                "message": message,
            }

    @app.post("/warehouse/inventory-table/sync")
    def sync_inventory_table(request: InventoryTableSyncRequest) -> dict[str, Any]:
        started = perf_counter()
        item_id = request.item_id.strip()
        if not inventory_table_sync_configured():
            return {
                "ok": False,
                "configured": False,
                "error": "missing_feishu_inventory_table_config",
                "message": "Feishu inventory table sync is not configured.",
            }
        try:
            rows = fetch_inventory_table_rows_with_fallback(
                item_id=item_id,
                warehouse_id=(request.warehouse_id or "").strip() or None,
                location_code=(request.location_code or "").strip() or None,
                batch_no=(request.batch_no or "").strip() or None,
                limit=1,
            )
            if not rows:
                raise RuntimeError(f"warehouse inventory table rows returned no data for item_id={item_id}")
            fields = rows[0]["fields"]
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            provision_result = ensure_inventory_table(
                token=token,
                table_name="Warehouse Inventory Snapshot",
            )
            table_fields = fields_by_name_for_table(
                token=token,
                table_identifier=provision_result["table_id"],
            )
            result = upsert_inventory_table_record(
                token=token,
                fields=normalize_inventory_record_fields(fields, table_fields),
            )
            latency_ms = (perf_counter() - started) * 1000
            write_sync_run_log(
                sku=item_id,
                status="succeeded",
                latency_ms=latency_ms,
                tool_calls=[
                    {
                        "tool": "warehouse_inventory_table_sync_tool",
                        "input": request.model_dump(),
                        "output": result,
                    }
                ],
            )
            return {
                "ok": True,
                "configured": True,
                "item_id": item_id,
                "batch_key": rows[0].get("batch_key"),
                "warehouse_id": fields.get("Warehouse ID"),
                "location_code": fields.get("Location"),
                "batch_no": fields.get("Batch No"),
                **result,
                "table_id": provision_result["table_id"],
                "table_url": inventory_table_url_for(provision_result["table_id"]),
                "last_synced_at": fields.get("Last Synced At", ""),
                "source_version": fields.get("Source Version", ""),
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            write_sync_run_log(
                sku=item_id,
                status="failed",
                latency_ms=latency_ms,
                error=str(error),
            )
            return {
                "ok": False,
                "configured": True,
                "item_id": item_id,
                "error": "feishu_inventory_table_sync_failed",
                "message": str(error),
            }

    @app.post("/warehouse/inventory-table/sync/filter")
    def sync_inventory_table_filter(
        request: InventoryTableSyncFilterRequest,
    ) -> dict[str, Any]:
        started = perf_counter()
        item_id = (request.item_id or request.sku or "").strip()
        sku = (request.sku or "").strip().lower()
        warehouse_id = (request.warehouse_id or "").strip()
        location_code = (request.location_code or "").strip()
        category = (request.category or "").strip()
        category_id = (request.category_id or "").strip()
        batch_no = (request.batch_no or "").strip()
        expiry_risk = (request.expiry_risk or "").strip()
        risk_level = (request.risk_level or "").strip()
        limit = max(min(int(request.limit or 50), 100), 1)
        if not inventory_table_sync_configured():
            return {
                "ok": False,
                "configured": False,
                "error": "missing_feishu_inventory_table_config",
                "message": "Feishu inventory table sync is not configured.",
            }
        try:
            paginated_rows = fetch_business_table_rows_paginated(
                rows_endpoint="/warehouse/inventory/table-rows",
                request_payload={
                    "item_id": item_id or None,
                    "sku": sku or None,
                    "warehouse_id": warehouse_id or None,
                    "location_code": location_code or None,
                    "category": category or None,
                    "category_id": category_id or None,
                    "batch_no": batch_no or None,
                    "expiry_risk": expiry_risk or None,
                    "risk_level": risk_level or None,
                    "limit": limit,
                },
                error_context="warehouse inventory table",
            )
            inventory_rows = paginated_rows["items"]
            page_count = int(paginated_rows["page_count"])
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            provision_result = ensure_inventory_table(
                token=token,
                table_name="Warehouse Inventory Snapshot",
            )
            table_fields = fields_by_name_for_table(
                token=token,
                table_identifier=provision_result["table_id"],
            )
            upsert_results = upsert_inventory_table_records(
                token=token,
                field_rows=[row["fields"] for row in inventory_rows],
                table_fields=table_fields,
            )
            synced_items: list[dict[str, Any]] = [
                inventory_sync_result_item(row, result)
                for row, result in zip(inventory_rows, upsert_results, strict=False)
            ]
            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id=f"inventory_table_sync_filter:{warehouse_id or risk_level or sku or 'all'}",
                workflow="/warehouse/inventory-table/sync/filter",
                status="succeeded",
                latency_ms=latency_ms,
                tool_calls=[
                    {
                        "tool": "warehouse_inventory_table_sync_tool",
                        "input": request.model_dump(),
                        "output": {"synced_count": len(synced_items), "page_count": page_count},
                    }
                ],
            )
            return {
                "ok": True,
                "configured": True,
                "item_id": item_id or None,
                "sku": sku or None,
                "warehouse_id": warehouse_id or None,
                "location_code": location_code or None,
                "category": category or None,
                "category_id": category_id or None,
                "batch_no": batch_no or None,
                "expiry_risk": expiry_risk or None,
                "risk_level": risk_level or None,
                "synced_count": len(synced_items),
                "page_count": page_count,
                "items": synced_items,
                "table_id": provision_result["table_id"],
                "table_url": inventory_table_url_for(provision_result["table_id"]),
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
            write_inventory_table_run_log(
                event_id="inventory_table_sync_filter:failed",
                workflow="/warehouse/inventory-table/sync/filter",
                status="failed",
                latency_ms=latency_ms,
                error=message,
            )
            return {
                "ok": False,
                "configured": True,
                "item_id": item_id or None,
                "sku": sku or None,
                "warehouse_id": warehouse_id or None,
                "location_code": location_code or None,
                "category": category or None,
                "category_id": category_id or None,
                "batch_no": batch_no or None,
                "expiry_risk": expiry_risk or None,
                "risk_level": risk_level or None,
                "error": "feishu_inventory_table_sync_filter_failed",
                "message": message,
            }

    @app.post("/warehouse/inventory-table/sync/jobs")
    def sync_inventory_table_jobs(request: InventoryTableSyncJobsRequest) -> dict[str, Any]:
        started = perf_counter()
        jobs = request.jobs[:100]
        limit_per_job = max(min(int(request.limit_per_job or 1), 10), 1)
        if not inventory_table_sync_configured():
            return {
                "ok": False,
                "configured": False,
                "error": "missing_feishu_inventory_table_config",
                "message": "Feishu inventory table sync is not configured.",
            }
        if not jobs:
            return {
                "ok": True,
                "configured": True,
                "processed_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "completed": [],
                "failed": [],
            }

        failed: list[dict[str, Any]] = []
        row_contexts: list[dict[str, Any]] = []
        for job in jobs:
            try:
                rows = fetch_inventory_table_rows_with_fallback(
                    item_id=(job.item_id or "").strip() or None,
                    warehouse_id=(job.warehouse_id or "").strip() or None,
                    location_code=(job.location_code or "").strip() or None,
                    batch_no=(job.batch_no or "").strip() or None,
                    limit=limit_per_job,
                )
                if not rows:
                    raise RuntimeError(f"warehouse inventory table rows returned no data for job_id={job.job_id}")
                for row in rows:
                    row_contexts.append({"job": job, "row": row})
            except (httpx.HTTPError, RuntimeError) as error:
                message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
                failed.append(
                    {
                        **inventory_sync_job_payload(job),
                        "error": message,
                    }
                )

        try:
            completed_by_job: dict[str, dict[str, Any]] = {}
            provision_result: dict[str, str] | None = None
            if row_contexts:
                token = get_tenant_access_token(
                    client=client,
                    app_id=table_app_id,
                    app_secret=table_app_secret,
                    api_base_url=api_base_url,
                )
                table_name = request.table_name.strip() or DEFAULT_INVENTORY_TABLE_NAME
                provision_result = ensure_inventory_table(
                    token=token,
                    table_name=table_name,
                )
                table_fields = fields_by_name_for_table(
                    token=token,
                    table_identifier=provision_result["table_id"],
                )
                upsert_results = upsert_inventory_table_records(
                    token=token,
                    field_rows=[context["row"]["fields"] for context in row_contexts],
                    table_fields=table_fields,
                )
                table_url = inventory_table_url_for(provision_result["table_id"])
                for context, result in zip(row_contexts, upsert_results, strict=False):
                    job = context["job"]
                    row = context["row"]
                    entry = completed_by_job.setdefault(
                        job.job_id,
                        {
                            **inventory_sync_job_payload(job),
                            "sync": {
                                "ok": True,
                                "configured": True,
                                "synced_count": 0,
                                "items": [],
                                "table_id": provision_result["table_id"],
                                "table_url": table_url,
                            },
                        },
                    )
                    entry["sync"]["items"].append(inventory_sync_result_item(row, result))
                    entry["sync"]["synced_count"] = len(entry["sync"]["items"])

            completed = list(completed_by_job.values())
            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id=f"inventory_table_sync_jobs:{len(jobs)}",
                workflow="/warehouse/inventory-table/sync/jobs",
                status="succeeded" if not failed else "partial_failed",
                latency_ms=latency_ms,
                tool_calls=[
                    {
                        "tool": "warehouse_inventory_sync_jobs_tool",
                        "input": {
                            "job_count": len(jobs),
                            "limit_per_job": limit_per_job,
                        },
                        "output": {
                            "completed_count": len(completed),
                            "failed_count": len(failed),
                        },
                    }
                ],
            )
            return {
                "ok": not failed,
                "configured": True,
                "processed_count": len(jobs),
                "completed_count": len(completed),
                "failed_count": len(failed),
                "completed": completed,
                "failed": failed,
                "table_id": provision_result["table_id"] if provision_result else None,
                "table_url": inventory_table_url_for(provision_result["table_id"]) if provision_result else None,
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
            write_inventory_table_run_log(
                event_id="inventory_table_sync_jobs:failed",
                workflow="/warehouse/inventory-table/sync/jobs",
                status="failed",
                latency_ms=latency_ms,
                error=message,
            )
            return {
                "ok": False,
                "configured": True,
                "processed_count": len(jobs),
                "completed_count": 0,
                "failed_count": len(jobs),
                "completed": [],
                "failed": [
                    {
                        **inventory_sync_job_payload(job),
                        "error": message,
                    }
                    for job in jobs
                ],
                "error": "feishu_inventory_table_sync_jobs_failed",
                "message": message,
            }

    def procurement_table_not_configured_response() -> dict[str, Any]:
        return {
            "ok": False,
            "configured": False,
            "error": "missing_feishu_procurement_table_config",
            "message": "Feishu procurement table sync is not configured.",
        }

    def provision_procurement_table(
        *,
        request: ProcurementTableProvisionRequest,
        default_table_name: str,
        state: dict[str, str],
        schema_endpoint: str,
        workflow: str,
        prune_extra_fields_enabled: bool = False,
    ) -> dict[str, Any]:
        started = perf_counter()
        if not procurement_table_configured():
            return procurement_table_not_configured_response()
        table_name = (request.table_name or default_table_name).strip() or default_table_name
        try:
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            result = ensure_procurement_table(
                token=token,
                table_name=table_name,
                state=state,
                schema_endpoint=schema_endpoint,
                prune_extra_fields_enabled=prune_extra_fields_enabled,
            )
            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id=f"procurement_table_provision:{result['table_id']}",
                workflow=workflow,
                status="succeeded",
                latency_ms=latency_ms,
            )
            return {
                "ok": True,
                "configured": True,
                "table_name": table_name,
                "table_url": procurement_table_url_for(state, result["table_id"]),
                **result,
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
            write_inventory_table_run_log(
                event_id="procurement_table_provision:failed",
                workflow=workflow,
                status="failed",
                latency_ms=latency_ms,
                error=message,
            )
            return {
                "ok": False,
                "configured": True,
                "error": "feishu_procurement_table_provision_failed",
                "message": message,
            }

    def sync_procurement_table(
        *,
        request_payload: dict[str, Any],
        table_name: str,
        state: dict[str, str],
        schema_endpoint: str,
        rows_endpoint: str,
        identity_field: str,
        workflow: str,
        prune_extra_fields_enabled: bool = False,
        max_pages: int = 500,
        tool_name: str | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        if not procurement_table_configured():
            return procurement_table_not_configured_response()
        try:
            paginated_rows = fetch_business_table_rows_paginated(
                rows_endpoint=rows_endpoint,
                request_payload=request_payload,
                error_context="procurement table",
                max_pages=max_pages,
            )
            rows = paginated_rows["items"]
            page_count = int(paginated_rows["page_count"])
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            table_result = ensure_procurement_table(
                token=token,
                table_name=table_name,
                state=state,
                schema_endpoint=schema_endpoint,
                prune_extra_fields_enabled=prune_extra_fields_enabled,
            )
            synced_items: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("fields"), dict):
                    continue
                fields = row["fields"]
                result = upsert_procurement_table_record(
                    token=token,
                    table_identifier=table_result["table_id"],
                    identity_field=identity_field,
                    fields=fields,
                )
                item: dict[str, Any] = {
                    "status": fields.get("Status") or fields.get("Warehouse Sync Status"),
                    "action": result["action"],
                    "record_id": result["record_id"],
                    "source_version": fields.get("Source Version", ""),
                }
                item["purchase_order_id"] = fields.get("Purchase Order ID")
                item["approval_status"] = fields.get("Approval Status")
                item["payment_status"] = fields.get("Payment Status")
                item["warehouse_sync_status"] = fields.get("Warehouse Sync Status")
                item[identity_field] = fields.get(identity_field)
                synced_items.append(item)
            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id=f"procurement_table_sync:{table_result['table_id']}",
                workflow=workflow,
                status="succeeded",
                latency_ms=latency_ms,
                tool_calls=[
                    {
                        "tool": tool_name or workflow.rsplit("/", 1)[-1],
                        "input": request_payload,
                        "output": {"synced_count": len(synced_items), "page_count": page_count},
                    }
                ],
            )
            return {
                "ok": True,
                "configured": True,
                "synced_count": len(synced_items),
                "page_count": page_count,
                "items": synced_items,
                "table_id": table_result["table_id"],
                "table_name": table_name,
                "table_url": procurement_table_url_for(state, table_result["table_id"]),
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
            write_inventory_table_run_log(
                event_id="procurement_table_sync:failed",
                workflow=workflow,
                status="failed",
                latency_ms=latency_ms,
                error=message,
            )
            return {
                "ok": False,
                "configured": True,
                "error": "feishu_procurement_table_sync_failed",
                "message": message,
            }

    def business_table_not_configured_response(*, error: str, message: str) -> dict[str, Any]:
        """Return a clear not-configured envelope for Feishu business tables.

        Args:
            error: Stable machine-readable error code for the table family.
            message: Human-readable explanation returned to schedulers and
                manual API callers.

        Returns:
            A response dictionary that mirrors existing procurement table
            behavior while making the missing table family explicit.
        """

        return {
            "ok": False,
            "configured": False,
            "error": error,
            "message": message,
        }

    def oss_signed_url(*, bucket: str, object_key: str) -> str:
        """Create a short-lived Aliyun OSS GET URL for private product images.

        Args:
            bucket: OSS bucket that stores product images.
            object_key: Object path inside the bucket, without a leading slash.

        Returns:
            A signed HTTPS URL that the adapter can download before uploading
            the image content to Feishu.

        Raises:
            RuntimeError: If any required OSS credential or location setting is
                missing. The caller reports this as a non-blocking image upload
                failure so the business row still syncs.
        """

        if not (
            aliyun_oss_access_key_id
            and aliyun_oss_access_key_secret
            and aliyun_oss_endpoint
            and bucket
            and object_key
        ):
            raise RuntimeError("Aliyun OSS credentials are required for private OSS image URLs")
        endpoint = aliyun_oss_endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        normalized_key = object_key.lstrip("/")
        expires = int(datetime.now(UTC).timestamp()) + 900
        string_to_sign = f"GET\n\n\n{expires}\n/{bucket}/{normalized_key}"
        signature = base64.b64encode(
            hmac.new(
                aliyun_oss_access_key_secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")
        encoded_key = quote(normalized_key, safe="/")
        return (
            f"https://{bucket}.{endpoint}/{encoded_key}"
            f"?OSSAccessKeyId={quote(aliyun_oss_access_key_id, safe='')}"
            f"&Expires={expires}"
            f"&Signature={quote(signature, safe='')}"
        )

    def resolve_image_source_url(image_url: str) -> str:
        """Resolve a database image reference into a downloadable URL.

        Args:
            image_url: Raw value from `items.image`. It may be a public HTTPS
                URL, an `oss://bucket/key` URI, or a bare OSS object key when
                the bucket is supplied by environment configuration.

        Returns:
            A URL suitable for `httpx.Client.get`.

        Raises:
            RuntimeError: If a private OSS-style reference cannot be signed
                because Aliyun OSS configuration is incomplete.
        """

        value = str(image_url or "").strip()
        if not value:
            raise RuntimeError("Image URL is empty")
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"}:
            return value
        if parsed.scheme == "oss":
            bucket = parsed.netloc or aliyun_oss_bucket
            return oss_signed_url(bucket=bucket, object_key=parsed.path.lstrip("/"))
        if aliyun_oss_bucket:
            return oss_signed_url(bucket=aliyun_oss_bucket, object_key=value)
        raise RuntimeError("Image URL is not directly downloadable and OSS bucket is not configured")

    def download_image_content(image_url: str) -> dict[str, Any]:
        """Download product image bytes for Feishu upload.

        Args:
            image_url: Raw source image URL from the Items read model.

        Returns:
            A dictionary containing resolved URL, image bytes, content hash,
            guessed filename, and MIME type.

        Raises:
            RuntimeError: If the source URL cannot be resolved or the download
                response is not successful.
        """

        resolved_url = resolve_image_source_url(image_url)
        response = client.get(resolved_url)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            reason_phrase = error.response.reason_phrase or "HTTP error"
            raise RuntimeError(
                f"Unable to download product image: HTTP {status_code} {reason_phrase}"
            ) from error
        content = response.content
        if not content:
            raise RuntimeError("Downloaded product image is empty")
        content_hash = hashlib.sha256(content).hexdigest()
        parsed_path = urlparse(resolved_url).path
        filename = Path(parsed_path).name or "product-image.jpg"
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        mime_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return {
            "resolved_url": resolved_url,
            "content": content,
            "content_hash": content_hash,
            "filename": filename,
            "mime_type": mime_type,
        }

    def upload_image_to_feishu(
        *,
        token: str,
        filename: str,
        content: bytes,
        mime_type: str,
    ) -> str:
        """Upload one product image to Feishu and return its `file_token`.

        Args:
            token: Tenant access token used for Feishu OpenAPI calls.
            filename: Display filename sent with the multipart upload.
            content: Image bytes downloaded from the source catalog URL.
            mime_type: MIME type used by Feishu to classify the attachment.

        Returns:
            Feishu `file_token` that can be written into a Bitable image or
            attachment field as `[{"file_token": "..."}]`.

        Raises:
            RuntimeError: If Feishu rejects the upload or omits `file_token`.
        """

        response = client.post(
            f"{api_base_url}/open-apis/drive/v1/medias/upload_all",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "file_name": filename,
                "parent_type": "bitable_image",
                "parent_node": table_app_token,
                "size": str(len(content)),
            },
            files={"file": (filename, content, mime_type)},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(f"Unable to upload image to Feishu: {describe_http_error(error)}") from error
        payload = response.json()
        if payload.get("code", 0) != 0:
            raise RuntimeError(f"Feishu image upload failed: {payload}")
        file_token = str((payload.get("data") or {}).get("file_token") or "").strip()
        if not file_token:
            raise RuntimeError(f"Feishu image upload did not return file_token: {payload}")
        return file_token

    def get_or_upload_feishu_image_token(*, token: str, image_url: str) -> dict[str, Any]:
        """Return a cached or newly uploaded Feishu image token for a URL.

        Args:
            token: Tenant access token used when a new upload is required.
            image_url: Raw image URL from the Items read model.

        Returns:
            A dictionary with `file_token`, `content_hash`, and `status` where
            status is either `uploaded` or `reused`.

        Side Effects:
            Downloads the image to compute its content hash, uploads it to
            Feishu when the cached hash differs, and persists the cache file
            after a successful upload.
        """

        image = download_image_content(image_url)
        content_hash = str(image["content_hash"])
        with image_token_cache_lock:
            cached = image_token_cache.get(image_url)
            if cached and cached.get("content_hash") == content_hash and cached.get("file_token"):
                return {
                    "file_token": cached["file_token"],
                    "content_hash": content_hash,
                    "status": "reused",
                }
        file_token = upload_image_to_feishu(
            token=token,
            filename=str(image["filename"]),
            content=image["content"],
            mime_type=str(image["mime_type"]),
        )
        with image_token_cache_lock:
            image_token_cache[image_url] = {
                "content_hash": content_hash,
                "file_token": file_token,
            }
            try:
                save_feishu_image_token_cache(image_token_cache_path, image_token_cache)
            except OSError as error:
                logger.warning("failed to persist Feishu image token cache: %s", error)
        return {
            "file_token": file_token,
            "content_hash": content_hash,
            "status": "uploaded",
        }

    def build_items_table_record_fields(
        *,
        token: str,
        source_fields: dict[str, Any],
        image_upload_summary: dict[str, Any],
        current_table_fields: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Convert Items read-model fields into Feishu-ready record fields.

        Args:
            token: Tenant access token used for image uploads.
            source_fields: Field dictionary returned by mock-api. It must keep
                `Image URL` as the raw text value and may omit `Product Image`.
            image_upload_summary: Mutable per-sync summary that receives upload
                counts and non-blocking failure details.
            current_table_fields: Feishu table fields that already exist on the
                target Items table. Legacy tables may still contain an `Image`
                text field from the first H9 implementation, and this function
                backfills that field only when it is actually present.

        Returns:
            A copy of the source fields where `Product Image` contains a Feishu
            `file_token` list when upload succeeds. On failure the raw `Image
            URL` remains and `Product Image` is omitted. Existing legacy
            `Image` columns receive the same raw image reference as `Image URL`.
        """

        fields = dict(source_fields)
        image_url = str(fields.get("Image URL") or "").strip()
        fields.pop("Image", None)
        fields.pop("Product Image", None)
        if not image_url:
            return fields
        legacy_image_field = current_table_fields.get("Image") or {}
        if legacy_image_field.get("type") == 1:
            fields["Image"] = image_url
        try:
            upload_result = get_or_upload_feishu_image_token(token=token, image_url=image_url)
        except (RuntimeError, httpx.HTTPError, ValueError) as error:
            image_upload_summary["failed_count"] += 1
            image_upload_summary["failures"].append(
                {
                    "image_url": image_url,
                    "reason": str(error),
                }
            )
            return fields
        if upload_result["status"] == "reused":
            image_upload_summary["reused_count"] += 1
        else:
            image_upload_summary["uploaded_count"] += 1
        fields["Product Image"] = [{"file_token": upload_result["file_token"]}]
        return fields

    def sync_business_table(
        *,
        request_payload: dict[str, Any],
        table_name: str,
        state: dict[str, str],
        schema_endpoint: str,
        rows_endpoint: str,
        identity_field: str,
        workflow: str,
        not_configured_error: str,
        not_configured_message: str,
        failure_error: str,
        item_builder: Any,
        field_transformer: Any | None = None,
        stale_field_names: set[str] | None = None,
        response_extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sync a mock-api business read model into a Feishu bitable table.

        Args:
            request_payload: JSON body sent to the mock-api row endpoint.
            table_name: Feishu table name created when no configured table
                exists.
            state: Mutable table state containing table id, view id, and URL.
            schema_endpoint: mock-api schema endpoint used to create fields.
            rows_endpoint: mock-api rows endpoint used as the source read model.
            identity_field: Feishu field used to find existing records.
            workflow: Run-log workflow identifier.
            not_configured_error: Error code returned when Feishu table
                credentials are missing.
            not_configured_message: Message returned with the missing-config
                response.
            failure_error: Error code returned for runtime sync failures.
            item_builder: Callable that converts source fields and upsert result
                into the compact `items[]` response row.
            field_transformer: Optional callable that can adapt source fields
                before Feishu upsert. Items uses this to convert raw image URLs
                into Feishu `file_token` attachments.
            stale_field_names: Optional Feishu field names that should be
                removed from an existing table after the backend schema stops
                emitting them. Button fields are never pruned through this path
                so manually maintained Feishu actions remain intact.
            response_extras: Optional extra keys appended to successful sync
                responses, such as non-blocking image upload summaries.

        Returns:
            A sync result containing table metadata, count, and per-row actions.
        """

        started = perf_counter()
        if not procurement_table_configured():
            return business_table_not_configured_response(
                error=not_configured_error,
                message=not_configured_message,
            )
        try:
            paginated_rows = fetch_business_table_rows_paginated(
                rows_endpoint=rows_endpoint,
                request_payload=request_payload,
                error_context="business table",
            )
            rows = paginated_rows["items"]
            page_count = int(paginated_rows["page_count"])
            field_specs = procurement_table_field_specs(schema_endpoint)
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            table_result = ensure_procurement_table(
                token=token,
                table_name=table_name,
                state=state,
                schema_endpoint=schema_endpoint,
                field_specs=field_specs,
            )
            if stale_field_names:
                existing_fields = fields_by_name_for_table(token=token, table_identifier=table_result["table_id"])
                for field_name in stale_field_names:
                    field = existing_fields.get(field_name)
                    if not field or field.get("is_primary") or int(field.get("type") or 0) == 3001:
                        continue
                    field_id = str(field.get("field_id") or "")
                    if field_id:
                        delete_inventory_table_field(
                            token=token,
                            table_identifier=table_result["table_id"],
                            field_id=field_id,
                        )
            current_table_fields = (
                fields_by_name_for_table(token=token, table_identifier=table_result["table_id"])
                if field_transformer is not None
                else {}
            )
            synced_items: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("fields"), dict):
                    continue
                fields = row["fields"]
                if field_transformer is not None:
                    fields = field_transformer(fields, token, current_table_fields)
                upsert_result = upsert_procurement_table_record(
                    token=token,
                    table_identifier=table_result["table_id"],
                    identity_field=identity_field,
                    fields=fields,
                    field_specs=field_specs,
                )
                synced_items.append(item_builder(row, fields, upsert_result))
            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id=f"{workflow.rsplit('/', 1)[-1]}:{table_result['table_id']}",
                workflow=workflow,
                status="succeeded",
                latency_ms=latency_ms,
                tool_calls=[
                    {
                        "tool": workflow.rsplit("/", 1)[-1],
                        "input": request_payload,
                        "output": {"synced_count": len(synced_items), "page_count": page_count},
                    }
                ],
            )
            result = {
                "ok": True,
                "configured": True,
                "synced_count": len(synced_items),
                "page_count": page_count,
                "items": synced_items,
                "table_id": table_result["table_id"],
                "table_name": table_name,
                "table_url": procurement_table_url_for(state, table_result["table_id"]),
            }
            if response_extras:
                result.update(response_extras)
            return result
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            message = describe_http_error(error) if isinstance(error, httpx.HTTPError) else str(error)
            write_inventory_table_run_log(
                event_id=f"{workflow.rsplit('/', 1)[-1]}:failed",
                workflow=workflow,
                status="failed",
                latency_ms=latency_ms,
                error=message,
            )
            return {
                "ok": False,
                "configured": True,
                "error": failure_error,
                "message": message,
            }

    @app.post("/procurement/purchase-orders-table/provision")
    def provision_procurement_purchase_orders_table(
        request: ProcurementTableProvisionRequest,
    ) -> dict[str, Any]:
        return provision_procurement_table(
            request=request,
            default_table_name="Procurement Purchase Orders",
            state=procurement_purchase_order_table_state,
            schema_endpoint="/procurement/purchase-orders/table-schema",
            workflow="/procurement/purchase-orders-table/provision",
            prune_extra_fields_enabled=True,
        )

    @app.post("/procurement/purchase-orders-table/sync")
    def sync_procurement_purchase_orders_table(
        request: ProcurementPurchaseOrderTableSyncRequest,
    ) -> dict[str, Any]:
        return sync_procurement_table(
            request_payload={
                "purchase_order_id": request.purchase_order_id,
                "approval_status": request.approval_status,
                "warehouse_sync_status": request.warehouse_sync_status,
                "payment_status": request.payment_status,
                "limit": max(min(int(request.limit or 100), 500), 1),
            },
            table_name="Procurement Purchase Orders",
            state=procurement_purchase_order_table_state,
            schema_endpoint="/procurement/purchase-orders/table-schema",
            rows_endpoint="/procurement/purchase-orders/table-rows",
            identity_field="Purchase Order ID",
            workflow="/procurement/purchase-orders-table/sync",
            prune_extra_fields_enabled=True,
        )

    @app.post("/orders/fulfillment-table/sync")
    def sync_order_fulfillment_table(
        request: OrderFulfillmentTableSyncRequest,
    ) -> dict[str, Any]:
        """Sync Order Fulfillment rows into the Feishu business read model."""

        return sync_business_table(
            request_payload={
                "order_id": request.order_id,
                "status": request.status,
                "limit": max(min(int(request.limit or 100), 500), 1),
            },
            table_name="Order Fulfillment",
            state=order_fulfillment_table_state,
            schema_endpoint="/warehouse/orders/fulfillment/table-schema",
            rows_endpoint="/warehouse/orders/fulfillment/table-rows",
            identity_field="order_id",
            workflow="/orders/fulfillment-table/sync",
            not_configured_error="missing_feishu_order_fulfillment_table_config",
            not_configured_message="Feishu order fulfillment table sync is not configured.",
            failure_error="feishu_order_fulfillment_table_sync_failed",
            stale_field_names={"id"},
            item_builder=lambda row, fields, result: {
                "order_id": fields.get("order_id") or row.get("order_id"),
                "status": fields.get("status"),
                "action": result["action"],
                "record_id": result["record_id"],
            },
        )

    @app.post("/orders/items-table/sync")
    def sync_order_items_table(
        request: OrderItemsTableSyncRequest,
    ) -> dict[str, Any]:
        """Sync Order Items rows into the Feishu business read model.

        Args:
            request: Filter and limit payload received from n8n or a manual
                operator-triggered sync call.

        Returns:
            Shared table-sync result containing table identity, table URL,
            synced count, and per-row upsert actions.

        Side Effects:
            Reads mock-api order item rows, ensures the configured Bitable
            fields exist, and upserts records by `Order Item ID`.
        """

        return sync_business_table(
            request_payload={
                "order_id": request.order_id,
                "status": request.status,
                "limit": max(min(int(request.limit or 100), 500), 1),
            },
            table_name="Order Items",
            state=order_items_table_state,
            schema_endpoint="/warehouse/orders/items/table-schema",
            rows_endpoint="/warehouse/orders/items/table-rows",
            identity_field="Order Item ID",
            workflow="/orders/items-table/sync",
            not_configured_error="missing_feishu_order_items_table_config",
            not_configured_message="Feishu order items table sync is not configured.",
            failure_error="feishu_order_items_table_sync_failed",
            item_builder=lambda row, fields, result: {
                "order_item_id": fields.get("Order Item ID") or row.get("order_item_id"),
                "order_id": fields.get("Order ID"),
                "status": fields.get("Status"),
                "action": result["action"],
                "record_id": result["record_id"],
                "source_version": fields.get("Source Version", ""),
            },
        )

    @app.post("/items/table/sync")
    def sync_items_table(
        request: ItemsTableSyncRequest,
    ) -> dict[str, Any]:
        """Sync standalone Items rows into the Feishu business read model.

        Args:
            request: Optional category filter and row limit.

        Returns:
            Shared table-sync result containing table identity, table URL,
            synced count, and per-row upsert actions.

        Side Effects:
            Reads mock-api catalog rows, ensures the Items Bitable schema, and
            upserts records by `Item ID`.
        """

        image_upload_summary = {
            "uploaded_count": 0,
            "reused_count": 0,
            "failed_count": 0,
            "failures": [],
        }
        return sync_business_table(
            request_payload={
                "category_id": request.category_id,
                "limit": max(min(int(request.limit or 100), 500), 1),
            },
            table_name="Items",
            state=items_table_state,
            schema_endpoint="/items/table-schema",
            rows_endpoint="/items/table-rows",
            identity_field="Item ID",
            workflow="/items/table/sync",
            not_configured_error="missing_feishu_items_table_config",
            not_configured_message="Feishu items table sync is not configured.",
            failure_error="feishu_items_table_sync_failed",
            field_transformer=lambda fields, token, current_table_fields: build_items_table_record_fields(
                token=token,
                source_fields=fields,
                image_upload_summary=image_upload_summary,
                current_table_fields=current_table_fields,
            ),
            response_extras={"image_upload": image_upload_summary},
            item_builder=lambda row, fields, result: {
                "item_id": fields.get("Item ID") or row.get("item_id"),
                "action": result["action"],
                "record_id": result["record_id"],
                "source_version": fields.get("Source Version", ""),
            },
        )

    @app.post("/products/operations-table/sync")
    def sync_product_operations_table(
        request: ProductOperationsTableSyncRequest,
    ) -> dict[str, Any]:
        """Sync Product Operations rows into the Feishu business read model."""

        return sync_business_table(
            request_payload={
                "category_id": request.category_id,
                "limit": max(min(int(request.limit or 100), 500), 1),
            },
            table_name="Product Operations",
            state=product_operations_table_state,
            schema_endpoint="/products/operations/table-schema",
            rows_endpoint="/products/operations/table-rows",
            identity_field="Item ID",
            workflow="/products/operations-table/sync",
            not_configured_error="missing_feishu_product_operations_table_config",
            not_configured_message="Feishu product operations table sync is not configured.",
            failure_error="feishu_product_operations_table_sync_failed",
            item_builder=lambda row, fields, result: {
                "item_id": fields.get("Item ID") or row.get("item_id"),
                "status": fields.get("Flash Deal Status"),
                "action": result["action"],
                "record_id": result["record_id"],
                "source_version": fields.get("Source Version", ""),
            },
        )

    @app.post("/flash-sales/table/sync")
    def sync_flash_sales_table(
        request: FlashSalesTableSyncRequest,
    ) -> dict[str, Any]:
        """Sync Flash Sales rows into the Feishu business read model.

        Args:
            request: Optional status filter and row limit.

        Returns:
            Shared table-sync result containing table identity, table URL,
            synced count, and per-row upsert actions.

        Side Effects:
            Reads mock-api flash-sale activities, ensures the Flash Sales
            Bitable schema, and upserts records by `Flash Sale ID`.
        """

        return sync_business_table(
            request_payload={
                "status": request.status,
                "limit": max(min(int(request.limit or 100), 500), 1),
            },
            table_name="Flash Sales",
            state=flash_sales_table_state,
            schema_endpoint="/flash-sales/table-schema",
            rows_endpoint="/flash-sales/table-rows",
            identity_field="Flash Sale ID",
            workflow="/flash-sales/table/sync",
            not_configured_error="missing_feishu_flash_sales_table_config",
            not_configured_message="Feishu flash sales table sync is not configured.",
            failure_error="feishu_flash_sales_table_sync_failed",
            item_builder=lambda row, fields, result: {
                "flash_sale_id": fields.get("Flash Sale ID") or row.get("flash_sale_id"),
                "status": fields.get("Status"),
                "action": result["action"],
                "record_id": result["record_id"],
                "source_version": fields.get("Source Version", ""),
            },
        )

    @app.post("/flash-sales/claims-table/sync")
    def sync_flash_sale_claims_table(
        request: FlashSaleClaimsTableSyncRequest,
    ) -> dict[str, Any]:
        """Sync Flash Sale Claims rows into the Feishu business read model.

        Args:
            request: Optional flash-sale id, optional claim status, and row
                limit.

        Returns:
            Shared table-sync result containing table identity, table URL,
            synced count, and per-row upsert actions.

        Side Effects:
            Reads mock-api claim-result rows, ensures the Flash Sale Claims
            Bitable schema, and upserts records by `Claim ID`.
        """

        return sync_business_table(
            request_payload={
                "flash_sale_id": request.flash_sale_id,
                "status": request.status,
                "limit": max(min(int(request.limit or 100), 500), 1),
            },
            table_name="Flash Sale Claims",
            state=flash_sale_claims_table_state,
            schema_endpoint="/flash-sales/claims/table-schema",
            rows_endpoint="/flash-sales/claims/table-rows",
            identity_field="Claim ID",
            workflow="/flash-sales/claims-table/sync",
            not_configured_error="missing_feishu_flash_sale_claims_table_config",
            not_configured_message="Feishu flash sale claims table sync is not configured.",
            failure_error="feishu_flash_sale_claims_table_sync_failed",
            item_builder=lambda row, fields, result: {
                "claim_id": fields.get("Claim ID") or row.get("claim_id"),
                "flash_sale_id": fields.get("Flash Sale ID"),
                "status": fields.get("Status"),
                "action": result["action"],
                "record_id": result["record_id"],
                "source_version": fields.get("Source Version", ""),
            },
        )

    def process_message(bot: BotConfig, message: Any) -> None:
        total_started = perf_counter()
        n8n_ms = 0.0
        token_ms = 0.0
        reply_ms = 0.0
        tool_calls: list[Any] = []
        workflow_override: str | None = None
        route_status = 200
        try:
            n8n_started = perf_counter()
            n8n_payload = procurement_arrival_fast_path_payload(bot, message)
            if n8n_payload is None:
                n8n_response = client.post(bot.n8n_webhook_url, json=to_n8n_payload(message))
                n8n_response.raise_for_status()
                route_status = n8n_response.status_code
                n8n_payload = n8n_response.json()
            else:
                workflow_override = str(n8n_payload.get("workflow") or "")
            n8n_ms = (perf_counter() - n8n_started) * 1000
            tool_calls = tool_calls_from_n8n_payload(n8n_payload)
        except httpx.HTTPError as error:
            total_ms = (perf_counter() - total_started) * 1000
            logger.error(
                "failed to forward feishu bot=%s message_id=%s to n8n error=%s",
                bot.name,
                message.message_id,
                error,
            )
            write_run_log(
                bot=bot,
                message=message,
                status="failed",
                total_ms=total_ms,
                n8n_ms=n8n_ms,
                error=str(error),
                workflow=workflow_override,
            )
            return

        reply = n8n_payload.get("reply") or n8n_payload.get("answer")
        logger.info(
            "forwarded feishu bot=%s message_id=%s to n8n status=%s has_reply=%s n8n_ms=%.1f",
            bot.name,
            message.message_id,
            route_status,
            bool(reply),
            n8n_ms,
        )

        if not bot.app_id or not bot.app_secret or not reply:
            total_ms = (perf_counter() - total_started) * 1000
            logger.info(
                "feishu bot=%s message_id=%s completed total_ms=%.1f n8n_ms=%.1f token_ms=%.1f reply_ms=%.1f",
                bot.name,
                message.message_id,
                total_ms,
                n8n_ms,
                token_ms,
                reply_ms,
            )
            write_run_log(
                bot=bot,
                message=message,
                status="succeeded",
                total_ms=total_ms,
                n8n_ms=n8n_ms,
                token_ms=token_ms,
                reply_ms=reply_ms,
                has_reply=bool(reply),
                tool_calls=tool_calls,
                workflow=workflow_override,
            )
            return

        status = "succeeded"
        error_text = None
        try:
            token_started = perf_counter()
            tenant_access_token = get_tenant_access_token(
                client=client,
                app_id=bot.app_id,
                app_secret=bot.app_secret,
                api_base_url=bot.api_base_url,
            )
            token_ms = (perf_counter() - token_started) * 1000
            reply_started = perf_counter()
            reply_text_message(
                client=client,
                tenant_access_token=tenant_access_token,
                message_id=message.message_id,
                text=str(reply),
                api_base_url=bot.api_base_url,
            )
            reply_ms = (perf_counter() - reply_started) * 1000
            logger.info(
                "replied to feishu bot=%s message_id=%s reply_ms=%.1f",
                bot.name,
                message.message_id,
                reply_ms,
            )
        except httpx.HTTPError as error:
            status = "reply_failed"
            error_text = str(error)
            logger.warning(
                "failed to reply to feishu bot=%s message_id=%s error=%s",
                bot.name,
                message.message_id,
                error,
            )
        finally:
            total_ms = (perf_counter() - total_started) * 1000
            logger.info(
                "feishu bot=%s message_id=%s completed total_ms=%.1f n8n_ms=%.1f token_ms=%.1f reply_ms=%.1f",
                bot.name,
                message.message_id,
                total_ms,
                n8n_ms,
                token_ms,
                reply_ms,
            )
            write_run_log(
                bot=bot,
                message=message,
                status=status,
                total_ms=total_ms,
                n8n_ms=n8n_ms,
                token_ms=token_ms,
                reply_ms=reply_ms,
                has_reply=True,
                tool_calls=tool_calls,
                error=error_text,
                workflow=workflow_override,
            )

    def handle_feishu_event(bot: BotConfig, payload: dict[str, Any]) -> None:
        message = normalize_feishu_event(payload)
        if message.chat_type == "group":
            mention_suffixes = [
                value[-8:] if len(value) > 8 else value
                for value in message.mention_open_ids
            ]
            bot_open_id_suffix = bot.bot_open_id[-8:] if len(bot.bot_open_id) > 8 else bot.bot_open_id
            if not message.mention_open_ids:
                logger.info(
                    "skipping unmentioned group feishu event bot=%s message_id=%s chat_id=%s",
                    bot.name,
                    message.message_id,
                    message.chat_id,
                )
                return
            if not bot.bot_open_id:
                logger.warning(
                    "skipping mentioned group feishu event bot=%s message_id=%s because bot_open_id is not configured",
                    bot.name,
                    message.message_id,
                )
                return
            if bot.bot_open_id not in message.mention_open_ids:
                logger.info(
                    "skipping group feishu event bot=%s message_id=%s because bot was not mentioned bot_open_id_suffix=%s mention_open_id_suffixes=%s",
                    bot.name,
                    message.message_id,
                    bot_open_id_suffix,
                    mention_suffixes,
                )
                return

        message_key = f"{bot.name}:{message.message_id}"
        with processed_message_ids_lock:
            if message_key in processed_message_ids:
                logger.info("skipping duplicate feishu bot=%s message_id=%s", bot.name, message.message_id)
                return
            processed_message_ids.add(message_key)

        logger.info(
            "received feishu event bot=%s message_id=%s chat_id=%s type=%s",
            bot.name,
            message.message_id,
            message.chat_id,
            message.message_type,
        )
        process_message(bot, message)

    @app.on_event("startup")
    def startup_long_connection() -> None:
        nonlocal listener_count
        if event_mode != "long_connection":
            app.state.feishu_listener_count = 0
            return
        clients = []
        for bot in bot_configs:
            if not bot.app_id or not bot.app_secret:
                logger.warning(
                    "FEISHU_EVENT_MODE=long_connection but app credentials are missing for bot=%s",
                    bot.name,
                )
                continue
            clients.append(
                start_listener(
                    app_id=bot.app_id,
                    app_secret=bot.app_secret,
                    on_event=lambda payload, current_bot=bot: handle_feishu_event(current_bot, payload),
                )
            )
            logger.info("started feishu long connection listener bot=%s", bot.name)
        app.state.feishu_long_connection_clients = clients
        listener_count = len(clients)
        app.state.feishu_listener_count = listener_count

    @app.post("/feishu/events")
    def receive_event(payload: dict[str, Any], background_tasks: BackgroundTasks) -> dict[str, Any]:
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not challenge:
                raise HTTPException(status_code=400, detail="missing challenge")
            return {"challenge": challenge}

        bot = bot_configs[0]
        message = normalize_feishu_event(payload)
        message_key = f"{bot.name}:{message.message_id}"
        with processed_message_ids_lock:
            if message_key in processed_message_ids:
                logger.info("skipping duplicate feishu bot=%s message_id=%s", bot.name, message.message_id)
            else:
                processed_message_ids.add(message_key)
                logger.info(
                    "received feishu event bot=%s message_id=%s chat_id=%s type=%s",
                    bot.name,
                    message.message_id,
                    message.chat_id,
                    message.message_type,
                )
                background_tasks.add_task(process_message, bot, message)
        return {
            "ok": True,
            "platform": "feishu",
            "message_id": message.message_id,
            "accepted": True,
        }

    return app


app = create_app()
