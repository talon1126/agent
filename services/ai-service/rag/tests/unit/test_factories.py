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
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = RAG_ROOT / "config" / "settings.example.yaml"

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
vector_store_module = importlib.import_module("src.libs.vector_store")
reranker_module = importlib.import_module("src.libs.reranker")
evaluator_module = importlib.import_module("src.libs.evaluator")

ConfigurationError = errors_module.ConfigurationError
Document = types_module.Document
Chunk = types_module.Chunk
ChatMessage = llm_module.ChatMessage
EmbeddingFactory = embedding_module.EmbeddingFactory
FakeLoader = loader_module.FakeLoader
FakeEmbedding = embedding_module.FakeEmbedding
FakeLLM = llm_module.FakeLLM
FakeSplitter = splitter_module.FakeSplitter
FakeVectorStore = vector_store_module.FakeVectorStore
FakeReranker = reranker_module.FakeReranker
CrossEncoderReranker = reranker_module.CrossEncoderReranker
NoOpReranker = reranker_module.NoOpReranker
FakeEvaluator = evaluator_module.FakeEvaluator
DeepSeekClient = llm_module.DeepSeekClient
OpenAIEmbedding = embedding_module.OpenAIEmbedding
PgVectorStore = vector_store_module.PgVectorStore
LLMFactory = llm_module.LLMFactory
LoaderFactory = loader_module.LoaderFactory
MarkdownLoader = loader_module.MarkdownLoader
PdfLoader = loader_module.PdfLoader
RecursiveCharacterSplitter = splitter_module.RecursiveCharacterSplitter
SplitterFactory = splitter_module.SplitterFactory
VectorStoreFactory = vector_store_module.VectorStoreFactory
RerankerFactory = reranker_module.RerankerFactory
EvaluatorFactory = evaluator_module.EvaluatorFactory
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

    B8-B11 add base interfaces, factories, and concrete implementations under
    these packages. Transform intentionally remains interface-only because
    concrete transform execution belongs to ingestion. This test fails early if
    a package is renamed,
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

    settings = load_settings(SETTINGS_PATH, validate_environment=False)

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
    VectorStoreFactory.register_builtin_providers()
    RerankerFactory.register_builtin_providers()
    EvaluatorFactory.register_builtin_providers()

    assert {"fake", "markdown", "pdf"}.issubset(LoaderFactory.list_providers())
    assert {"fake", "recursive_character"}.issubset(SplitterFactory.list_providers())
    assert {"deepseek", "fake"}.issubset(LLMFactory.list_providers())
    assert {"fake", "openai"}.issubset(EmbeddingFactory.list_providers())
    assert {"fake", "pgvector"}.issubset(VectorStoreFactory.list_providers())
    assert {"fake", "none", "rrf", "fallback"}.issubset(
        RerankerFactory.list_providers()
    )
    assert {"fake", "ragas"}.issubset(EvaluatorFactory.list_providers())


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


def test_embedding_factory_registers_dashscope_openai_compatible_provider() -> None:
    """Require DashScope to reuse the provider-independent embedding adapter."""

    sdk_client = Mock()
    embedding = EmbeddingFactory.create(
        provider="dashscope",
        model="text-embedding-v4",
        dimensions=1536,
        api_key_env="DASHSCOPE_API_KEY",
        base_url_env="DASHSCOPE_BASE_URL",
        client=sdk_client,
    )

    assert isinstance(embedding, OpenAIEmbedding)
    assert "dashscope" in EmbeddingFactory.list_providers()


def test_model_factories_fail_fast_when_required_environment_is_missing() -> None:
    """Require real providers to reject missing credentials before SDK calls."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)

    with pytest.raises(ConfigurationError) as llm_error:
        LLMFactory.create(settings=settings, environ={})
    with pytest.raises(ConfigurationError) as embedding_error:
        EmbeddingFactory.create(settings=settings, environ={})

    assert llm_error.value.context["environment_variable"] == "DASHSCOPE_API_KEY"
    assert embedding_error.value.context["environment_variable"] == "DASHSCOPE_API_KEY"
    assert embedding_error.value.context["provider"] == "dashscope"


def test_vector_store_factory_creates_order_preserving_fake_store() -> None:
    """Protect the minimal vector-store contract needed by ingestion and retrieval.

    The fake store must accept chunk/vector pairs, rank dense candidates
    deterministically, apply exact metadata filters, and preserve caller order
    when sparse retrieval asks for chunks by ID.
    """

    store = VectorStoreFactory.create(provider="fake")
    first = Chunk(
        id="chunk-1",
        text="Quiet silicone stress ball.",
        metadata={"collection": "shopping_guides", "doc_type": "guide"},
        chunk_index=0,
        start_offset=0,
        end_offset=27,
        source_ref={
            "document_id": "doc-stress",
            "source_path": "shopping_guides/stress-balls.md",
        },
    )
    second = Chunk(
        id="chunk-2",
        text="Wireless headphone comparison.",
        metadata={"collection": "shopping_guides", "doc_type": "comparison"},
        chunk_index=1,
        start_offset=28,
        end_offset=58,
    )

    upserted_ids = store.upsert(
        [first, second],
        [[1.0, 0.0], [0.0, 1.0]],
    )
    results = store.search(
        [0.9, 0.1],
        filters={"doc_type": "guide"},
        top_k=5,
    )
    fetched = store.get_by_ids(["chunk-2", "missing", "chunk-1"])

    assert isinstance(store, FakeVectorStore)
    assert upserted_ids == ["chunk-1", "chunk-2"]
    assert [result.chunk_id for result in results] == ["chunk-1"]
    assert results[0].metadata["source_ref"] == first.source_ref
    assert [chunk.id for chunk in fetched] == ["chunk-2", "chunk-1"]


def test_fake_vector_store_rejects_dimension_changes_across_upserts() -> None:
    """Keep the fake aligned with pgvector's fixed-dimension column contract."""

    store = VectorStoreFactory.create(provider="fake")
    chunk = Chunk(
        id="chunk-1",
        text="Stable vector dimensions.",
        metadata={},
        chunk_index=0,
        start_offset=0,
        end_offset=25,
    )
    store.upsert([chunk], [[1.0, 0.0]])

    with pytest.raises(ValueError, match="dimensions"):
        store.upsert([chunk], [[1.0, 0.0, 0.0]])


