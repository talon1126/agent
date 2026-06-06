"""Protect the pluggable component package boundaries for the RAG subsystem.

The B7 task does not implement concrete factories yet. It establishes the
package layout that later tasks fill with abstract interfaces, registry-backed
factories, and provider implementations. These tests make that boundary
explicit so future development can import component packages consistently.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[2]

# Repository-level pytest runs the independently installable RAG module directly
# from source. Adding this root mirrors editable-install imports while keeping
# the tests independent from the outer ai-service package layout.
sys.path.insert(0, str(RAG_ROOT))

COMPONENT_PACKAGES = (
    "loader",
    "llm",
    "splitter",
    "transform",
    "embedding",
    "vector_store",
    "reranker",
    "evaluator",
)


def test_libs_exports_stable_component_package_names() -> None:
    """Require ``src.libs`` to expose every pluggable component namespace.

    Later orchestration code and Dashboard component discovery should be able to
    rely on these names without scanning the filesystem or hardcoding a second
    copy of the package list.
    """

    libs = importlib.import_module("src.libs")

    assert tuple(libs.__all__) == COMPONENT_PACKAGES


def test_pluggable_component_packages_are_importable() -> None:
    """Require each component package namespace to be importable.

    B8-B11 will add base interfaces, factories, and concrete implementations
    under these packages. This test fails early if a package is renamed,
    removed, or never created.
    """

    for package_name in COMPONENT_PACKAGES:
        module = importlib.import_module(f"src.libs.{package_name}")

        assert module.__name__ == f"src.libs.{package_name}"
