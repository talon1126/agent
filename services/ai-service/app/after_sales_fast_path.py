import re
import threading
import logging
from time import perf_counter

import httpx

from app.message_schemas import (
    AfterSalesFastPathRequest,
    AfterSalesFastPathResponse,
    ToolCall,
)
from app.order_status_tool import get_order_status
from app.session_store import InMemorySessionStore, SessionStateStore

ORDER_ID_PATTERN = re.compile(r"\bord_[0-9A-Za-z]+\b", re.IGNORECASE)
REFUND_KEYWORDS = ("退款", "退货", "换货", "refund", "return", "exchange")
ORDER_KEYWORDS = ("订单", "物流", "发货", "配送", "order", "shipment", "delivery")
ORDER_REFERENCE_KEYWORDS = ("这个订单", "该订单", "刚才那个订单", "上一单", "this order")

logger = logging.getLogger("ai_service.fast_path")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(handler)
_STATE_LOCK = threading.Lock()
_SESSION_STATE = InMemorySessionStore()


def extract_order_id(text: str) -> str | None:
    match = ORDER_ID_PATTERN.search(text)
    return match.group(0).lower() if match else None


def is_after_sales_fast_path(
    text: str,
    order_id: str | None,
    *,
    has_remembered_order: bool = False,
) -> bool:
    lowered = text.lower()
    has_refund_intent = any(keyword in lowered for keyword in REFUND_KEYWORDS)
    has_after_sales_intent = any(keyword in lowered for keyword in REFUND_KEYWORDS + ORDER_KEYWORDS)
    has_order_reference = any(keyword in lowered for keyword in ORDER_REFERENCE_KEYWORDS)
    return bool(
        (order_id and has_after_sales_intent)
        or (has_order_reference and has_after_sales_intent)
        or (has_refund_intent and has_remembered_order)
    )


def _remember_order(
    request: AfterSalesFastPathRequest,
    order_id: str,
    state_store: SessionStateStore | dict[str, dict[str, str]],
) -> None:
    if hasattr(state_store, "remember_order"):
        state_store.remember_order(
            request.session_id,
            order_id,
            source="feishu",
            chat_id=request.chat_id,
            sender_id=request.sender_id,
        )
        return
    with _STATE_LOCK:
        state_store.setdefault(request.session_id, {})["last_order_id"] = order_id


def _last_order_id(
    session_id: str,
    state_store: SessionStateStore | dict[str, dict[str, str]],
) -> str | None:
    if hasattr(state_store, "get_last_order_id"):
        return state_store.get_last_order_id(session_id)
    with _STATE_LOCK:
        return state_store.get(session_id, {}).get("last_order_id")


def _format_policy_sources(matches: list[dict]) -> str:
    if not matches:
        return "未找到对应公司政策，需要人工确认。"
    lines = []
    for match in matches[:3]:
        lines.append(
            "- "
            f"{match.get('clause_id', 'UNKNOWN')} {match.get('clause_title', '')}："
            f"source_file={match.get('source_file', '')}，"
            f"section={match.get('section', '')}"
        )
    return "\n".join(lines)


def handle_after_sales_fast_path(
    request: AfterSalesFastPathRequest,
    *,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
    state_store: SessionStateStore | dict[str, dict[str, str]] | None = None,
) -> AfterSalesFastPathResponse:
    total_started = perf_counter()
    order_ms = 0.0
    policy_ms = 0.0
    text = request.text.strip()
    state = state_store if state_store is not None else _SESSION_STATE
    order_id = request.order_id or extract_order_id(text)
    is_refund = any(keyword in text.lower() for keyword in REFUND_KEYWORDS)
    remembered_order_id = _last_order_id(request.session_id, state)

    if not is_after_sales_fast_path(
        text,
        order_id,
        has_remembered_order=bool(remembered_order_id),
    ):
        logger.info(
            "after_sales_fast_path message_id=%s handled=false reason=not_fast_path total_ms=%.1f",
            request.message_id,
            (perf_counter() - total_started) * 1000,
        )
        return AfterSalesFastPathResponse(
            message_id=request.message_id,
            session_id=request.session_id,
            input_text=text,
            handled=False,
            reason="not_fast_path",
            answer="",
            chat_id=request.chat_id,
            sender_id=request.sender_id,
            confidence=0.0,
        )

    if order_id:
        _remember_order(request, order_id, state)
    else:
        order_id = remembered_order_id

    if not order_id:
        logger.info(
            "after_sales_fast_path message_id=%s handled=false reason=missing_order_context total_ms=%.1f",
            request.message_id,
            (perf_counter() - total_started) * 1000,
        )
        return AfterSalesFastPathResponse(
            message_id=request.message_id,
            session_id=request.session_id,
            input_text=text,
            handled=False,
            reason="missing_order_context",
            answer="",
            chat_id=request.chat_id,
            sender_id=request.sender_id,
            confidence=0.0,
        )

    owns_client = http_client is None
    client = http_client or httpx.Client(base_url=mock_api_url, timeout=5)
    try:
        order_started = perf_counter()
        order_status = get_order_status(
            order_id=order_id,
            mock_api_url=mock_api_url,
            http_client=client,
        )
        order_ms = (perf_counter() - order_started) * 1000
        tool_calls = [
            ToolCall(
                tool_name="get_order_status",
                input={"order_id": order_id},
                output=order_status,
                status="succeeded",
            )
        ]
        prefix = f"我按上一单 {order_id} 处理。\n" if not (request.order_id or extract_order_id(text)) else ""
        answer = (
            f"{prefix}订单 {order_id} 当前状态：{order_status.get('order_status', 'unknown')}；"
            f"物流状态：{order_status.get('shipment_status', 'unknown')}。"
        )

        if is_refund:
            policy_started = perf_counter()
            policy_response = client.post(
                "/policies/search",
                json={"query": text, "locale": "zh", "limit": 5},
            )
            policy_response.raise_for_status()
            policy_result = policy_response.json()
            policy_ms = (perf_counter() - policy_started) * 1000
            matches = policy_result.get("matches", [])
            tool_calls.append(
                ToolCall(
                    tool_name="policy_search",
                    input={"query": text, "locale": "zh", "limit": 5},
                    output=policy_result,
                    status="succeeded",
                )
            )
            answer += "\n退款政策引用：\n" + _format_policy_sources(matches)

        logger.info(
            "after_sales_fast_path message_id=%s handled=true reason=after_sales_fast_path total_ms=%.1f order_ms=%.1f policy_ms=%.1f",
            request.message_id,
            (perf_counter() - total_started) * 1000,
            order_ms,
            policy_ms,
        )
        return AfterSalesFastPathResponse(
            message_id=request.message_id,
            session_id=request.session_id,
            input_text=text,
            handled=True,
            reason="after_sales_fast_path",
            answer=answer,
            order_id=order_id,
            chat_id=request.chat_id,
            sender_id=request.sender_id,
            confidence=0.95,
            tool_calls=tool_calls,
        )
    finally:
        if owns_client:
            client.close()
