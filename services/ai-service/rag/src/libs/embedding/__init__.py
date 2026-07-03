"""Define the embedding component namespace for dense vector clients.

Embedding implementations provide single-text and batch vector generation
behind a common interface so ingestion code can switch providers through
configuration. The package exports deterministic fake vectors and the
OpenAI-compatible adapter used by DashScope ``text-embedding-v4`` and OpenAI
models through one registry-backed factory.
"""

from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.embedding.fake_embedding import FakeEmbedding
from src.libs.embedding.openai_embedding import OpenAIEmbedding

__all__ = (
    "BaseEmbedding",
    "EmbeddingFactory",
    "FakeEmbedding",
    "OpenAIEmbedding",
)
