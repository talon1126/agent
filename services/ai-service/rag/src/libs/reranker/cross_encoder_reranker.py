"""Adapt Cross-Encoder models to the provider-independent reranker contract.

``CrossEncoderReranker`` scores ``(query, candidate.text)`` pairs, sorts
candidates by the returned relevance score, and returns defensive
``RetrievalResult`` copies. The adapter accepts an injected scorer for unit
tests and local composition, while the default path lazily loads
``sentence_transformers.CrossEncoder`` only when candidates actually need
scoring.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from src.core.errors import ProviderError
from src.core.types import RetrievalResult
from src.libs.reranker.base_reranker import BaseReranker


class CrossEncoderScorer(Protocol):
    """Describe the minimal prediction method used by CrossEncoderReranker."""

    def predict(self, pairs: list[tuple[str, str]]) -> Sequence[float]:
        """Return one relevance score per query-document pair.

        Args:
            pairs: Ordered ``(query, document_text)`` pairs.

        Returns:
            Scores aligned positionally with ``pairs``.
        """


class CrossEncoderReranker(BaseReranker):
    """Score filtered candidates with a Cross-Encoder and reorder them."""

    def __init__(
        self,
        *,
        model: str,
        device: str | None = None,
        scorer: CrossEncoderScorer | None = None,
    ) -> None:
        """Configure the Cross-Encoder adapter.

        Args:
            model: Cross-Encoder model name or local path.
            device: Optional device passed to ``sentence_transformers``.
            scorer: Optional injected scorer implementing ``predict``. Tests use
                this to avoid external model loading.

        Raises:
            ValueError: If ``model`` is blank.
        """

        if not model.strip():
            raise ValueError("Cross-Encoder model must not be blank")
        self._model = model
        self._device = device
        self._scorer = scorer

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Score query-candidate pairs and return ranked candidate copies.

        Args:
            query: Original or rewritten user query used for pair scoring.
            candidates: Metadata-filtered candidates in current RRF order.
            top_k: Optional positive result limit.

        Returns:
            Candidates ordered by descending Cross-Encoder score. Ties preserve
            the input order. Result metadata receives a ``rerank`` diagnostic
            object with provider, model, and original score.

        Raises:
            ValueError: If query or ``top_k`` is invalid.
            ProviderError: If model loading, prediction, or score validation
                fails.
        """

        if not query.strip():
            raise ValueError("Rerank query must not be blank")
        if top_k is not None and top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if not candidates:
            return []

        pairs = [(query, candidate.text) for candidate in candidates]
        try:
            scores = [float(score) for score in self._get_scorer().predict(pairs)]
            self._validate_scores(scores, expected_count=len(candidates))
        except Exception as error:
            raise ProviderError(
                "Cross-Encoder rerank failed",
                context={
                    "provider": "cross_encoder",
                    "model": self._model,
                    "candidate_count": len(candidates),
                },
                cause=error,
            ) from error

        indexed_results = [
            (
                index,
                self._with_rerank_score(candidate, score),
            )
            for index, (candidate, score) in enumerate(zip(candidates, scores, strict=True))
        ]
        indexed_results.sort(key=lambda item: (-item[1].score, item[0]))
        results = [result for _, result in indexed_results]
        return results if top_k is None else results[:top_k]

    def _get_scorer(self) -> CrossEncoderScorer:
        """Return an injected scorer or lazily load ``sentence_transformers``.

        Returns:
            Cross-Encoder-compatible scorer.

        Raises:
            ProviderError: If the optional dependency is unavailable or the
                model cannot be constructed.
        """

        if self._scorer is not None:
            return self._scorer
        try:
            from sentence_transformers import CrossEncoder

            self._scorer = CrossEncoder(self._model, device=self._device)
        except Exception as error:
            raise ProviderError(
                "Unable to load Cross-Encoder reranker",
                context={
                    "provider": "cross_encoder",
                    "model": self._model,
                    "device": self._device,
                },
                cause=error,
            ) from error
        return self._scorer

    @staticmethod
    def _validate_scores(scores: list[float], *, expected_count: int) -> None:
        """Validate scorer output before ranking candidates.

        Args:
            scores: Float-normalized scores returned by the scorer.
            expected_count: Required number of scores.

        Raises:
            ValueError: If count or numeric values are invalid.
        """

        if len(scores) != expected_count:
            raise ValueError(
                "Cross-Encoder score count must match candidate count"
            )
        if any(not math.isfinite(score) for score in scores):
            raise ValueError("Cross-Encoder scores must be finite")

    def _with_rerank_score(
        self,
        candidate: RetrievalResult,
        score: float,
    ) -> RetrievalResult:
        """Return a candidate copy with Cross-Encoder score diagnostics.

        Args:
            candidate: Original retrieval candidate.
            score: Cross-Encoder relevance score.

        Returns:
            Retrieval result copy whose score is the Cross-Encoder score.
        """

        metadata = dict(candidate.metadata)
        metadata["rerank"] = {
            "provider": "cross_encoder",
            "model": self._model,
            "original_score": candidate.score,
        }
        return RetrievalResult(
            chunk_id=candidate.chunk_id,
            text=candidate.text,
            score=score,
            metadata=metadata,
        )
