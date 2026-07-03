"""Expose ingestion-stage dense, sparse, and batch indexing orchestration.

The ingestion embedding package contains the first indexing-stage adapters.
DenseEncoder owns chunk-level content hashing and one-at-a-time vector
generation. BM25Indexer owns in-memory sparse statistics and keyword ranking.
BatchProcessor provides bounded execution, failure isolation, and limited
retry behavior. EmbeddingStep coordinates Dense and BM25 work before later
upsert tasks persist the outputs.
"""

from src.ingestion.embedding.batch_processor import (
    BatchFailure,
    BatchProcessor,
    BatchRunResult,
    BatchSuccess,
)
from src.ingestion.embedding.bm25_indexer import (
    BM25Candidate,
    BM25Indexer,
    BM25IndexResult,
)
from src.ingestion.embedding.dense_encoder import DenseEncoder, DenseEncodingResult
from src.ingestion.embedding.embedding_step import EmbeddingBatchResult, EmbeddingStep

__all__ = (
    "BatchFailure",
    "BatchProcessor",
    "BatchRunResult",
    "BatchSuccess",
    "BM25Candidate",
    "BM25Indexer",
    "BM25IndexResult",
    "DenseEncoder",
    "DenseEncodingResult",
    "EmbeddingBatchResult",
    "EmbeddingStep",
)
