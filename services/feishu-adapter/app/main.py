import os
import logging
import threading
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, field_validator, model_validator

from app.feishu_client import FEISHU_API_BASE_URL, get_tenant_access_token, reply_text_message
from app.feishu_events import normalize_feishu_event, to_n8n_payload
from app.feishu_long_connection import start_long_connection_listener
from app.intent_router import route_warehouse_intent
from app.view_template_builder import (
    load_warehouse_view_templates,
    match_warehouse_view_template,
    render_warehouse_view_plan,
)

DEFAULT_N8N_WEBHOOK_URL = "http://n8n:5678/webhook/chat-agent-inbound"
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


class InventoryTableProvisionRequest(BaseModel):
    table_name: str = "Warehouse Inventory Snapshot"


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
    table_id = inventory_table_id if inventory_table_id is not None else os.getenv("FEISHU_INVENTORY_TABLE_ID", "")
    table_view_id = (
        inventory_table_view_id
        if inventory_table_view_id is not None
        else os.getenv("FEISHU_INVENTORY_TABLE_VIEW_ID", "")
    )
    table_url = inventory_table_url if inventory_table_url is not None else os.getenv("FEISHU_INVENTORY_TABLE_URL", "")
    inventory_table_state = {
        "table_id": table_id,
        "view_id": table_view_id,
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
                    "workflow": bot.n8n_webhook_url,
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

    def inventory_table_url_for(table_identifier: str) -> str:
        if not table_url:
            return ""
        return table_url.replace("{table_id}", table_identifier)

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

    def bitable_records_url(record_id: str | None = None) -> str:
        base = (
            f"{api_base_url}/open-apis/bitable/v1/apps/{table_app_token}"
            f"/tables/{inventory_table_state['table_id']}/records"
        )
        return f"{base}/{record_id}" if record_id else base

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
        }.get(type_value, f"type_{type_value}")

    def inventory_field_options(field: dict[str, Any]) -> list[dict[str, Any]]:
        property_payload = field.get("property") or {}
        options = property_payload.get("options", [])
        if not isinstance(options, list):
            return []
        return [
            {
                "name": str(option.get("name") or ""),
                "color": option.get("color"),
            }
            for option in options
            if isinstance(option, dict) and option.get("name")
        ]

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

    def create_inventory_table_fields(
        *,
        token: str,
        table_identifier: str,
        field_specs: list[dict[str, Any]],
        existing_fields: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        existing = existing_fields or {}
        for field in field_specs:
            existing_field = existing.get(str(field["field_name"]))
            if existing_field:
                field_id = str(existing_field.get("field_id") or "")
                if field_id and field_signature(field) != field_signature(existing_field):
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
        for item in list_inventory_tables(token=token):
            if str(item.get("name") or item.get("table_name") or "") == table_name:
                return {
                    "table_id": str(item.get("table_id") or ""),
                    "view_id": str(item.get("default_view_id") or item.get("view_id") or ""),
                }
        return {"table_id": "", "view_id": ""}

    def resolve_inventory_table_for_schema(*, token: str, table_name: str) -> dict[str, str]:
        if inventory_table_state["table_id"]:
            return {
                "table_id": inventory_table_state["table_id"],
                "view_id": inventory_table_state["view_id"],
                "action": "existing",
            }
        existing = find_inventory_table_by_name(token=token, table_name=table_name)
        if existing["table_id"]:
            remember_inventory_table(existing)
            return {**existing, "action": "existing"}
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

    def remember_inventory_table(result: dict[str, str]) -> None:
        if result.get("table_id"):
            inventory_table_state["table_id"] = result["table_id"]
        if result.get("view_id"):
            inventory_table_state["view_id"] = result["view_id"]

    def create_or_reuse_inventory_table(
        *,
        token: str,
        table_name: str,
        field_specs: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        field_specs = field_specs or inventory_table_field_specs()
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
        field_specs = field_specs or inventory_table_field_specs()
        if inventory_table_state["table_id"]:
            result = {
                "table_id": inventory_table_state["table_id"],
                "view_id": inventory_table_state["view_id"],
                "action": "existing",
            }
            ensure_inventory_table_fields(
                token=token,
                table_identifier=result["table_id"],
                field_specs=field_specs,
            )
            return result
        existing = find_inventory_table_by_name(token=token, table_name=table_name)
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

    def inventory_record_identity(fields: dict[str, Any]) -> dict[str, str]:
        batch_identity = {
            "Warehouse ID": str(fields.get("Warehouse ID") or "").strip(),
            "Location": str(fields.get("Location") or "").strip(),
            "Item ID": str(fields.get("Item ID") or "").strip(),
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
            "Warehouse ID, Location, Item ID, Batch No"
        )

    def bitable_filter_literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def find_inventory_table_record(*, token: str, identity: dict[str, str]) -> str:
        conditions = [
            f'CurrentValue.[{field_name}]="{bitable_filter_literal(value)}"'
            for field_name, value in identity.items()
        ]
        params: dict[str, Any] = {
            "page_size": 20,
            "filter": f"AND({','.join(conditions)})",
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
        if not items:
            return ""
        return str(items[0].get("record_id") or items[0].get("id") or "")

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

    def normalize_view_filter_value(value: Any) -> str:
        if isinstance(value, list):
            values = value
        elif value is None:
            values = []
        else:
            values = [value]
        return json.dumps(values, ensure_ascii=False)

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
                    "value": normalize_view_filter_value(filter_rule["value"]),
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
            result = upsert_inventory_table_record(token=token, fields=fields)
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
            inventory_rows = fetch_inventory_table_rows_with_fallback(
                item_id=item_id or None,
                sku=sku or None,
                warehouse_id=warehouse_id or None,
                location_code=location_code or None,
                category=category or None,
                category_id=category_id or None,
                batch_no=batch_no or None,
                expiry_risk=expiry_risk or None,
                risk_level=risk_level or None,
                limit=limit,
            )
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
            synced_items: list[dict[str, Any]] = []
            for row in inventory_rows:
                fields = row["fields"]
                result = upsert_inventory_table_record(token=token, fields=fields)
                synced_items.append(
                    {
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
                )
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
                        "output": {"synced_count": len(synced_items)},
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

    def process_message(bot: BotConfig, message: Any) -> None:
        total_started = perf_counter()
        n8n_ms = 0.0
        token_ms = 0.0
        reply_ms = 0.0
        tool_calls: list[Any] = []
        try:
            n8n_started = perf_counter()
            n8n_response = client.post(bot.n8n_webhook_url, json=to_n8n_payload(message))
            n8n_ms = (perf_counter() - n8n_started) * 1000
            n8n_response.raise_for_status()
            n8n_payload = n8n_response.json()
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
            )
            return

        reply = n8n_payload.get("reply") or n8n_payload.get("answer")
        logger.info(
            "forwarded feishu bot=%s message_id=%s to n8n status=%s has_reply=%s n8n_ms=%.1f",
            bot.name,
            message.message_id,
            n8n_response.status_code,
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
            )

    def handle_feishu_event(bot: BotConfig, payload: dict[str, Any]) -> None:
        message = normalize_feishu_event(payload)
        if message.chat_type == "group":
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
                    "skipping group feishu event bot=%s message_id=%s because bot was not mentioned",
                    bot.name,
                    message.message_id,
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
