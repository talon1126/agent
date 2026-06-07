"""Provide an in-memory vector store for deterministic unit tests.

``FakeVectorStore`` implements the complete ``BaseVectorStore`` contract
without PostgreSQL. It stores deep copies of chunks, computes cosine similarity
locally, applies exact metadata filters, and preserves requested ID order. This
makes ingestion and retrieval tests realistic without silently substituting the
fake for the production ``pgvector`` provider.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from math import sqrt
from typing import Any

from src.core.types import Chunk, RetrievalResult
from src.libs.vector_store.base_vector_store import BaseVectorStore


class FakeVectorStore(BaseVectorStore):
    """Store chunk/vector pairs in memory using stable chunk IDs."""

    def __init__(self) -> None:
        """Create an empty isolated store for one test or pipeline instance."""

        self._entries: dict[str, tuple[Chunk, tuple[float, ...]]] = {}
        self._dimensions: int | None = None

    def upsert(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> list[str]:
        """Copy aligned chunk/vector pairs into the in-memory index.

        Args:
            chunks: Ordered chunks to insert or replace.
            vectors: Dense vectors aligned positionally with ``chunks``.

        Returns:
            Chunk IDs in input order.

        Raises:
            ValueError: If counts differ, a vector is empty, or vectors in one
                batch use inconsistent dimensions.

        Side Effects:
            Replaces existing entries whose stable chunk IDs match an input.
        """

        if len(chunks) != len(vectors):
            raise ValueError("Chunk and vector counts must match")

        normalized_vectors = [tuple(float(value) for value in vector) for vector in vectors]
        if any(not vector for vector in normalized_vectors):
            raise ValueError("Dense vectors must not be empty")
        dimensions = {len(vector) for vector in normalized_vectors}
        if len(dimensions) > 1:
            raise ValueError("Dense vectors in one batch must share dimensions")
        batch_dimensions = next(iter(dimensions), None)
        if (
            batch_dimensions is not None
            and self._dimensions is not None
            and batch_dimensions != self._dimensions
        ):
            raise ValueError(
                "Dense vector dimensions must match the existing vector-store index"
            )
        if batch_dimensions is not None and self._dimensions is None:
            self._dimensions = batch_dimensions

        upserted_ids: list[str] = []
        for chunk, vector in zip(chunks, normalized_vectors, strict=True):
            self._entries[chunk.id] = (chunk.model_copy(deep=True), vector)
            upserted_ids.append(chunk.id)
        return upserted_ids

    def search(
        self,
        vector: Sequence[float],
        *,
        filters: Mapping[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[RetrievalResult]:
        """Rank matching entries by cosine similarity.

        Args:
            vector: Dense query vector.
            filters: Optional metadata key/value pairs that all must match.
            top_k: Maximum result count.

        Returns:
            Filtered results ordered by descending cosine similarity and then
            stable chunk ID for deterministic ties. Each result carries a
            defensive copy of ``Chunk.source_ref`` inside metadata so the fake
            matches the production citation contract.

        Raises:
            ValueError: If ``top_k`` is not positive, the query vector is empty,
                or stored and query vector dimensions differ.
        """

        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        query_vector = tuple(float(value) for value in vector)
        if not query_vector:
            raise ValueError("Query vector must not be empty")

        required_metadata = dict(filters or {})
        results: list[RetrievalResult] = []
        for chunk, stored_vector in self._entries.values():
            if any(chunk.metadata.get(key) != value for key, value in required_metadata.items()):
                continue
            score = self._cosine_similarity(query_vector, stored_vector)
            metadata = deepcopy(chunk.metadata)
            if chunk.source_ref is not None:
                metadata["source_ref"] = deepcopy(chunk.source_ref)
            results.append(
                RetrievalResult(
                    chunk_id=chunk.id,
                    text=chunk.text,
                    score=score,
                    metadata=metadata,
                )
            )

        results.sort(key=lambda result: (-result.score, result.chunk_id))
        return results[:top_k]

    def get_by_ids(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        """Return deep-copied chunks in requested order.

        Args:
            chunk_ids: Ordered stable IDs. Missing IDs are ignored because BM25
                indexes may temporarily reference chunks removed by lifecycle
                cleanup.

        Returns:
            Existing chunk copies in caller-provided order.
        """

        chunks: list[Chunk] = []
        for chunk_id in chunk_ids:
            entry = self._entries.get(chunk_id)
            if entry is not None:
                chunks.append(entry[0].model_copy(deep=True))
        return chunks

    @staticmethod
    def _cosine_similarity(
        left: Sequence[float],
        right: Sequence[float],
    ) -> float:
        """Calculate cosine similarity for equal-length vectors.

        Args:
            left: Query vector.
            right: Stored chunk vector.

        Returns:
            Cosine similarity. A zero-norm vector produces ``0.0``.

        Raises:
            ValueError: If vector dimensions differ.
        """

        if len(left) != len(right):
            raise ValueError("Query and stored vector dimensions must match")
        dot_product = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = sqrt(sum(value * value for value in left))
        right_norm = sqrt(sum(value * value for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot_product / (left_norm * right_norm)
