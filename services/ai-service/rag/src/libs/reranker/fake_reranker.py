"""Provide deterministic candidate ordering for reranker unit tests."""

from __future__ import annotations

from collections.abc import Sequence

from src.core.types import RetrievalResult
from src.libs.reranker.base_reranker import BaseReranker


class FakeReranker(BaseReranker):
    """Order candidates by a configured sequence of stable chunk IDs."""

    def __init__(self, *, ordered_chunk_ids: Sequence[str] | None = None) -> None:
        """Configure deterministic candidate priority.

        Args:
            ordered_chunk_ids: Chunk IDs placed first in the specified order.
                Candidates not listed retain their original relative order.
        """

        self._rank = {
            chunk_id: index for index, chunk_id in enumerate(ordered_chunk_ids or ())
        }

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Return deep-copied candidates in deterministic configured order.

        Args:
            query: Query accepted for interface compatibility; the fake performs
                no semantic scoring.
            candidates: Current retrieval candidates.
            top_k: Optional positive result limit.

        Returns:
            Reordered candidate copies.

        Raises:
            ValueError: If the query is blank or ``top_k`` is not positive.
        """

        if not query.strip():
            raise ValueError("Rerank query must not be blank")
        if top_k is not None and top_k <= 0:
            raise ValueError("top_k must be greater than zero")

        indexed_candidates = list(enumerate(candidates))
        unlisted_offset = len(self._rank)
        indexed_candidates.sort(
            key=lambda item: (
                self._rank.get(item[1].chunk_id, unlisted_offset + item[0]),
                item[0],
            )
        )
        reranked = [candidate.model_copy(deep=True) for _, candidate in indexed_candidates]
        return reranked if top_k is None else reranked[:top_k]
