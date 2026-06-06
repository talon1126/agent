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
llm_module = importlib.import_module("src.libs.llm")
embedding_module = importlib.import_module("src.libs.embedding")
transform_module = importlib.import_module("src.libs.transform")

ConfigurationError = errors_module.ConfigurationError
Document = types_module.Document
Chunk = types_module.Chunk
ChatMessage = llm_module.ChatMessage
EmbeddingFactory = embedding_module.EmbeddingFactory
FakeLoader = loader_module.FakeLoader
FakeEmbedding = embedding_module.FakeEmbedding
FakeLLM = llm_module.FakeLLM
FakeSplitter = splitter_module.FakeSplitter
FakeTransform = transform_module.FakeTransform
LLMFactory = llm_module.LLMFactory
LoaderFactory = loader_module.LoaderFactory
MarkdownLoader = loader_module.MarkdownLoader
PdfLoader = loader_module.PdfLoader
RecursiveCharacterSplitter = splitter_module.RecursiveCharacterSplitter
SplitterFactory = splitter_module.SplitterFactory
TransformFactory = transform_module.TransformFactory
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


def test_factories_register_builtin_providers_through_explicit_method() -> None:
    """Require factories to inject built-ins through one explicit method.

    Factory registries should not be maintained as pre-filled class-variable
    maps. Each factory owns a ``register_builtin_providers()`` method and callers
    can rely on ``list_providers()`` to ensure built-ins are available.
    """

    LoaderFactory.register_builtin_providers()
    SplitterFactory.register_builtin_providers()
    LLMFactory.register_builtin_providers()
    EmbeddingFactory.register_builtin_providers()
    TransformFactory.register_builtin_providers()

    assert {"fake", "markdown", "pdf"}.issubset(LoaderFactory.list_providers())
    assert {"fake", "recursive_character"}.issubset(SplitterFactory.list_providers())
    assert "fake" in LLMFactory.list_providers()
    assert "fake" in EmbeddingFactory.list_providers()
    assert "fake" in TransformFactory.list_providers()


def test_llm_factory_creates_fake_llm_with_unified_chat_interface() -> None:
    """Require LLMFactory to return a chat client with a stable message contract.

    B9 only implements a fake provider, but the caller-facing shape must already
    match future OpenAI, Azure, Ollama, and DeepSeek adapters: business code
    supplies normalized messages and receives a provider-independent response.
    """

    llm = LLMFactory.create(
        provider="fake",
        response_text="Use soft silicone stress balls for quiet decompression.",
    )
    response = llm.chat(
        [
            ChatMessage(role="system", content="Answer in Chinese."),
            ChatMessage(role="user", content="Any relaxing toy recommendation?"),
        ]
    )

    assert isinstance(llm, FakeLLM)
    assert response.content == "Use soft silicone stress balls for quiet decompression."
    assert response.model == "fake-llm"
    assert response.provider == "fake"
    assert response.raw["message_count"] == 2


def test_embedding_factory_creates_fake_embedding_with_batch_interface() -> None:
    """Require EmbeddingFactory to expose consistent single and batch methods.

    Ingestion and query code should not care whether vectors come from OpenAI or
    a deterministic fake. ``embed_batch()`` must preserve input order and match
    repeated ``embed()`` calls for the same text.
    """

    embedding = EmbeddingFactory.create(provider="fake", dimensions=6)
    texts = ["wireless headphones", "quiet stress toy"]

    single_vector = embedding.embed(texts[0])
    batch_vectors = embedding.embed_batch(texts)

    assert isinstance(embedding, FakeEmbedding)
    assert len(single_vector) == 6
    assert batch_vectors == [embedding.embed(text) for text in texts]
    assert batch_vectors[0] == single_vector
    assert batch_vectors[0] != batch_vectors[1]


def test_model_factories_read_settings_default_and_fail_fast_when_unimplemented() -> None:
    """Require factories to read configured providers before reporting gaps.

    The checked-in settings select real providers whose adapters arrive in later
    tasks. B9 factories should still read those selectors from settings and
    raise structured configuration errors instead of silently falling back.
    """

    settings = load_settings(validate_environment=False)

    with pytest.raises(ConfigurationError) as llm_error:
        LLMFactory.create(settings=settings)
    with pytest.raises(ConfigurationError) as embedding_error:
        EmbeddingFactory.create(settings=settings)

    assert llm_error.value.context["provider"] == settings.llm.default
    assert embedding_error.value.context["provider"] == settings.embedding.default
    assert "fake" in llm_error.value.context["available"]
    assert "fake" in embedding_error.value.context["available"]


def test_transform_factory_creates_fake_transform_with_unified_interface() -> None:
    """Require TransformFactory to build a transform with a stable chunk contract.

    Transform stages should accept business ``Chunk`` objects and return a new
    list of ``Chunk`` objects. The fake implementation proves factory wiring and
    metadata mutation behavior without calling LLMs or image-caption providers.
    """

    transform = TransformFactory.create(
        provider="fake",
        metadata_updates={"transform_status": "ok"},
    )
    chunk = Chunk(
        id="chunk-1",
        text="Original chunk text.",
        metadata={"source_path": "shopping_guides/example.md"},
        chunk_index=0,
        start_offset=0,
        end_offset=20,
        source_ref={"document_id": "doc-1"},
    )

    transformed = transform.transform([chunk], context={"trace_id": "trace-1"})

    assert isinstance(transform, FakeTransform)
    assert transformed != [chunk]
    assert transformed[0].metadata["source_path"] == "shopping_guides/example.md"
    assert transformed[0].metadata["transform_status"] == "ok"
    assert "transform_status" not in chunk.metadata


def test_transform_factory_raises_configuration_error_for_unknown_provider() -> None:
    """Require TransformFactory to fail fast for misspelled provider names."""

    with pytest.raises(ConfigurationError) as transform_error:
        TransformFactory.create(provider="missing")

    assert transform_error.value.context["provider"] == "missing"
    assert "fake" in transform_error.value.context["available"]
