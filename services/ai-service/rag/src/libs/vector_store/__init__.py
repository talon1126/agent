"""Define the vector-store component namespace for retrieval storage adapters.

Vector-store implementations persist and search dense vectors plus related
metadata while keeping query orchestration independent from PostgreSQL/pgvector
details. B11 provides the contract, registry-backed factory, and deterministic
fake. The production pgvector adapter is added separately in B12.
"""

from src.libs.vector_store.base_vector_store import BaseVectorStore
from src.libs.vector_store.fake_vector_store import FakeVectorStore
from src.libs.vector_store.vector_store_factory import VectorStoreFactory

__all__ = (
    "BaseVectorStore",
    "FakeVectorStore",
    "VectorStoreFactory",
)
