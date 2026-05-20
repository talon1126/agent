import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException

from app.feishu_client import FEISHU_API_BASE_URL, get_tenant_access_token, reply_text_message
from app.feishu_events import normalize_feishu_event, to_n8n_payload

DEFAULT_N8N_WEBHOOK_URL = "http://n8n:5678/webhook/chat-agent-inbound"


def create_app(
    *,
    http_client: httpx.Client | None = None,
    n8n_webhook_url: str | None = None,
    feishu_app_id: str | None = None,
    feishu_app_secret: str | None = None,
    feishu_api_base_url: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Feishu Adapter")
    client = http_client or httpx.Client(timeout=20)
    webhook_url = n8n_webhook_url or os.getenv("N8N_CHAT_WEBHOOK_URL", DEFAULT_N8N_WEBHOOK_URL)
    app_id = feishu_app_id if feishu_app_id is not None else os.getenv("FEISHU_APP_ID", "")
    app_secret = (
        feishu_app_secret if feishu_app_secret is not None else os.getenv("FEISHU_APP_SECRET", "")
    )
    api_base_url = feishu_api_base_url or os.getenv("FEISHU_API_BASE_URL", FEISHU_API_BASE_URL)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/feishu/events")
    def receive_event(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not challenge:
                raise HTTPException(status_code=400, detail="missing challenge")
            return {"challenge": challenge}

        message = normalize_feishu_event(payload)
        n8n_response = client.post(webhook_url, json=to_n8n_payload(message))
        n8n_response.raise_for_status()
        n8n_payload = n8n_response.json()
        reply = n8n_payload.get("reply") or n8n_payload.get("answer")
        replied = False

        if app_id and app_secret and reply:
            tenant_access_token = get_tenant_access_token(
                client=client,
                app_id=app_id,
                app_secret=app_secret,
                api_base_url=api_base_url,
            )
            reply_text_message(
                client=client,
                tenant_access_token=tenant_access_token,
                message_id=message.message_id,
                text=str(reply),
                api_base_url=api_base_url,
            )
            replied = True

        return {
            "ok": True,
            "platform": "feishu",
            "message_id": message.message_id,
            "forwarded": True,
            "replied": replied,
        }

    return app


app = create_app()
