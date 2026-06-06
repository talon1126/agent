"""Coordinate offline document ingestion and indexing workflows.

Modules under this package will compose document deduplication, loading,
splitting, transformation, image captioning, dense encoding, BM25 indexing,
batch processing, and storage upserts into one ingestion pipeline.

Provider-specific parsing, embedding, and storage behavior belongs to
``src.libs`` implementations or ``src.storage`` adapters. This package owns
the business sequence and data flow between those components.
"""

from src.ingestion.pipeline import (
    IngestionPipeline,
    IngestionRunResult,
    calculate_sha256,
    should_skip_document,
)

__all__ = (
    "IngestionPipeline",
    "IngestionRunResult",
    "calculate_sha256",
    "should_skip_document",
)
