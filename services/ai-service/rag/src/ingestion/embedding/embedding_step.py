"""Coordinate dense embedding work across ordered chunks.

``EmbeddingStep`` is the first orchestration layer for indexing output. In C6
it only delegates to ``DenseEncoder`` and preserves output order for chunks
whose content hashes are not already indexed. It does not batch, retry, write
storage, or build BM25 indexes; those concerns are explicitly owned by later
pipeline tasks.
"""

from __future__ import annotations

from src.core.types import Chunk
from src.ingestion.embedding.dense_encoder import DenseEncoder, DenseEncodingResult


class EmbeddingStep:
    """Run dense encoding for chunks that need semantic vectors."""

    def __init__(self, *, dense_encoder: DenseEncoder) -> None:
        """Configure the dense encoder dependency.

        Args:
            dense_encoder: Chunk-level encoder used for content hashing and
                single-vector generation.
        """

        self._dense_encoder = dense_encoder

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
