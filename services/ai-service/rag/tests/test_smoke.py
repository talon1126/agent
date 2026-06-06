"""Protect the minimum runtime and packaging contract of the RAG module.

These tests run before feature-specific suites and verify that a fresh checkout
can import the standalone entry point, discover every top-level architecture
package, and build a container context without local runtime data. Failures
usually indicate a broken package layout, an invalid runtime entry point, or a
Docker skeleton that no longer matches the independent deployment contract.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(__file__).resolve().parents[4]


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


def test_uv_project_contract() -> None:
    """Verify uv owns dependency locking and project environment creation.

    A fresh checkout must carry a committed lock file and enough project
    metadata for ``uv sync --extra dev --frozen`` to reproduce the same Python
    3.12 environment. A failure means local, CI, and Docker dependency graphs
    can drift independently.
    """

    pyproject = tomllib.loads((RAG_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lockfile = (RAG_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert pyproject["project"]["requires-python"] == ">=3.12"
    assert "dev" in pyproject["project"]["optional-dependencies"]
    assert pyproject["tool"]["uv"]["package"] is True
    assert pyproject["tool"]["uv"]["link-mode"] == "copy"
    assert 'name = "aimodel-modular-rag"' in lockfile


def test_docker_skeleton_uses_uv() -> None:
    """Verify Docker installs the frozen production graph through uv.

    The assertions protect the approved Python 3.12 baseline, pinned uv binary,
    committed lockfile, production-only frozen sync, and direct ``main.py``
    startup contract. A failure means container dependencies may differ from
    the reviewed local lockfile.
    """
    dockerfile = (RAG_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.11.13" in dockerfile
    assert "COPY pyproject.toml uv.lock README.md ./" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "pip install" not in dockerfile
    assert 'CMD ["python", "main.py"]' in dockerfile


def test_documented_commands_and_auto_coder_use_uv() -> None:
    """Verify developer and autonomous workflows execute inside the uv project.

    README commands are the human onboarding contract, while auto-coder
    commands govern autonomous task execution. Both must use the same project
    environment and neither may depend on shell-specific virtualenv activation.
    """

    readme = (RAG_ROOT / "README.md").read_text(encoding="utf-8")
    auto_coder = (
        WORKSPACE_ROOT / ".codex" / "skills" / "auto-coder" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "uv sync --extra dev --frozen" in readme
    assert "uv run pytest" in readme
    assert "uv run ruff check" in readme
    assert "uv run python main.py" in readme
    assert "Activate.ps1" not in auto_coder
    assert "uv run --project services/ai-service/rag python" in auto_coder
    assert "uv run --project services/ai-service/rag pytest" in auto_coder


def test_docker_context_excludes_runtime_data() -> None:
    """Verify that mutable or machine-local assets cannot enter Docker builds.

    The required ignore entries prevent credentials, virtual environments,
    generated caches, local logs, and database/index state from increasing the
    image size or leaking host-specific data into a deployment artifact.
    """
    ignored_entries = set((RAG_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {".venv", "__pycache__", "src/cache", "src/logs", "data/db"} <= ignored_entries
