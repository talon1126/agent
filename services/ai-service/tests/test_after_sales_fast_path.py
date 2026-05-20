import httpx

from app.after_sales_fast_path import handle_after_sales_fast_path
from app.message_schemas import AfterSalesFastPathRequest


def test_fast_path_records_order_and_uses_it_for_follow_up_refund() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/orders/ord_100":
            return httpx.Response(
                200,
                json={
                    "order_id": "ord_100",
                    "status": "delivered",
                    "shipment_id": "ship_100",
                },
            )
        if request.url.path == "/shipments/ship_100":
            return httpx.Response(
                200,
                json={
                    "shipment_id": "ship_100",
                    "status": "delivered",
                    "estimated_delivery": "2026-05-18",
                },
            )
        if request.url.path == "/policies/search":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "source": "fixtures/policies/after_sales_policy.zh.md",
                    "matches": [
                        {
                            "source_file": "fixtures/policies/after_sales_policy.zh.md",
                            "section": "退款标准",
                            "clause_id": "REFUND-001",
                            "clause_title": "已送达订单退款",
                            "text": "已送达订单需要满足退款标准。",
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock-api")
    state_store: dict[str, dict[str, str]] = {}

    first = handle_after_sales_fast_path(
        AfterSalesFastPathRequest(
            message_id="msg_1",
            session_id="feishu:chat:user",
            text="帮我查一下订单 ord_100",
        ),
        mock_api_url="http://mock-api",
        http_client=client,
        state_store=state_store,
    )
    second = handle_after_sales_fast_path(
        AfterSalesFastPathRequest(
            message_id="msg_2",
            session_id="feishu:chat:user",
            text="这个订单怎么退款",
        ),
        mock_api_url="http://mock-api",
        http_client=client,
        state_store=state_store,
    )

    assert first.handled is True
    assert first.order_id == "ord_100"
    assert second.handled is True
    assert second.order_id == "ord_100"
    assert "我按上一单 ord_100 处理" in second.answer
    assert "REFUND-001" in second.answer
    assert "fixtures/policies/after_sales_policy.zh.md" in second.answer
    assert [request.url.path for request in requests] == [
        "/orders/ord_100",
        "/shipments/ship_100",
        "/orders/ord_100",
        "/shipments/ship_100",
        "/policies/search",
    ]


def test_fast_path_declines_unknown_messages() -> None:
    response = handle_after_sales_fast_path(
        AfterSalesFastPathRequest(
            message_id="msg_unknown",
            session_id="feishu:chat:user",
            text="你好",
        ),
        mock_api_url="http://mock-api",
        state_store={},
    )

    assert response.handled is False
    assert response.reason == "not_fast_path"
