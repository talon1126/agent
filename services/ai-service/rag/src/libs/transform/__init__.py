"""Expose the shared transform abstraction used by ingestion steps.

Concrete transform implementations are owned by ``src.ingestion.transform``
because transform order, prompt injection, and trace context are ingestion
pipeline concerns. This package intentionally contains only the stable
``BaseTransform`` contract for code that wants to type-check transform-like
objects without depending on specific ingestion implementations.
"""

from src.libs.transform.base_transform import BaseTransform

__all__ = ("BaseTransform",)
