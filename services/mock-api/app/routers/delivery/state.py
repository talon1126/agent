DELIVERY_CASES: list[dict] = []

DELIVERY_PROVIDERS: list[dict] = [
    {
        "provider_id": "sf",
        "name": "顺丰",
        "service_hotline": "95338",
        "tracking_prefix": "SF",
        "status": "active",
    },
    {
        "provider_id": "jd",
        "name": "京东",
        "service_hotline": "950616",
        "tracking_prefix": "JD",
        "status": "active",
    },
    {
        "provider_id": "yto",
        "name": "圆通",
        "service_hotline": "95554",
        "tracking_prefix": "YT",
        "status": "active",
    },
]


def get_delivery_provider(provider_id: str | None) -> dict:
    provider_key = (provider_id or "sf").strip().casefold()
    provider = next(
        (item for item in DELIVERY_PROVIDERS if item["provider_id"] == provider_key),
        None,
    )
    return provider or DELIVERY_PROVIDERS[0]
