"""Protect the minimum runtime and packaging contract of the RAG module.

These tests run before feature-specific suites and verify that a fresh checkout
can import the standalone entry point, discover every top-level architecture
package, and build a container context without local runtime data. Failures
usually indicate a broken package layout, an invalid runtime entry point, or a
Docker skeleton that no longer matches the independent deployment contract.
"""

from __future__ import annotations

import importlib
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[1]


def test_main_importable() -> None:
    """Verify the standalone process API without starting external services.

    The test imports ``main`` exactly as the Docker command will and validates
    both the callable CLI entry point and the stable health payload. A failure
    means the service can no longer satisfy its minimum local/container startup
    contract.
    """
    main_module = importlib.import_module("main")

    assert callable(main_module.main)
    assert main_module.health_status() == {"status": "ok", "service": "aimodel-rag"}


def test_rag_packages_importable() -> None:
    """Verify that every top-level architecture package is importable.

    Importing these packages must not require PostgreSQL, API credentials, or
    provider SDK initialization. A failure identifies either a missing package
    boundary or an import-time side effect introduced by later development.
    """
    package_names = (
        "src.core",
        "src.libs",
        "src.ingestion",
        "src.storage",
        "src.observability",
        "src.mcp_server",
    )

    for package_name in package_names:
        assert importlib.import_module(package_name)


def test_docker_skeleton_uses_python_312() -> None:
    """Verify the Docker runtime version and executable service command.

    The assertions protect the approved Python 3.12 baseline, dependency
    installation step, and direct ``main.py`` startup contract. A failure means
    local Python behavior and the independently deployed container may diverge.
    """
    dockerfile = (RAG_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim" in dockerfile
    assert "pip install" in dockerfile
    assert 'CMD ["python", "main.py"]' in dockerfile


def test_docker_context_excludes_runtime_data() -> None:
    """Verify that mutable or machine-local assets cannot enter Docker builds.

    The required ignore entries prevent credentials, virtual environments,
    generated caches, local logs, and database/index state from increasing the
    image size or leaking host-specific data into a deployment artifact.
    """
    ignored_entries = set((RAG_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {".venv", "__pycache__", "src/cache", "src/logs", "data/db"} <= ignored_entries
