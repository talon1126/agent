"""Preserve filtered RRF order when reranking is disabled or unavailable.

The no-op implementation is the safe degradation target for ``none``, ``rrf``,
and ``fallback`` provider names. It never invents new scores or changes ranking;
it only applies the requested final result limit.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.core.types import RetrievalResult
from src.libs.reranker.base_reranker import BaseReranker


class NoOpReranker(BaseReranker):
    """Return candidates in their existing order."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Preserve candidate order while returning defensive copies.

        Args:
            query: Query accepted for interface compatibility. No scoring occurs.
            candidates: Filtered RRF candidates whose order must be retained.
            top_k: Optional positive result limit.

        Returns:
            Candidate copies in unchanged order.

        Raises:
            ValueError: If the query is blank or ``top_k`` is not positive.
        """

        if not query.strip():
            raise ValueError("Rerank query must not be blank")
        if top_k is not None and top_k <= 0:
            raise ValueError("top_k must be greater than zero")

        preserved = [candidate.model_copy(deep=True) for candidate in candidates]
        return preserved if top_k is None else preserved[:top_k]
