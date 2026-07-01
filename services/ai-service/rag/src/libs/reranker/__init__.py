"""Define the reranker component namespace for candidate reordering adapters.

Reranker implementations will score and reorder retrieval candidates using
Cross-Encoder, LLM, or fallback strategies without changing hybrid retrieval
code. The namespace exports only provider-independent contracts and concrete
adapters that satisfy the shared ``BaseReranker`` interface.
"""

from src.libs.reranker.base_reranker import BaseReranker
from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
from src.libs.reranker.fake_reranker import FakeReranker
from src.libs.reranker.llm_reranker import LLMReranker
from src.libs.reranker.no_op_reranker import NoOpReranker
from src.libs.reranker.qwen_reranker import QwenReranker
from src.libs.reranker.reranker_factory import RerankerFactory

__all__ = (
    "BaseReranker",
    "CrossEncoderReranker",
    "FakeReranker",
    "LLMReranker",
    "NoOpReranker",
    "QwenReranker",
    "RerankerFactory",
)
