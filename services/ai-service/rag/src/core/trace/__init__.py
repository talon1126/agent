"""Expose core trace contracts for ingestion, query, MCP, and Dashboard code.

The trace package is intentionally provider- and storage-independent at F1. It
builds JSON-compatible snapshots in memory and lets callers inject the sink
that will later write JSON Lines, PostgreSQL trace rows, or Dashboard fixtures.
"""

from src.core.trace.trace_context import TraceContext
from src.core.trace.trace_controller import TraceController

__all__ = [
    "TraceContext",
    "TraceController",
]
