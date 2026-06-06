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

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]

# Repository-level pytest runs the independently installable RAG module directly
# from source. Adding this root mirrors editable-install imports while keeping
# the tests independent from the outer ai-service package layout.
sys.path.insert(0, str(RAG_ROOT))

config_module = importlib.import_module("src.core.config")
errors_module = importlib.import_module("src.core.errors")
types_module = importlib.import_module("src.core.types")
loader_module = importlib.import_module("src.libs.loader")
splitter_module = importlib.import_module("src.libs.splitter")

ConfigurationError = errors_module.ConfigurationError
Document = types_module.Document
FakeLoader = loader_module.FakeLoader
FakeSplitter = splitter_module.FakeSplitter
LoaderFactory = loader_module.LoaderFactory
MarkdownLoader = loader_module.MarkdownLoader
PdfLoader = loader_module.PdfLoader
RecursiveCharacterSplitter = splitter_module.RecursiveCharacterSplitter
SplitterFactory = splitter_module.SplitterFactory
load_settings = config_module.load_settings

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


def test_loader_factory_creates_fake_markdown_and_pdf_loaders(tmp_path: Path) -> None:
    """Require LoaderFactory to build initial loader implementations by registry.

    B8 must avoid provider-selection branches in business code. The factory is
    responsible for resolving provider names to concrete classes and returning
    objects that all satisfy the same ``BaseLoader.load()`` contract.
    """

    markdown_path = tmp_path / "guide.md"
    markdown_path.write_text("# Noise Control\n\nChoose soft silicone toys.", encoding="utf-8")

    fake_loader = LoaderFactory.create(
        "fake",
        document=Document(
            id="fake-doc",
            text="Fake loader content.",
            metadata={"source_path": "memory://fake-doc.md"},
        ),
    )
    markdown_loader = LoaderFactory.create("markdown")
    pdf_loader = LoaderFactory.create("pdf")

    assert isinstance(fake_loader, FakeLoader)
    assert fake_loader.load("ignored").id == "fake-doc"
    assert isinstance(markdown_loader, MarkdownLoader)
    assert markdown_loader.load(markdown_path).metadata["source_type"] == "markdown"
    assert isinstance(pdf_loader, PdfLoader)


def test_loader_factory_selects_loader_from_source_suffix(tmp_path: Path) -> None:
    """Require source suffix selection to stay centralized in LoaderFactory.

    Ingestion code should pass the source path and receive the right loader
    without duplicating suffix-to-loader mapping logic in pipeline stages.
    """

    markdown_path = tmp_path / "selection.md"
    markdown_path.write_text("Markdown content for loader selection.", encoding="utf-8")

    loader = LoaderFactory.for_source(markdown_path)

    assert isinstance(loader, MarkdownLoader)
    assert loader.load(markdown_path).metadata["source_path"].endswith("selection.md")


def test_splitter_factory_creates_fake_and_configured_recursive_splitters() -> None:
    """Require SplitterFactory to support test and configured text splitters.

    The fake splitter makes unit tests deterministic, while the configured
    recursive splitter proves the factory can read ``settings.yaml`` without
    hardcoding chunk parameters in orchestration code.
    """

    settings = load_settings(validate_environment=False)

    fake_splitter = SplitterFactory.create(provider="fake", chunks=["first", "second"])
    recursive_splitter = SplitterFactory.create(settings=settings)

    assert isinstance(fake_splitter, FakeSplitter)
    assert fake_splitter.split("ignored") == ["first", "second"]
    assert isinstance(recursive_splitter, RecursiveCharacterSplitter)
    assert all(isinstance(part, str) for part in recursive_splitter.split("alpha beta gamma"))


def test_factories_raise_configuration_error_for_unknown_providers() -> None:
    """Require clear configuration failures when provider names are unknown.

    A misspelled provider should fail at factory creation time with structured
    context, not later as an import error or attribute error inside a pipeline.
    """

    with pytest.raises(ConfigurationError) as loader_error:
        LoaderFactory.create("missing")
    with pytest.raises(ConfigurationError) as splitter_error:
        SplitterFactory.create(provider="missing")

    assert loader_error.value.context["provider"] == "missing"
    assert splitter_error.value.context["provider"] == "missing"
