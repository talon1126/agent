"""Define the public root package for the modular AImodel RAG subsystem.

The ``src`` package groups the core orchestration, pluggable component
contracts, ingestion pipeline, persistence adapters, observability features,
and MCP integration behind one independently deployable Python module.

Only package metadata is exported here. Importing ``src`` must remain free of
database connections, provider initialization, and other runtime side effects
so tooling and smoke tests can inspect the package safely.
"""

__version__ = "0.1.0"
