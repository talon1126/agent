import json
from typing import Any

from pydantic import BaseModel


class FeishuMessage(BaseModel):
    platform: str
    message_type: str
    sender_id: str
    chat_id: str
    message_id: str
    text: str = ""
    audio_url: str = ""
    media_id: str = ""
    raw_payload: dict[str, Any]


def _parse_content(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"text": value}
    return parsed if isinstance(parsed, dict) else {"text": str(parsed)}


def _first(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def normalize_feishu_event(payload: dict[str, Any]) -> FeishuMessage:
    event = payload.get("event") or {}
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    message = event.get("message") or {}
    content = _parse_content(message.get("content"))
    message_type = _first(message.get("message_type"), "text").lower()

    text = _first(
        content.get("text"),
        content.get("content"),
        message.get("text"),
    )
    media_id = _first(
        content.get("file_key"),
        content.get("media_id"),
        message.get("file_key"),
        message.get("media_id"),
    )

    return FeishuMessage(
        platform="feishu",
        message_type="audio" if message_type in {"audio", "voice"} else "text",
        sender_id=_first(
            sender_id.get("open_id"),
            sender_id.get("user_id"),
            sender_id.get("union_id"),
            "unknown-sender",
        ),
        chat_id=_first(message.get("chat_id"), "unknown-chat"),
        message_id=_first(message.get("message_id"), payload.get("uuid"), "unknown-message"),
        text=text,
        audio_url=_first(content.get("audio_url"), content.get("file_url")),
        media_id=media_id,
        raw_payload=payload,
    )


def to_n8n_payload(message: FeishuMessage) -> dict[str, Any]:
    return {
        "platform": message.platform,
        "message_type": message.message_type,
        "sender_id": message.sender_id,
        "chat_id": message.chat_id,
        "message_id": message.message_id,
        "text": message.text,
        "audio_url": message.audio_url,
        "media_id": message.media_id,
    }
