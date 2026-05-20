import asyncio
import json
import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("feishu_adapter")


def start_long_connection_listener(
    *,
    app_id: str,
    app_secret: str,
    on_event: Callable[[dict[str, Any]], None],
) -> object:
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    except ImportError as error:
        raise RuntimeError("lark-oapi is required for FEISHU_EVENT_MODE=long_connection") from error

    def handle_message(data: P2ImMessageReceiveV1) -> None:
        payload = json.loads(lark.JSON.marshal(data))
        logger.info("received feishu long connection event")
        on_event(payload)

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(handle_message)
        .build()
    )
    ws_client = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
        auto_reconnect=True,
    )

    def run_client() -> None:
        import lark_oapi.ws.client as ws_client_module

        ws_client_module.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(ws_client_module.loop)
        ws_client.start()

    thread = threading.Thread(
        target=run_client,
        name="feishu-long-connection",
        daemon=True,
    )
    thread.start()
    logger.info("started feishu long connection listener")
    return ws_client
