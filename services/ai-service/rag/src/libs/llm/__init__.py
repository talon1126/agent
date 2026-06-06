"""Define the LLM component namespace for chat-model clients.

LLM implementations will expose a consistent chat contract for OpenAI, Azure
OpenAI, Ollama, DeepSeek, and test doubles while hiding provider-specific SDK
details from pipeline code. This package only establishes the B7 directory
boundary; B9 adds interfaces, factories, and implementations.
"""

from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse
from src.libs.llm.fake_llm import FakeLLM
from src.libs.llm.llm_factory import LLMFactory

__all__ = (
    "BaseLLM",
    "ChatMessage",
    "FakeLLM",
    "LLMFactory",
    "LLMResponse",
)
