"""Validate the static configuration contracts consumed by later RAG tasks.

This module reads YAML directly instead of using the future ``RagSettings``
loader. That boundary is intentional: A3 and A4 must prove that checked-in
examples are complete, credential-safe, and structurally stable before A5 adds
runtime interpolation and validation behavior.

Failures in this suite indicate configuration drift between ``DEV_SPEC.md``
and the settings or prompt files, not failures in provider SDKs or external
services.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

RAG_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = RAG_ROOT / "config" / "settings.example.yaml"
PROMPTS_DIR = RAG_ROOT / "config" / "prompts"

# The RAG subsystem is independently installable, but repository-level pytest
# runs it directly from source. Add only this module root so imports match the
# package layout that an editable or wheel installation exposes.
sys.path.insert(0, str(RAG_ROOT))

config_module = importlib.import_module("src.core.config")
PromptTemplate = config_module.PromptTemplate
RagSettings = config_module.RagSettings
load_prompt = config_module.load_prompt
load_settings = config_module.load_settings


def load_settings_document() -> dict[str, Any]:
    """Parse the checked-in settings example as an unprocessed YAML mapping.

    Returns:
        The top-level settings mapping exactly as represented in
        ``config/settings.example.yaml``. Environment-variable names remain
        strings; no secrets are resolved and no defaults are injected.

    Raises:
        OSError: If the settings file cannot be read.
        yaml.YAMLError: If the file contains invalid YAML.
        AssertionError: If the YAML root is not a mapping, which violates the
            configuration contract expected by the runtime loader.
    """
    content = SETTINGS_PATH.read_text(encoding="utf-8")
    settings = yaml.safe_load(content)

    assert isinstance(settings, dict)
    return settings


def load_prompt_document(file_name: str) -> dict[str, Any]:
    """Parse one versioned prompt definition without rendering placeholders.

    Args:
        file_name: File name relative to ``config/prompts``. Callers provide
            only a known test fixture name; this helper does not discover or
            select prompts dynamically.

    Returns:
        The prompt definition mapping with template placeholders preserved for
        structural contract assertions.

    Raises:
        OSError: If the requested prompt file cannot be read.
        yaml.YAMLError: If the prompt definition contains invalid YAML.
        AssertionError: If the prompt document root is not a mapping.
    """
    content = (PROMPTS_DIR / file_name).read_text(encoding="utf-8")
    prompt = yaml.safe_load(content)

    assert isinstance(prompt, dict)
    return prompt


def test_settings_contains_required_sections() -> None:
    """Protect the single-entry configuration contract for all planned layers.

    The test requires one top-level section for each configurable subsystem so
    later factories and pipelines never need hidden constants or additional
    configuration files. A failure means a required architecture component is
    no longer represented in the canonical settings document.
    """
    settings = load_settings_document()

    assert {
        "project",
        "database",
        "llm",
        "vision_llm",
        "embedding",
        "vector_store",
        "splitter",
        "transform",
        "retrieval",
        "rerank",
        "response",
        "ingestion",
        "storage",
        "observability",
        "dashboard",
        "evaluation",
        "mcp",
    } <= settings.keys()


def test_runtime_settings_is_ignored_and_versioned_example_exists() -> None:
    """Protect separation between local runtime values and reviewed defaults.

    ``settings.yaml`` may contain machine-specific provider selections and must
    never be committed. The complete example remains versioned so clean
    checkouts, CI, and new developers retain an executable configuration
    contract.
    """

    gitignore = (RAG_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "config/settings.yaml" in gitignore.splitlines()
    assert SETTINGS_PATH.is_file()


def test_default_component_selection_matches_the_spec() -> None:
    """Protect the approved first-release provider and fallback selections.

    The assertions pin only architecture decisions already approved in
    ``DEV_SPEC.md``: DeepSeek chat, Qwen vision, DashScope embeddings, pgvector,
    recursive splitting, and RRF fallback. A failure indicates unreviewed
    provider drift rather than a model-runtime error.
    """
    settings = load_settings_document()

    assert settings["llm"]["default"] == "deepseek"
    assert settings["llm"]["providers"]["deepseek"]["model"] == "deepseek-v4-flash"
    assert settings["vision_llm"]["providers"]["qwen_vl_max"]["model"] == "qwen-vl-max"
    assert settings["embedding"]["default"] == "dashscope"
    assert settings["embedding"]["providers"]["dashscope"]["model"] == (
        "text-embedding-v4"
    )
    assert settings["embedding"]["providers"]["dashscope"]["dimensions"] == 1536
    assert settings["vector_store"]["provider"] == "pgvector"
    assert settings["splitter"]["default"] == "recursive_character"
    assert settings["rerank"]["fallback"] == "rrf"
    assert settings["ingestion"]["document_summary"]["llm_provider"] == "deepseek"
    assert settings["evaluation"]["llm_provider"] == "deepseek"
    assert settings["evaluation"]["embedding_provider"] == "dashscope"


def test_sensitive_values_are_referenced_by_environment_variable_name() -> None:
    """Ensure checked-in configuration references secrets without storing them.

    The test validates the environment-variable contract used by deployment
    tooling and scans the source text for common placeholder/key patterns. A
    failure signals either a broken runtime lookup name or a potential
    credential-management regression.
    """
    settings = load_settings_document()
    serialized = SETTINGS_PATH.read_text(encoding="utf-8")

    assert settings["database"]["url_env"] == "DATABASE_URL"
    assert settings["database"]["timezone"] == "Asia/Shanghai"
    assert settings["llm"]["providers"]["deepseek"]["api_key_env"] == ("DASHSCOPE_API_KEY")
    assert settings["embedding"]["providers"]["dashscope"]["api_key_env"] == (
        "DASHSCOPE_API_KEY"
    )
    assert settings["embedding"]["providers"]["dashscope"]["base_url_env"] == (
        "DASHSCOPE_BASE_URL"
    )
    assert "sk-" not in serialized
    assert "YOUR_API_KEY" not in serialized


def test_dashboard_lists_all_six_management_pages() -> None:
    """Protect the ordered six-page management-platform navigation contract.

    Dashboard implementation tasks will consume this exact list to build
    navigation dynamically. A failure means a required operations page was
    removed, renamed, or reordered without updating the approved design.
    """
    settings = load_settings_document()

    assert settings["dashboard"]["pages"] == [
        "overview",
        "ingestion_manage",
        "data_browser",
        "query_trace",
        "ingestion_trace",
        "evaluation",
    ]


def test_observability_transform_snapshot_defaults_are_bounded() -> None:
    """Trace snapshots should be enabled with explicit size boundaries."""

    settings = load_settings_document()

    assert settings["observability"]["transform_snapshots"] == {
        "enabled": True,
        "max_chunks_per_step": 20,
        "max_chars_per_chunk": 800,
        "include_unchanged_chunks": False,
    }


def test_retrieval_and_transform_defaults_are_complete() -> None:
    """Verify defaults can support the initial ingestion and retrieval flows.

    Candidate pools must be at least as large as the final result set, the
    shopping guide collection must remain the default filter, and every
    approved transform must be enabled for the first pipeline implementation.
    A failure exposes a configuration that would silently disable required RAG
    behavior or truncate retrieval prematurely.
    """
    settings = load_settings_document()

    assert settings["retrieval"]["dense_top_k"] >= settings["retrieval"]["final_top_k"]
    assert settings["retrieval"]["sparse_top_k"] >= settings["retrieval"]["final_top_k"]
    assert settings["retrieval"]["filters"]["default_collection"] == "shopping_guides"
    transform_steps = settings["transform"]["steps"]
    assert [step["name"] for step in transform_steps] == [
        "metadata_enrich",
        "rewrite_chunk",
        "semantic_merge",
        "denoise",
        "image_captioner",
    ]
    assert all(step["enabled"] for step in transform_steps)
    assert all("provider" not in step for step in transform_steps)
    assert transform_steps[-1]["prompt_path"] == "config/prompts/image_caption_prompt.yaml"


def test_response_context_optimizer_defaults_are_complete() -> None:
    """Protect the Agent-ready final context optimization configuration."""

    settings = load_settings_document()

    assert settings["response"]["evidence_context_optimizer"] == {
        "enabled": True,
        "llm_provider": "deepseek",
        "prompt_path": "config/prompts/evidence_context_prompt.yaml",
        "fallback_to_raw": True,
    }


def test_prompt_definitions_share_a_stable_contract() -> None:
    """Verify every prompt follows one versioned, renderer-friendly schema.

    The test protects metadata needed for prompt discovery, explicit input
    variables, separate system/user instructions, and a machine-readable output
    schema. It also confirms every declared variable appears in the user
    template, preventing runtime rendering calls from accepting unused inputs.
    """
    prompt_files = (
        "document_summary_prompt.yaml",
        "rerank_prompt.yaml",
        "rewrite_chunk_prompt.yaml",
        "semantic_merge_prompt.yaml",
        "image_caption_prompt.yaml",
        "evidence_context_prompt.yaml",
    )

    for file_name in prompt_files:
        prompt = load_prompt_document(file_name)

        assert {
            "name",
            "version",
            "description",
            "input_variables",
            "system_prompt",
            "user_prompt",
            "output_schema",
        } <= prompt.keys()
        assert isinstance(prompt["version"], int)
        assert prompt["version"] >= 1
        assert prompt["input_variables"]
        assert all(
            f"{{{variable}}}" in prompt["user_prompt"] for variable in prompt["input_variables"]
        )


def test_prompt_instruction_content_is_written_in_english() -> None:
    """Prevent CJK instructions from entering runtime Prompt templates.

    English is the canonical authoring language for system prompts, user
    templates, descriptions, and strategy guidance. Model output may still be
    requested in another language when the business task requires it, but the
    checked-in instructions themselves must remain reviewable by one shared
    engineering convention. The check targets CJK characters rather than all
    Unicode so valid English punctuation and technical symbols remain usable.
    A failure identifies an instruction that must be translated before the
    Prompt can be versioned and evaluated.
    """
    prompt_files = (
        "document_summary_prompt.yaml",
        "rerank_prompt.yaml",
        "rewrite_chunk_prompt.yaml",
        "semantic_merge_prompt.yaml",
        "image_caption_prompt.yaml",
    )
    cjk_pattern = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")

    assert cjk_pattern.search("Temperature ≥ 20 °C") is None
    assert cjk_pattern.search("中文 instruction") is not None

    for file_name in prompt_files:
        source = (PROMPTS_DIR / file_name).read_text(encoding="utf-8")

        assert cjk_pattern.search(source) is None, f"{file_name} contains CJK Prompt instructions"


def test_rerank_prompt_requires_structured_ranking_output() -> None:
    """Protect the reranker prompt's input and structured-output boundaries.

    Retrieval code must be able to associate every model score with the
    original candidate, so the prompt requires immutable candidate identifiers,
    numeric scores, and human-readable reasons in JSON. A failure would make
    deterministic parsing or ranking traceability unsafe.
    """
    prompt = load_prompt_document("rerank_prompt.yaml")

    assert prompt["input_variables"] == ["query", "candidates"]
    assert prompt["output_schema"]["type"] == "json"
    assert {"candidate_id", "score", "reason"} <= set(prompt["output_schema"]["item_fields"])


def test_document_summary_prompt_defines_short_semantic_summary() -> None:
    """Protect the document-level summary prompt used before chunk rewrite."""

    prompt = load_prompt_document("document_summary_prompt.yaml")

    assert prompt["input_variables"] == ["document_text", "metadata"]
    assert "concise document-level summary" in prompt["system_prompt"]
    assert "Do not copy the full document" in prompt["system_prompt"]
    assert prompt["output_schema"]["fields"]["summary"] == "string"


def test_rewrite_prompt_uses_only_chunk_text_and_document_summary() -> None:
    """Ensure chunk rewriting keeps provider input limited to semantic context.

    Metadata and image references are maintained by Python objects instead of
    being sent back to the LLM. A failure means rewrite prompts may leak
    structured ingestion metadata into provider calls or chunk content.
    """
    prompt = load_prompt_document("rewrite_chunk_prompt.yaml")

    assert prompt["input_variables"] == [
        "chunk_text",
        "document_summary",
    ]
    assert "Do not invent" in prompt["system_prompt"]
    assert "metadata" not in prompt["system_prompt"].lower()
    assert "image_refs" not in prompt["system_prompt"]
    assert "document_summary" in prompt["user_prompt"]
    assert "Metadata:" not in prompt["user_prompt"]
    assert "Image references:" not in prompt["user_prompt"]


def test_image_prompt_defines_quality_fallback_and_type_strategies() -> None:
    """Verify image captioning adapts by content type and can reject weak input.

    The six strategies cover the image categories expected in shopping and
    guide documents. The ``low_quality`` output gives the ingestion pipeline a
    deterministic fallback when an image is unreadable or has no retrieval
    value, preventing poor captions from contaminating the index.
    """
    prompt = load_prompt_document("image_caption_prompt.yaml")

    assert prompt["input_variables"] == ["image_type"]
    assert "Simplified Chinese" in prompt["system_prompt"]
    assert "verbatim" in prompt["system_prompt"]
    assert "document_context" not in prompt["user_prompt"]
    assert "Document context" not in prompt["user_prompt"]
    assert prompt["output_schema"]["low_quality_value"] == "low_quality"
    assert {
        "product",
        "parameters",
        "flowchart",
        "table",
        "ui_screenshot",
        "decorative",
    } <= prompt["image_type_strategies"].keys()


def test_load_settings_returns_typed_configuration() -> None:
    """Verify runtime loading exposes typed sections to orchestration code.

    Factories and pipelines must consume attribute-based configuration instead
    of reopening YAML or indexing unvalidated dictionaries. The supplied
    environment mapping models a complete local deployment without modifying
    the process environment during the test.
    """
    environment = {
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
        "DASHSCOPE_API_KEY": "test-dashscope-key",
        "DASHSCOPE_BASE_URL": "https://dashscope.example.test",
        "OPENAI_API_KEY": "test-openai-key",
    }

    settings = load_settings(SETTINGS_PATH, environ=environment)

    assert isinstance(settings, RagSettings)
    assert settings.project.default_collection == "shopping_guides"
    assert settings.llm.selected_provider.model == "deepseek-v4-flash"
    assert settings.embedding.selected_provider.dimensions == 1536
    assert settings.evaluation.llm_provider == "deepseek"
    assert settings.evaluation.embedding_provider == "dashscope"
    assert settings.dashboard.pages[-1] == "evaluation"


def test_load_settings_reports_a_missing_file_clearly(tmp_path: Path) -> None:
    """Verify configuration path errors identify the unreadable source.

    Startup must fail before provider construction when the configured settings
    file does not exist. A readable exception gives operators the exact path
    they need to correct instead of exposing an unrelated downstream failure.
    """
    missing_path = tmp_path / "missing-settings.yaml"

    with pytest.raises(ValueError, match="Settings file does not exist"):
        load_settings(missing_path, environ={}, validate_environment=False)


def test_load_settings_rejects_unknown_selected_provider(tmp_path: Path) -> None:
    """Verify provider selectors cannot reference an undefined implementation.

    A typo in ``llm.default`` would otherwise survive startup and fail only when
    the first chat request reaches the factory. Validation keeps this failure at
    the configuration boundary and includes the invalid provider name.
    """
    document = load_settings_document()
    document["llm"]["default"] = "missing-provider"
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="missing-provider"):
        load_settings(settings_path, environ={}, validate_environment=False)


def test_load_settings_requires_a_model_for_selected_provider(tmp_path: Path) -> None:
    """Verify an active model provider cannot omit its model identifier.

    Keeping ``model`` optional at the generic provider level is useful for
    non-model adapters such as rerank wrappers, but selected LLM, Vision, and
    Embedding providers cannot operate without it. Startup validation must catch
    the omission before a factory attempts to construct an SDK client.
    """
    document = load_settings_document()
    document["llm"]["providers"]["deepseek"].pop("model")
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="must define a model"):
        load_settings(settings_path, environ={}, validate_environment=False)


def test_load_settings_rejects_embedding_dimension_mismatch(tmp_path: Path) -> None:
    """Verify embedding output dimensions match the pgvector column contract.

    A mismatch cannot be repaired during upsert and would make every generated
    vector invalid for the configured schema. The loader therefore rejects the
    configuration before an embedding request or database write occurs.
    """
    document = load_settings_document()
    document["embedding"]["providers"]["dashscope"]["dimensions"] = 1024
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="Embedding dimensions"):
        load_settings(settings_path, environ={}, validate_environment=False)


def test_environment_validation_lists_only_active_requirements() -> None:
    """Verify missing runtime secrets are reported together and without fallback noise.

    The active database, selected chat model, enabled vision model, and selected
    embedding model require environment values. Inactive providers and fallback
    providers must not block startup until selected, while duplicate references
    such as the DashScope key appear only once in the error message.
    """
    settings = load_settings(SETTINGS_PATH, environ={}, validate_environment=False)

    with pytest.raises(ValueError) as error:
        settings.validate_environment({})

    message = str(error.value)
    assert "DATABASE_URL" in message
    assert "DASHSCOPE_API_KEY" in message
    assert "DASHSCOPE_BASE_URL" in message
    assert "OPENAI_API_KEY" not in message
    assert message.count("DASHSCOPE_API_KEY") == 1


def test_load_prompt_returns_a_validated_template() -> None:
    """Verify Prompt loading produces a typed, render-ready configuration object.

    Runtime transforms and rerankers need stable metadata, declared variables,
    and output schemas. Loading must preserve placeholders for the later render
    step while rejecting malformed Prompt files at the configuration boundary.
    """
    prompt = load_prompt("config/prompts/rerank_prompt.yaml")

    assert isinstance(prompt, PromptTemplate)
    assert prompt.name == "rerank"
    assert prompt.input_variables == ["query", "candidates"]
    assert "{query}" in prompt.user_prompt


def test_load_prompt_rejects_undeclared_or_unused_variables(tmp_path: Path) -> None:
    """Verify Prompt declarations and template placeholders cannot drift apart.

    A mismatch would either cause runtime formatting errors or accept inputs
    that never reach the model. The loader must identify the Prompt file and
    explain that its variable contract is invalid before any provider call.
    """
    prompt_path = tmp_path / "invalid-prompt.yaml"
    prompt_path.write_text(
        yaml.safe_dump(
            {
                "name": "invalid",
                "version": 1,
                "description": "Invalid Prompt fixture.",
                "input_variables": ["query"],
                "system_prompt": "Return structured output.",
                "user_prompt": "Candidates: {candidates}",
                "output_schema": {"type": "json"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Prompt variable contract"):
        load_prompt(prompt_path)
