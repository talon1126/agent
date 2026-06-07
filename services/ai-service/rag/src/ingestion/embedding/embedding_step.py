"""Coordinate dense and sparse indexing work across ordered chunks.

``EmbeddingStep`` is the indexing orchestration layer used by ingestion before
storage upsert. ``run_dense()`` preserves the narrow C6 one-at-a-time behavior
for callers that only need dense vectors. ``run_batch()`` is the C8 path: it
uses ``BatchProcessor`` for bounded Dense execution and wraps BM25 indexing in
the same batch boundary while leaving PostgreSQL writes to later tasks.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from src.core.types import Chunk
from src.ingestion.embedding.batch_processor import BatchFailure, BatchProcessor
from src.ingestion.embedding.bm25_indexer import BM25Indexer, BM25IndexResult
from src.ingestion.embedding.dense_encoder import DenseEncoder, DenseEncodingResult


class EmbeddingBatchResult(BaseModel):
    """Carry C8 Dense and BM25 batch outputs to later upsert orchestration."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    dense_results: list[DenseEncodingResult] = Field(default_factory=list)
    bm25_index: BM25IndexResult
    dense_failures: list[BatchFailure] = Field(default_factory=list)
    bm25_failures: list[BatchFailure] = Field(default_factory=list)
    dense_batches_processed: int = Field(ge=0)
    bm25_batches_processed: int = Field(ge=0)


class EmbeddingStep:
    """Run Dense and BM25 indexing steps for transformed chunks."""

    def __init__(
        self,
        *,
        dense_encoder: DenseEncoder,
        bm25_indexer: BM25Indexer | None = None,
        batch_processor: BatchProcessor | None = None,
    ) -> None:
        """Configure indexing dependencies.

        Args:
            dense_encoder: Chunk-level encoder used for content hashing and
                single-vector generation.
            bm25_indexer: Sparse indexer used by ``run_batch``. ``None`` uses
                the default BM25 implementation.
            batch_processor: Shared batch executor. ``None`` uses a conservative
                one-item batch with no retries for backwards-compatible tests.
        """

        self._dense_encoder = dense_encoder
        self._bm25_indexer = bm25_indexer or BM25Indexer()
        self._batch_processor = batch_processor or BatchProcessor(
            batch_size=1,
            max_retries=0,
        )

    def run_dense(
        self,
        chunks: list[Chunk],
        *,
        existing_content_hashes: set[str] | None = None,
    ) -> list[DenseEncodingResult]:
        """Encode chunks whose content hash is absent from storage.

        Args:
            chunks: Ordered chunks produced by transform and image captioning.
            existing_content_hashes: Hashes already indexed for this ingestion
                scope. ``None`` means nothing is known to be indexed.

        Returns:
            Dense encoding results in the same relative order as the chunks
            that required new embeddings.
        """

        existing = set(existing_content_hashes or set())
        results: list[DenseEncodingResult] = []
        for chunk in chunks:
            if not self._dense_encoder.should_encode(
                chunk,
                existing_content_hashes=existing,
            ):
                continue
            result = self._dense_encoder.encode(chunk)
            results.append(result)
            existing.add(result.content_hash)
        return results

    def run_batch(
        self,
        chunks: list[Chunk],
        *,
        existing_content_hashes: set[str] | None = None,
        existing_vectors_by_hash: Mapping[str, list[float]] | None = None,
    ) -> EmbeddingBatchResult:
        """Run Dense batch encoding and BM25 indexing for ordered chunks.

        Args:
            chunks: Ordered chunks produced by transform and image captioning.
            existing_content_hashes: Hashes already indexed for this ingestion
                scope. Hashes generated during this run are tracked internally
                so duplicate chunk text is encoded once.
            existing_vectors_by_hash: Durable vectors keyed by content hash.
                These vectors are reused without calling the embedding provider
                and expanded to the current ordered chunk IDs.

        Returns:
            ``EmbeddingBatchResult`` containing ordered Dense successes, Dense
            failures, one BM25 index result, BM25 failures, and batch counters.
        """

        reusable_vectors = {
            content_hash: list(vector)
            for content_hash, vector in (existing_vectors_by_hash or {}).items()
        }
        known_hashes = set(existing_content_hashes or set())
        known_hashes.update(reusable_vectors)
        dense_candidates = self._select_dense_candidates(
            chunks,
            existing_content_hashes=known_hashes,
        )
        dense_run = self._batch_processor.run(
            dense_candidates,
            process_batch=self._dense_encoder.encode_batch,
        )
        bm25_run = self._batch_processor.run(
            [chunks],
            process_batch=lambda batch: [self._bm25_indexer.index(batch[0])],
        )
        bm25_values = bm25_run.successful_values()
        bm25_index = (
            bm25_values[0]
            if bm25_values
            else BM25IndexResult(chunk_count=0, average_document_length=0.0)
        )
        vectors_by_hash = dict(reusable_vectors)
        for result in dense_run.successful_values():
            vectors_by_hash[result.content_hash] = list(result.vector)
        ordered_dense_results = [
            DenseEncodingResult(
                chunk_id=chunk.id,
                content_hash=self._dense_encoder.content_hash(chunk),
                vector=vectors_by_hash[self._dense_encoder.content_hash(chunk)],
                metadata={"chunk_index": chunk.chunk_index},
            )
            for chunk in chunks
            if self._dense_encoder.content_hash(chunk) in vectors_by_hash
        ]

        return EmbeddingBatchResult(
            dense_results=ordered_dense_results,
            bm25_index=bm25_index,
            dense_failures=dense_run.failures,
            bm25_failures=bm25_run.failures,
            dense_batches_processed=dense_run.batches_processed,
            bm25_batches_processed=bm25_run.batches_processed,
        )

    def _select_dense_candidates(
        self,
        chunks: list[Chunk],
        *,
        existing_content_hashes: set[str] | None,
    ) -> list[Chunk]:
        """Select chunks that need Dense encoding without mutating caller state.

        Args:
            chunks: Ordered candidate chunks.
            existing_content_hashes: Durable hashes already known to storage.

        Returns:
            Ordered chunks whose current content hash has not been seen in
            durable storage or earlier in this run.
        """

        existing = set(existing_content_hashes or set())
        candidates: list[Chunk] = []
        for chunk in chunks:
            if not self._dense_encoder.should_encode(
                chunk,
                existing_content_hashes=existing,
            ):
                continue
            candidates.append(chunk)
            existing.add(self._dense_encoder.content_hash(chunk))
        return candidates
