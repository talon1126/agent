"""Define the reranker component namespace for candidate reordering adapters.

Reranker implementations will score and reorder retrieval candidates using
Cross-Encoder, LLM, or fallback strategies without changing hybrid retrieval
code. This B7 namespace receives concrete contracts and implementations in B11
and later reranker tasks.
"""

from src.libs.reranker.base_reranker import BaseReranker
from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
from src.libs.reranker.fake_reranker import FakeReranker
from src.libs.reranker.no_op_reranker import NoOpReranker
from src.libs.reranker.reranker_factory import RerankerFactory

__all__ = (
    "BaseReranker",
    "CrossEncoderReranker",
    "FakeReranker",
    "NoOpReranker",
    "RerankerFactory",
)
