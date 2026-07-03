"""Adapt a local CCSwitch model endpoint to the RAG LLM contract.

CCSwitch can expose a local OpenAI-compatible API endpoint backed by the user's
selected model, such as a 5.5-series model. This provider keeps that local proxy
visible in traces and Dashboard component views instead of hiding it behind the
DeepSeek provider label.
"""

from __future__ import annotations

from src.libs.llm.openai_compatible_llm import OpenAICompatibleLLM


class CCSwitchClient(OpenAICompatibleLLM):
    """Call the local CCSwitch OpenAI-compatible chat endpoint."""

    provider_name = "ccswitch"
    display_name = "CCSwitch"
