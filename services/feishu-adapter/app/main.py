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
from pydantic import BaseModel

from app.feishu_client import FEISHU_API_BASE_URL, get_tenant_access_token, reply_text_message
from app.feishu_events import normalize_feishu_event, to_n8n_payload
from app.feishu_long_connection import start_long_connection_listener

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
    sku: str


class InventoryTableProvisionRequest(BaseModel):
    table_name: str = "Warehouse Inventory Snapshot"


INVENTORY_TABLE_FIELD_SPECS = [
    {"field_name": "SKU", "type": "text"},
    {"field_name": "Product Name", "type": "text"},
    {"field_name": "Warehouse", "type": "text"},
    {"field_name": "Available", "type": "number"},
    {"field_name": "Reserved", "type": "number"},
    {"field_name": "Pending Orders", "type": "number"},
    {"field_name": "Risk Level", "type": "text"},
    {"field_name": "Open Exception Count", "type": "number"},
    {"field_name": "Recommendation", "type": "text"},
    {"field_name": "Last Synced At", "type": "text"},
    {"field_name": "Sync Status", "type": "text"},
    {"field_name": "Source Version", "type": "text"},
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
        return bool(table_app_id and table_app_secret and table_app_token and table_id)

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

    def bitable_records_url(record_id: str | None = None) -> str:
        base = f"{api_base_url}/open-apis/bitable/v1/apps/{table_app_token}/tables/{table_id}/records"
        return f"{base}/{record_id}" if record_id else base

    def bitable_tables_url() -> str:
        return f"{api_base_url}/open-apis/bitable/v1/apps/{table_app_token}/tables"

    def bitable_fields_url(table_identifier: str) -> str:
        return f"{api_base_url}/open-apis/bitable/v1/apps/{table_app_token}/tables/{table_identifier}/fields"

    def create_inventory_table_fields(*, token: str, table_identifier: str) -> None:
        for field in INVENTORY_TABLE_FIELD_SPECS:
            response = client.post(
                bitable_fields_url(table_identifier),
                headers={"Authorization": f"Bearer {token}"},
                json=field,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in {0, None}:
                raise RuntimeError(f"Feishu inventory table field create failed: {payload}")

    def create_inventory_table(*, token: str, table_name: str) -> dict[str, str]:
        response = client.post(
            bitable_tables_url(),
            headers={"Authorization": f"Bearer {token}"},
            json={"table": {"name": table_name}},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in {0, None}:
            raise RuntimeError(f"Feishu inventory table create failed: {payload}")
        data = payload.get("data", {})
        created_table_id = str(data.get("table_id") or "")
        if not created_table_id:
            raise RuntimeError(f"Feishu inventory table create returned no table_id: {payload}")
        create_inventory_table_fields(token=token, table_identifier=created_table_id)
        return {
            "table_id": created_table_id,
            "view_id": str(data.get("default_view_id") or data.get("view_id") or ""),
        }

    def find_inventory_table_record(*, token: str, sku: str, warehouse_id: str) -> str:
        params: dict[str, Any] = {
            "page_size": 20,
            "filter": f'AND(CurrentValue.[SKU]="{sku}",CurrentValue.[Warehouse]="{warehouse_id}")',
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
        record_id = find_inventory_table_record(
            token=token,
            sku=str(fields["SKU"]),
            warehouse_id=str(fields["Warehouse"]),
        )
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
        if table_id:
            return {
                "ok": True,
                "configured": True,
                "action": "existing",
                "table_id": table_id,
                "view_id": table_view_id,
                "table_url": inventory_table_url_for(table_id),
            }
        table_name = request.table_name.strip() or "Warehouse Inventory Snapshot"
        try:
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            result = create_inventory_table(token=token, table_name=table_name)
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
                "action": "created",
                **result,
                "table_url": inventory_table_url_for(result["table_id"]),
                "fields": [field["field_name"] for field in INVENTORY_TABLE_FIELD_SPECS],
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            write_inventory_table_run_log(
                event_id="inventory_table_provision:failed",
                workflow="/warehouse/inventory-table/provision",
                status="failed",
                latency_ms=latency_ms,
                error=str(error),
            )
            return {
                "ok": False,
                "configured": True,
                "error": "feishu_inventory_table_provision_failed",
                "message": str(error),
            }

    @app.post("/warehouse/inventory-table/sync")
    def sync_inventory_table(request: InventoryTableSyncRequest) -> dict[str, Any]:
        started = perf_counter()
        sku = request.sku.strip().lower()
        if not inventory_table_sync_configured():
            return {
                "ok": False,
                "configured": False,
                "error": "missing_feishu_inventory_table_config",
                "message": "Feishu inventory table sync is not configured.",
            }
        try:
            inventory_response = client.get(
                f"{runtime_mock_api_url}/warehouse/inventory/{sku}",
            )
            inventory_response.raise_for_status()
            inventory = inventory_response.json()
            fields = build_inventory_snapshot_fields(inventory)
            token = get_tenant_access_token(
                client=client,
                app_id=table_app_id,
                app_secret=table_app_secret,
                api_base_url=api_base_url,
            )
            result = upsert_inventory_table_record(token=token, fields=fields)
            latency_ms = (perf_counter() - started) * 1000
            write_sync_run_log(
                sku=sku,
                status="succeeded",
                latency_ms=latency_ms,
                tool_calls=[{"tool": "warehouse_inventory_table_sync_tool", "input": {"sku": sku}, "output": result}],
            )
            return {
                "ok": True,
                "configured": True,
                "sku": sku,
                **result,
                "table_url": table_url,
                "last_synced_at": fields["Last Synced At"],
                "source_version": fields["Source Version"],
            }
        except (httpx.HTTPError, RuntimeError) as error:
            latency_ms = (perf_counter() - started) * 1000
            write_sync_run_log(
                sku=sku,
                status="failed",
                latency_ms=latency_ms,
                error=str(error),
            )
            return {
                "ok": False,
                "configured": True,
                "sku": sku,
                "error": "feishu_inventory_table_sync_failed",
                "message": str(error),
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
