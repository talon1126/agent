"""Expose concrete URL-discovery implementations outside the generic core."""

from data_ops.discovery.jd_product_urls import (
    DiscoveryResult,
    JdProductDiscoveryError,
    canonicalize_jd_product_url,
    discover_jd_product_urls,
)

__all__ = [
    "DiscoveryResult",
    "JdProductDiscoveryError",
    "canonicalize_jd_product_url",
    "discover_jd_product_urls",
]
