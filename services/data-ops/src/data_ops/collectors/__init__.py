"""Expose file-based detail collectors used by data-ops pipelines."""

from data_ops.collectors.base import (
    CaptureRequest,
    CaptureResult,
    CollectorMode,
    ProductDetailCollector,
)

__all__ = [
    "CaptureRequest",
    "CaptureResult",
    "CollectorMode",
    "ProductDetailCollector",
]
