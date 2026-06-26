"""Adapt Bailian-hosted DeepSeek chat models to the local LLM contract.

Alibaba Cloud Bailian exposes DeepSeek models through an OpenAI-compatible
endpoint. This thin provider class supplies the DeepSeek identity while the
shared OpenAI-compatible adapter handles credential resolution, SDK calls,
response normalization, and trace-safe metadata.
"""

from __future__ import annotations

from src.libs.llm.openai_compatible_llm import OpenAICompatibleLLM


class DeepSeekClient(OpenAICompatibleLLM):
    """Call a Bailian DeepSeek model through an OpenAI-compatible client."""

    provider_name = "deepseek"
    display_name = "DeepSeek"
