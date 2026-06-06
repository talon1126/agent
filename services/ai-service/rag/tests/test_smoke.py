"""Smoke tests for the standalone RAG module skeleton."""

from __future__ import annotations

import importlib
from pathlib import Path


RAG_ROOT = Path(__file__).resolve().parents[1]


def test_main_importable() -> None:
    """The standalone runtime entry point must expose a callable main function."""
    main_module = importlib.import_module("main")

    assert callable(main_module.main)
    assert main_module.health_status() == {"status": "ok", "service": "aimodel-rag"}


def test_rag_packages_importable() -> None:
    """The primary architecture packages must be available before feature work starts."""
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
    """The container skeleton must target Python 3.12 and start the module entry point."""
    dockerfile = (RAG_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim" in dockerfile
    assert "pip install" in dockerfile
    assert 'CMD ["python", "main.py"]' in dockerfile


def test_docker_context_excludes_runtime_data() -> None:
    """Local caches, logs, databases, and virtual environments must stay out of images."""
    ignored_entries = set(
        (RAG_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    )

    assert {".venv", "__pycache__", "src/cache", "src/logs", "data/db"} <= ignored_entries
