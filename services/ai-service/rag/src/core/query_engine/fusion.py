"""Fuse Dense and Sparse retrieval candidates with Reciprocal Rank Fusion.

RRF is the first ranking stage that combines semantic vector recall and BM25
keyword recall. It deliberately uses per-route rank positions instead of native
scores, because Dense cosine similarity and BM25 scores are not comparable
scales. The fused result remains a ``RetrievalResult`` so later HybridSearch,
filtering, reranking, response building, and trace stages can consume one
stable contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.core.errors import RetrievalError
from src.core.types import RetrievalResult


@dataclass
class _FusionCandidate:
    """Accumulate one chunk's route ranks, source scores, and payload."""

    chunk_id: str
    text: str
    metadata: dict[str, object]
    first_seen_index: int
    rrf_score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None
    dense_score: float | None = None
    sparse_score: float | None = None

    @property
    def best_rank(self) -> int:
        """Return the best available route rank for deterministic tie-breaking."""

        ranks = [
            rank for rank in (self.dense_rank, self.sparse_rank)
            if rank is not None
        ]
        return min(ranks)


def reciprocal_rank_fusion(
    dense_results: Sequence[RetrievalResult],
    sparse_results: Sequence[RetrievalResult],
    *,
    top_k: int,
    rrf_k: int,
) -> list[RetrievalResult]:
    """Fuse Dense and Sparse ranked lists by reciprocal rank contribution.

    Args:
        dense_results: Ranked semantic candidates from Dense Route.
        sparse_results: Ranked keyword candidates from Sparse Route.
        top_k: Maximum fused candidates to return.
        rrf_k: Positive RRF smoothing constant applied to every route rank.

    Returns:
        Fused candidates ordered by descending RRF score. Each result's
        ``score`` is the RRF score, while ``metadata["fusion"]`` records
        per-route ranks and original provider scores for later trace and
        debugging stages.

    Raises:
        RetrievalError: If ``top_k`` or ``rrf_k`` is invalid.

    Side Effects:
        None. The input ``RetrievalResult`` objects are never mutated.
    """

    _validate_positive_integer(top_k, name="Fusion top_k")
    _validate_positive_integer(rrf_k, name="RRF k")

    candidates: dict[str, _FusionCandidate] = {}
    first_seen_counter = 0
    first_seen_counter = _add_route_contributions(
        candidates,
        dense_results,
        route="dense",
        rrf_k=rrf_k,
        first_seen_counter=first_seen_counter,
    )
    _add_route_contributions(
        candidates,
        sparse_results,
        route="sparse",
        rrf_k=rrf_k,
        first_seen_counter=first_seen_counter,
    )

    ranked = sorted(
        candidates.values(),
        key=lambda candidate: (
            -candidate.rrf_score,
            candidate.best_rank,
            candidate.first_seen_index,
            candidate.chunk_id,
        ),
    )
    return [_to_retrieval_result(candidate) for candidate in ranked[:top_k]]


def _validate_positive_integer(value: int, *, name: str) -> None:
    """Validate ranking parameters before any fusion work begins.

    Args:
        value: Candidate integer parameter.
        name: Human-readable parameter name used in error messages.

    Raises:
        RetrievalError: If ``value`` is not an integer or is smaller than one.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise RetrievalError(
            f"{name} must be an integer",
            context={"received_type": type(value).__name__},
        )
    if value <= 0:
        raise RetrievalError(f"{name} must be greater than zero")


def _add_route_contributions(
    candidates: dict[str, _FusionCandidate],
    route_results: Sequence[RetrievalResult],
    *,
    route: str,
    rrf_k: int,
    first_seen_counter: int,
) -> int:
    """Add one retrieval route's first occurrence of every chunk to RRF.

    Args:
        candidates: Accumulator keyed by stable ``chunk_id``.
        route_results: Ranked results from one retrieval route.
        route: Route label, either ``dense`` or ``sparse``.
        rrf_k: Positive RRF smoothing constant.
        first_seen_counter: Monotonic counter used for stable tie-breaking.

    Returns:
        Updated first-seen counter after processing this route.
    """

    seen_in_route: set[str] = set()
    for index, result in enumerate(route_results, start=1):
        if result.chunk_id in seen_in_route:
            continue
        seen_in_route.add(result.chunk_id)

        candidate = candidates.get(result.chunk_id)
        if candidate is None:
            candidate = _FusionCandidate(
                chunk_id=result.chunk_id,
                text=result.text,
                metadata=dict(result.metadata),
                first_seen_index=first_seen_counter,
            )
            candidates[result.chunk_id] = candidate
            first_seen_counter += 1

        candidate.rrf_score += 1 / (rrf_k + index)
        if route == "dense":
            candidate.dense_rank = index
            candidate.dense_score = result.score
        else:
            candidate.sparse_rank = index
            candidate.sparse_score = result.score

    return first_seen_counter


def _to_retrieval_result(candidate: _FusionCandidate) -> RetrievalResult:
    """Convert accumulated fusion state into the shared retrieval contract.

    Args:
        candidate: Internal fused candidate state.

    Returns:
        ``RetrievalResult`` with RRF score and fusion diagnostics in metadata.
    """

    metadata = dict(candidate.metadata)
    sources: list[str] = []
    if candidate.dense_rank is not None:
        sources.append("dense")
    if candidate.sparse_rank is not None:
        sources.append("sparse")
    metadata["fusion"] = {
        "dense_rank": candidate.dense_rank,
        "sparse_rank": candidate.sparse_rank,
        "dense_score": candidate.dense_score,
        "sparse_score": candidate.sparse_score,
        "sources": sources,
    }
    return RetrievalResult(
        chunk_id=candidate.chunk_id,
        text=candidate.text,
        score=candidate.rrf_score,
        metadata=metadata,
    )
