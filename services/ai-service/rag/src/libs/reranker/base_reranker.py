"""Define the provider-independent retrieval reranker contract.

Rerankers receive already retrieved and metadata-filtered candidates. They may
use a Cross-Encoder, an LLM, deterministic test ordering, or a no-op fallback,
but query orchestration always calls the same method and receives
``RetrievalResult`` objects.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence

from src.core.types import RetrievalResult


class BaseReranker(ABC):
    """Specify the minimal candidate-reordering interface."""

    async def async_rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Reorder candidates without blocking the event-loop caller.

        Args:
            query: Original or rewritten user query used for relevance scoring.
            candidates: Filtered retrieval candidates in current RRF order.
            top_k: Optional maximum number of candidates to return.

        Returns:
            Reordered provider-independent retrieval results.

        Raises:
            ValueError: If inputs or ``top_k`` are invalid.
            ProviderError: Concrete providers may raise this when scoring fails.
        """

        return await asyncio.to_thread(
            self.rerank,
            query,
            candidates,
            top_k=top_k,
        )

    @abstractmethod
    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Reorder candidates for one query.

        Args:
            query: Original or rewritten user query used for relevance scoring.
            candidates: Filtered retrieval candidates in current RRF order.
            top_k: Optional maximum number of candidates to return.

        Returns:
            Reordered provider-independent retrieval results.

        Raises:
            ValueError: If inputs or ``top_k`` are invalid.
            ProviderError: Concrete providers may raise this when scoring fails.
        """
