"""Expose the generic processor registry without importing site processors."""

from data_ops.processors.registry import get_processor, register_processor

__all__ = ["get_processor", "register_processor"]
