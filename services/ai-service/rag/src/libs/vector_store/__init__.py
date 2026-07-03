"""Define the vector-store component namespace for retrieval storage adapters.

Vector-store implementations persist and search dense vectors plus related
metadata while keeping query orchestration independent from PostgreSQL/pgvector
details. The package exports the shared contract, deterministic in-memory fake,
and production PgVectorStore through one registry-backed factory.
"""

from src.libs.vector_store.base_vector_store import BaseVectorStore
from src.libs.vector_store.fake_vector_store import FakeVectorStore
from src.libs.vector_store.pgvector_store import PgVectorStore
from src.libs.vector_store.vector_store_factory import VectorStoreFactory

__all__ = (
    "BaseVectorStore",
    "FakeVectorStore",
    "PgVectorStore",
    "VectorStoreFactory",
)
