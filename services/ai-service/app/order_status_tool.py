import httpx


def get_order_status(
    *,
    order_id: str,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
) -> dict:
    owns_client = http_client is None
    client = http_client or httpx.Client(base_url=mock_api_url, timeout=5)
    try:
        order_response = client.get(f"/orders/{order_id}")
        order_response.raise_for_status()
        order = order_response.json()

        shipment = None
        shipment_id = order.get("shipment_id")
        if shipment_id:
            shipment_response = client.get(f"/shipments/{shipment_id}")
            shipment_response.raise_for_status()
            shipment = shipment_response.json()

        shipment_status = shipment.get("status") if shipment else "unknown"
        estimated_delivery = shipment.get("estimated_delivery") if shipment else None
        summary = (
            f"Order {order_id} is {order.get('status', 'unknown')}. "
            f"Shipment status is {shipment_status}."
        )
        if estimated_delivery:
            summary += f" Estimated delivery is {estimated_delivery}."

        return {
            "order_id": order_id,
            "order_status": order.get("status", "unknown"),
            "shipment_id": shipment_id,
            "shipment_status": shipment_status,
            "estimated_delivery": estimated_delivery,
            "summary": summary,
        }
    finally:
        if owns_client:
            client.close()
