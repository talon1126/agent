"""Run opt-in smoke tests against configured external model providers.

These tests are excluded from normal development runs unless
``RUN_RAG_EXTERNAL_TESTS=1`` is set explicitly. This prevents accidental API
charges while retaining a repeatable way to verify Bailian DeepSeek and OpenAI
Embedding credentials before deployment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAG_ROOT))

from src.core.config import load_settings  # noqa: E402
from src.libs.embedding import EmbeddingFactory  # noqa: E402
from src.libs.llm import ChatMessage, LLMFactory  # noqa: E402

EXTERNAL_TESTS_ENABLED = os.getenv("RUN_RAG_EXTERNAL_TESTS") == "1"

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        not EXTERNAL_TESTS_ENABLED,
        reason="Set RUN_RAG_EXTERNAL_TESTS=1 to call external model providers",
    ),
]


def test_deepseek_provider_returns_non_blank_content() -> None:
    """Call the configured Bailian DeepSeek model through the common chat API."""

    # Provider constructors validate only the environment variables they use.
    # Avoid requiring DATABASE_URL for a model-only external smoke test.
    settings = load_settings(validate_environment=False)
    llm = LLMFactory.create(settings=settings)

    response = llm.chat([ChatMessage(role="user", content="Reply with: ok")])

    assert response.content.strip()
    assert response.provider == "deepseek"


def test_openai_embedding_returns_configured_dimensions() -> None:
    """Call OpenAI Embedding and verify the configured vector dimensions."""

    # Provider constructors validate only the environment variables they use.
    # Avoid requiring DATABASE_URL for a model-only external smoke test.
    settings = load_settings(validate_environment=False)
    embedding = EmbeddingFactory.create(settings=settings)

    vector = embedding.embed("RAG external provider smoke test")

    assert len(vector) == settings.embedding.selected_provider.dimensions
