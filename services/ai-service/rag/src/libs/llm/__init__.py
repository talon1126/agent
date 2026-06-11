"""Define the LLM component namespace for chat-model clients.

LLM implementations expose a consistent chat contract while hiding
provider-specific SDK details from pipeline code. The package currently exports
the deterministic fake and Bailian-hosted DeepSeek adapter; later OpenAI, Azure,
and Ollama clients must implement the same ``BaseLLM`` interface.
"""

from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse
from src.libs.llm.base_vision_llm import BaseVisionLLM, VisionCaptionResponse
from src.libs.llm.dashscope_vision_llm import DashScopeVisionLLM
from src.libs.llm.deepseek_client import DeepSeekClient
from src.libs.llm.fake_llm import FakeLLM
from src.libs.llm.llm_factory import LLMFactory

__all__ = (
    "BaseLLM",
    "BaseVisionLLM",
    "ChatMessage",
    "DashScopeVisionLLM",
    "DeepSeekClient",
    "FakeLLM",
    "LLMFactory",
    "LLMResponse",
    "VisionCaptionResponse",
)
