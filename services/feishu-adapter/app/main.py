import os
import logging
import threading
import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException

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
