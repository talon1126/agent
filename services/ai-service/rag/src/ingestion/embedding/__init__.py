"""Expose ingestion-stage dense embedding orchestration.

The ingestion embedding package starts with DenseEncoder and EmbeddingStep.
DenseEncoder owns chunk-level content hashing and one-at-a-time vector
generation. EmbeddingStep coordinates dense encoding across ordered chunks
without adding batching, retry, BM25, or upsert behavior; those responsibilities
belong to later C7-C9 tasks.
"""

from src.ingestion.embedding.dense_encoder import DenseEncoder, DenseEncodingResult
from src.ingestion.embedding.embedding_step import EmbeddingStep

__all__ = (
    "DenseEncoder",
    "DenseEncodingResult",
    "EmbeddingStep",
)
