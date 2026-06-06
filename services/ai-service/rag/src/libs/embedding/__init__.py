"""Define the embedding component namespace for dense vector clients.

Embedding implementations will provide single-text and batch vector generation
behind a common interface so ingestion code can switch providers through
configuration. This package is an empty B7 boundary until B9 introduces the
base interface, factory, OpenAI implementation, and fake implementation.
"""

from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.embedding.fake_embedding import FakeEmbedding

__all__ = (
    "BaseEmbedding",
    "EmbeddingFactory",
    "FakeEmbedding",
)
