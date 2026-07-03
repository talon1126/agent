"""Provide the minimal standalone process entry point for the RAG subsystem.

This module is the first executable boundary of the independently deployable
RAG service. It deliberately exposes only a deterministic health payload while
the ingestion, retrieval, MCP, and dashboard runtimes are implemented in later
tasks. Docker and local smoke checks import this module to verify that the
package graph can be loaded without starting external services.

The module does not create database connections, load model providers, or
perform configuration validation. Those responsibilities belong to the
dedicated core and infrastructure packages.
"""

from __future__ import annotations

import json


def health_status() -> dict[str, str]:
    """Build the dependency-free health payload for process smoke checks.

    The payload is intentionally static. At this project stage, a successful
    import and function call proves that the standalone runtime is installed
    correctly; it does not claim that PostgreSQL, model providers, or indexes
    are reachable.

    Returns:
        A JSON-serializable mapping containing the stable service identifier
        and an ``ok`` process status.
    """
    return {"status": "ok", "service": "aimodel-rag"}


def main() -> int:
    """Write the health payload to standard output for CLI and Docker use.

    Returns:
        Process exit code ``0`` after the status payload has been emitted.

    Side Effects:
        Writes one JSON object to standard output. The function performs no
        network, filesystem, database, or model-provider operations.
    """
    print(json.dumps(health_status()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
