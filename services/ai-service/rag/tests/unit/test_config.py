"""Contract tests for the RAG configuration files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


RAG_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = RAG_ROOT / "config" / "settings.yaml"


def load_settings_document() -> dict[str, Any]:
    """Load the YAML document without applying runtime configuration behavior."""
    content = SETTINGS_PATH.read_text(encoding="utf-8")
    settings = yaml.safe_load(content)

    assert isinstance(settings, dict)
    return settings


def test_settings_contains_required_sections() -> None:
    """The single configuration entry point must cover every planned subsystem."""
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
    """Initial providers and models must match the approved architecture choices."""
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
    """Configuration examples must never contain deployable credentials."""
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
    """The configuration must expose the complete six-page dashboard contract."""
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
    """Retrieval and transform defaults must support the first pipeline implementation."""
    settings = load_settings_document()

    assert settings["retrieval"]["dense_top_k"] >= settings["retrieval"]["final_top_k"]
    assert settings["retrieval"]["sparse_top_k"] >= settings["retrieval"]["final_top_k"]
    assert settings["retrieval"]["filters"]["default_collection"] == "shopping_guides"
    assert all(
        settings["transform"][name]["enabled"]
        for name in ("rewrite_chunk", "semantic_merge", "denoise", "image_to_text")
    )