def test_reranker_factory_uses_configured_rrf_fallback_when_default_is_unavailable() -> None:
    """Require settings-only LLM rerank creation to degrade to stable RRF order.

    Settings select the LLM reranker, but settings do not contain a live
    ``BaseLLM`` dependency. Factory creation should therefore select
    ``settings.rerank.fallback`` and preserve the already fused candidate order
    until orchestration injects an actual chat client.
    """

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    reranker = RerankerFactory.create(settings=settings)
    candidates = [
        types_module.RetrievalResult(
            chunk_id="chunk-2",
            text="Second candidate.",
            score=0.7,
            metadata={},
        ),
        types_module.RetrievalResult(
            chunk_id="chunk-1",
            text="First candidate.",
            score=0.9,
            metadata={},
        ),
    ]

    reranked = reranker.rerank("stress toy", candidates, top_k=1)

    assert isinstance(reranker, NoOpReranker)
    assert [result.chunk_id for result in reranked] == ["chunk-2"]


def test_reranker_factory_preserves_configured_options_for_non_default_fallback() -> None:
    """Require settings-selected fallback providers to receive their own config.

    The configured default can be ``llm`` while orchestration has not injected a
    live ``BaseLLM`` yet. If the fallback is changed from ``rrf`` to another
    concrete provider, the factory must reload that fallback provider's options
    instead of reusing the unavailable LLM provider options.
    """

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    settings.rerank.fallback = "cross_encoder"
    scorer = SimpleNamespace(predict=lambda pairs: [0.42])

    reranker = RerankerFactory.create(settings=settings, scorer=scorer)

    assert isinstance(reranker, CrossEncoderReranker)
    result = reranker.rerank(
        "query",
        [
            types_module.RetrievalResult(
                chunk_id="chunk-1",
                text="Candidate.",
                score=0.1,
                metadata={},
            )
        ],
    )
    assert result[0].metadata["rerank"]["model"] == "BAAI/bge-reranker-base"


def test_fake_reranker_and_evaluator_expose_provider_independent_contracts() -> None:
    """Require deterministic fake implementations for later pipeline tests."""

    reranker = RerankerFactory.create(
        provider="fake",
        ordered_chunk_ids=["chunk-1", "chunk-2"],
    )
    evaluator = EvaluatorFactory.create(
        provider="fake",
        metrics={"hit_rate_at_5": 1.0, "mrr": 0.75},
    )
    candidates = [
        types_module.RetrievalResult(
            chunk_id="chunk-2",
            text="Second candidate.",
            score=0.8,
            metadata={},
        ),
        types_module.RetrievalResult(
            chunk_id="chunk-1",
            text="First candidate.",
            score=0.7,
            metadata={},
        ),
    ]

    reranked = reranker.rerank("query", candidates)
    metrics = evaluator.evaluate(
        dataset=[{"query": "query", "relevant_ids": ["chunk-1"]}],
        predictions=[{"chunk_ids": ["chunk-1", "chunk-2"]}],
    )

    assert isinstance(reranker, FakeReranker)
    assert [result.chunk_id for result in reranked] == ["chunk-1", "chunk-2"]
    assert isinstance(evaluator, FakeEvaluator)
    assert metrics == {"hit_rate_at_5": 1.0, "mrr": 0.75}


def test_b11_factories_fail_fast_for_unknown_or_unimplemented_providers() -> None:
    """Prevent unknown provider names from silently resolving to fake code."""

    with pytest.raises(ConfigurationError) as vector_error:
        VectorStoreFactory.create(provider="missing")
    with pytest.raises(ConfigurationError) as reranker_error:
        RerankerFactory.create(provider="missing")
    with pytest.raises(ConfigurationError) as evaluator_error:
        EvaluatorFactory.create(provider="custom")

    assert vector_error.value.context["provider"] == "missing"
    assert reranker_error.value.context["provider"] == "missing"
    assert evaluator_error.value.context["provider"] == "custom"


