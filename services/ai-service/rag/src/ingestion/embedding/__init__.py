"""Expose ingestion-stage dense and sparse indexing orchestration.

The ingestion embedding package contains the first indexing-stage adapters.
DenseEncoder owns chunk-level content hashing and one-at-a-time vector
generation. BM25Indexer owns in-memory sparse statistics and keyword ranking.
EmbeddingStep coordinates dense encoding across ordered chunks without adding
batching, retry, BM25 orchestration, or upsert behavior; those responsibilities
belong to later C8-C9 tasks.
"""

from src.ingestion.embedding.bm25_indexer import (
    BM25Candidate,
    BM25Indexer,
    BM25IndexResult,
)
from src.ingestion.embedding.dense_encoder import DenseEncoder, DenseEncodingResult
from src.ingestion.embedding.embedding_step import EmbeddingStep

__all__ = (
    "BM25Candidate",
    "BM25Indexer",
    "BM25IndexResult",
    "DenseEncoder",
    "DenseEncodingResult",
    "EmbeddingStep",
)
