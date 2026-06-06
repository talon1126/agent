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

import re
from pathlib import Path
from typing import Any

import yaml


RAG_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = RAG_ROOT / "config" / "settings.yaml"
PROMPTS_DIR = RAG_ROOT / "config" / "prompts"


def load_settings_document() -> dict[str, Any]:
    """Parse the checked-in settings example as an unprocessed YAML mapping.

    Returns:
        The top-level settings mapping exactly as represented in
        ``config/settings.yaml``. Environment-variable names remain strings;
        no secrets are resolved and no defaults are injected.

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
        "ingestion",
        "storage",
        "observability",
        "dashboard",
        "evaluation",
        "mcp",
    } <= settings.keys()


def test_default_component_selection_matches_the_spec() -> None:
    """Protect the approved first-release provider and fallback selections.

    The assertions pin only architecture decisions already approved in
    ``DEV_SPEC.md``: DeepSeek chat, Qwen vision, OpenAI embeddings, pgvector,
    recursive splitting, and RRF fallback. A failure indicates unreviewed
    provider drift rather than a model-runtime error.
    """
    settings = load_settings_document()

    assert settings["llm"]["default"] == "deepseek"
    assert settings["llm"]["providers"]["deepseek"]["model"] == "deepseek-v4-flash"
    assert settings["vision_llm"]["providers"]["qwen_vl_max"]["model"] == "Qwen-VL-Max"
    assert settings["embedding"]["providers"]["openai"]["model"] == (
        "text-embedding-3-small"
    )
    assert settings["vector_store"]["provider"] == "pgvector"
    assert settings["splitter"]["default"] == "recursive_character"
    assert settings["rerank"]["fallback"] == "rrf"


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
    assert settings["llm"]["providers"]["deepseek"]["api_key_env"] == (
        "DASHSCOPE_API_KEY"
    )
    assert settings["embedding"]["providers"]["openai"]["api_key_env"] == (
        "OPENAI_API_KEY"
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
    assert all(
        settings["transform"][name]["enabled"]
        for name in ("rewrite_chunk", "semantic_merge", "denoise", "image_to_text")
    )


def test_prompt_definitions_share_a_stable_contract() -> None:
    """Verify every prompt follows one versioned, renderer-friendly schema.

    The test protects metadata needed for prompt discovery, explicit input
    variables, separate system/user instructions, and a machine-readable output
    schema. It also confirms every declared variable appears in the user
    template, preventing runtime rendering calls from accepting unused inputs.
    """
    prompt_files = (
        "rerank_prompt.yaml",
        "rewrite_chunk_prompt.yaml",
        "image_to_text_prompt.yaml",
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
        assert prompt["version"] == 1
        assert prompt["input_variables"]
        assert all(
            f"{{{variable}}}" in prompt["user_prompt"]
            for variable in prompt["input_variables"]
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
        "rerank_prompt.yaml",
        "rewrite_chunk_prompt.yaml",
        "image_to_text_prompt.yaml",
    )
    cjk_pattern = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")

    assert cjk_pattern.search("Temperature ≥ 20 °C") is None
    assert cjk_pattern.search("中文 instruction") is not None

    for file_name in prompt_files:
        source = (PROMPTS_DIR / file_name).read_text(encoding="utf-8")

        assert cjk_pattern.search(source) is None, (
            f"{file_name} contains CJK Prompt instructions"
        )


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
    assert {"candidate_id", "score", "reason"} <= set(
        prompt["output_schema"]["item_fields"]
    )


def test_rewrite_prompt_preserves_facts_and_image_references() -> None:
    """Ensure chunk rewriting cannot discard source facts or image linkage.

    The prompt receives text, metadata, and image references as separate inputs
    and explicitly prohibits fabrication. A failure means ingestion transforms
    could produce cleaner text at the cost of citation fidelity or multimodal
    context.
    """
    prompt = load_prompt_document("rewrite_chunk_prompt.yaml")

    assert prompt["input_variables"] == ["chunk_text", "metadata", "image_refs"]
    assert "Do not invent" in prompt["system_prompt"]
    assert "image_refs" in prompt["system_prompt"]


def test_image_prompt_defines_quality_fallback_and_type_strategies() -> None:
    """Verify image captioning adapts by content type and can reject weak input.

    The six strategies cover the image categories expected in shopping and
    guide documents. The ``low_quality`` output gives the ingestion pipeline a
    deterministic fallback when an image is unreadable or has no retrieval
    value, preventing poor captions from contaminating the index.
    """
    prompt = load_prompt_document("image_to_text_prompt.yaml")

    assert prompt["input_variables"] == ["image_type", "document_context"]
    assert "Simplified Chinese" in prompt["system_prompt"]
    assert "verbatim" in prompt["system_prompt"]
    assert prompt["output_schema"]["low_quality_value"] == "low_quality"
    assert {
        "product",
        "parameters",
        "flowchart",
        "table",
        "ui_screenshot",
        "decorative",
    } <= prompt["image_type_strategies"].keys()