def test_deepseek_client_uses_openai_compatible_chat_contract() -> None:
    """Require the Bailian DeepSeek adapter to normalize SDK requests and output."""

    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = SimpleNamespace(
        id="chat-response-1",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Use the first product."),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=5,
            total_tokens=17,
        ),
    )
    llm = LLMFactory.create(
        provider="deepseek",
        model="deepseek-v4-flash",
        api_key_env="DASHSCOPE_API_KEY",
        base_url_env="DASHSCOPE_BASE_URL",
        timeout_seconds=30,
        client=sdk_client,
    )

    response = llm.chat(
        [
            ChatMessage(role="system", content="Answer concisely."),
            ChatMessage(role="user", content="Compare these products."),
        ]
    )

    assert isinstance(llm, DeepSeekClient)
    sdk_client.chat.completions.create.assert_called_once_with(
        model="deepseek-v4-flash",
        messages=[
            {"role": "system", "content": "Answer concisely."},
            {"role": "user", "content": "Compare these products."},
        ],
    )
    assert response.content == "Use the first product."
    assert response.provider == "deepseek"
    assert response.raw == {
        "response_id": "chat-response-1",
        "finish_reason": "stop",
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
    }


def test_deepseek_client_wraps_sdk_failures_without_exposing_secrets() -> None:
    """Require provider transport failures to cross the shared error boundary."""

    sdk_client = Mock()
    sdk_client.chat.completions.create.side_effect = RuntimeError(
        "request failed with secret-key-value"
    )
    llm = LLMFactory.create(
        provider="deepseek",
        model="deepseek-v4-flash",
        client=sdk_client,
    )

    with pytest.raises(errors_module.ProviderError) as captured:
        llm.chat([ChatMessage(role="user", content="Hello")])

    assert captured.value.context == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
    }
    assert "secret-key-value" not in str(captured.value)


def test_openai_embedding_batches_once_and_restores_input_order() -> None:
    """Require batch embeddings to follow response indexes rather than SDK order."""

    sdk_client = Mock()
    sdk_client.embeddings.create.return_value = SimpleNamespace(
        data=[
            SimpleNamespace(index=1, embedding=[0.0, 1.0, 0.0]),
            SimpleNamespace(index=0, embedding=[1.0, 0.0, 0.0]),
        ]
    )
    embedding = EmbeddingFactory.create(
        provider="openai",
        model="text-embedding-3-small",
        dimensions=3,
        api_key_env="OPENAI_API_KEY",
        timeout_seconds=30,
        client=sdk_client,
    )

    vectors = embedding.embed_batch(["first", "second"])

    assert isinstance(embedding, OpenAIEmbedding)
    sdk_client.embeddings.create.assert_called_once_with(
        model="text-embedding-3-small",
        input=["first", "second"],
        dimensions=3,
    )
    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


def test_real_provider_factories_read_checked_in_settings_with_injected_clients() -> None:
    """Require B12 implementations to use settings without performing network I/O."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    sdk_client = Mock()
    pool = Mock()

    llm = LLMFactory.create(settings=settings, client=sdk_client)
    embedding = EmbeddingFactory.create(settings=settings, client=sdk_client)
    vector_store = VectorStoreFactory.create(settings=settings, pool=pool)

    assert isinstance(llm, DeepSeekClient)
    assert isinstance(embedding, OpenAIEmbedding)
    assert isinstance(vector_store, PgVectorStore)


def test_real_provider_clients_wrap_sdk_initialization_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep proxy or endpoint setup failures inside the configuration boundary."""

    deepseek_module = importlib.import_module("src.libs.llm.deepseek_client")
    openai_embedding_module = importlib.import_module(
        "src.libs.embedding.openai_embedding"
    )
    monkeypatch.setattr(
        deepseek_module,
        "OpenAI",
        Mock(side_effect=RuntimeError("invalid proxy with secret-value")),
    )

    with pytest.raises(ConfigurationError) as deepseek_error:
        LLMFactory.create(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key_env="DASHSCOPE_API_KEY",
            base_url_env="DASHSCOPE_BASE_URL",
            environ={
                "DASHSCOPE_API_KEY": "secret-value",
                "DASHSCOPE_BASE_URL": "https://dashscope.example.test/v1",
            },
        )

    monkeypatch.setattr(
        openai_embedding_module,
        "OpenAI",
        Mock(side_effect=RuntimeError("invalid proxy with secret-value")),
    )
    with pytest.raises(ConfigurationError) as embedding_error:
        EmbeddingFactory.create(
            provider="openai",
            model="text-embedding-3-small",
            dimensions=3,
            api_key_env="OPENAI_API_KEY",
            environ={"OPENAI_API_KEY": "secret-value"},
        )

    assert deepseek_error.value.context == {"provider": "deepseek"}
    assert embedding_error.value.context == {"provider": "openai"}
    assert "secret-value" not in str(deepseek_error.value)
    assert "secret-value" not in str(embedding_error.value)
