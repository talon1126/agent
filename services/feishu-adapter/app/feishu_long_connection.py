import asyncio
import importlib.util
import json
import logging
import multiprocessing
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("feishu_adapter")


def _run_long_connection_client(
    *,
    app_id: str,
    app_secret: str,
    on_event: Callable[[dict[str, Any]], None],
) -> None:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    import lark_oapi.ws.client as ws_client_module

    def handle_message(data: P2ImMessageReceiveV1) -> None:
        payload = json.loads(lark.JSON.marshal(data))
        logger.info("received feishu long connection event")
        on_event(payload)

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(handle_message)
        .build()
    )
    client = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
        auto_reconnect=True,
    )
    ws_client_module.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ws_client_module.loop)
    client.start()


def start_long_connection_listener(
    *,
    app_id: str,
    app_secret: str,
    on_event: Callable[[dict[str, Any]], None],
) -> object:
    if importlib.util.find_spec("lark_oapi") is None:
        raise RuntimeError("lark-oapi is required for FEISHU_EVENT_MODE=long_connection")

    if "fork" in multiprocessing.get_all_start_methods():
        process = multiprocessing.get_context("fork").Process(
            target=_run_long_connection_client,
            kwargs={
                "app_id": app_id,
                "app_secret": app_secret,
                "on_event": on_event,
            },
            name="feishu-long-connection",
            daemon=True,
        )
        process.start()
        logger.info("started feishu long connection listener process pid=%s", process.pid)
        return process

    def run_client() -> None:
        _run_long_connection_client(
            app_id=app_id,
            app_secret=app_secret,
            on_event=on_event,
        )

    thread = threading.Thread(
        target=run_client,
        name="feishu-long-connection",
        daemon=True,
    )
    thread.start()
    logger.warning(
        "started feishu long connection listener in thread fallback; multiple listeners require fork support"
    )
    return thread
