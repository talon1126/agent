"""Define the provider-independent vector-store contract.

Vector stores sit between ingestion/retrieval orchestration and concrete
storage engines. The interface accepts validated ``Chunk`` objects plus dense
vectors, returns provider-independent ``RetrievalResult`` objects for semantic
search, and supports ordered ID lookup for the BM25 sparse route.

This module deliberately contains no PostgreSQL or pgvector code. Concrete
adapters own SQL, connection management, distance operators, and
metadata-filter translation.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from src.core.types import Chunk, RetrievalResult


class BaseVectorStore(ABC):
    """Specify the minimal storage operations required by RAG pipelines."""

    @abstractmethod
    def upsert(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> list[str]:
        """Insert or replace chunk vectors using stable chunk IDs.

        Args:
            chunks: Ordered business chunks ready for vector persistence.
            vectors: Dense vectors aligned positionally with ``chunks``.

        Returns:
            Upserted chunk IDs in the same order as the input chunks.

        Raises:
            ValueError: If chunk/vector counts or vector dimensions are invalid.
            ProviderError: Concrete providers may raise this when persistence
                fails.
        """

    async def async_search(
        self,
        vector: Sequence[float],
        *,
        filters: Mapping[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[RetrievalResult]:
        """Return dense-search results without blocking the event-loop caller.

        Args:
            vector: Dense query vector using the store's configured dimensions.
            filters: Optional exact-match metadata constraints.
            top_k: Maximum number of results to return.

        Returns:
            Results ordered from highest to lowest provider-native similarity.

        Raises:
            ValueError: If the vector or result limit is invalid.
            ProviderError: Concrete providers may raise this when search fails.
        """

        return await asyncio.to_thread(
            self.search,
            vector,
            filters=filters,
            top_k=top_k,
        )

    @abstractmethod
    def search(
        self,
        vector: Sequence[float],
        *,
        filters: Mapping[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[RetrievalResult]:
        """Return the highest-ranked chunks for one dense query vector.

        Args:
            vector: Dense query vector using the store's configured dimensions.
            filters: Optional exact-match metadata constraints.
            top_k: Maximum number of results to return.

        Returns:
            Results ordered from highest to lowest provider-native similarity.

        Raises:
            ValueError: If the vector or result limit is invalid.
            ProviderError: Concrete providers may raise this when search fails.
        """

    @abstractmethod
    def get_by_ids(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        """Fetch chunks in caller-provided order while skipping missing IDs.

        Args:
            chunk_ids: Stable chunk IDs produced by BM25 or other sparse routes.

        Returns:
            Existing chunks in the same relative order as ``chunk_ids``.

        Raises:
            ProviderError: Concrete providers may raise this when lookup fails.
        """
