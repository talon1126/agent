"""Standalone runtime entry point for the AImodel RAG subsystem."""

from __future__ import annotations

import json


def health_status() -> dict[str, str]:
    """Return the minimal service status used by local and container checks."""
    return {"status": "ok", "service": "aimodel-rag"}


def main() -> int:
    """Print the current service status and exit successfully."""
    print(json.dumps(health_status()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
