"""Verify concrete reranker adapters without external model calls.

D7 introduces the Cross-Encoder adapter. These tests inject a deterministic
scorer so the adapter's query-document pair contract, ranking behavior, error
boundaries, and factory registration can be validated without downloading or
loading real model weights.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.errors import ProviderError
from src.core.types import RetrievalResult
from src.libs.reranker import CrossEncoderReranker, RerankerFactory


def _candidate(
    chunk_id: str,
    text: str,
    *,
    score: float = 0.1,
    metadata: dict[str, object] | None = None,
) -> RetrievalResult:
    """Build a rerank candidate fixture with valid retrieval fields."""

    return RetrievalResult(
        chunk_id=chunk_id,
        text=text,
        score=score,
        metadata=dict(metadata or {}),
    )


class RecordingScorer:
    """Deterministic Cross-Encoder-like scorer used by unit tests."""

    def __init__(self, scores: list[float]) -> None:
        """Configure the scores returned for one predict call."""

        self.scores = scores
        self.pairs: list[tuple[str, str]] | None = None

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Record query-document pairs and return configured scores."""

        self.pairs = pairs
        return self.scores


def test_cross_encoder_reranker_scores_query_document_pairs_and_sorts_descending() -> None:
    """Require CrossEncoderReranker to rank by model score, not input order."""

    scorer = RecordingScorer([0.2, 0.95, 0.4])
    reranker = CrossEncoderReranker(model="local-cross-encoder", scorer=scorer)
    candidates = [
        _candidate("chunk-a", "Basic stress ball.", score=0.01, metadata={"rank": 1}),
        _candidate("chunk-b", "Silent fidget cube for office use.", score=0.02),
        _candidate("chunk-c", "Weighted blanket buying guide.", score=0.03),
    ]

    results = reranker.rerank("quiet decompression toy", candidates)

    assert scorer.pairs == [
        ("quiet decompression toy", "Basic stress ball."),
        ("quiet decompression toy", "Silent fidget cube for office use."),
        ("quiet decompression toy", "Weighted blanket buying guide."),
    ]
    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-c", "chunk-a"]
    assert [result.score for result in results] == [0.95, 0.4, 0.2]
    assert results[0].metadata["rerank"] == {
        "provider": "cross_encoder",
        "model": "local-cross-encoder",
        "original_score": 0.02,
    }
    assert candidates[1].score == 0.02
    assert "rerank" not in candidates[1].metadata


def test_cross_encoder_reranker_preserves_stable_order_for_equal_scores_and_top_k() -> None:
    """Require equal rerank scores to preserve filtered RRF order."""

    reranker = CrossEncoderReranker(
        model="local-cross-encoder",
        scorer=RecordingScorer([0.5, 0.5, 0.1]),
    )
    candidates = [
        _candidate("chunk-a", "A"),
        _candidate("chunk-b", "B"),
        _candidate("chunk-c", "C"),
    ]

    results = reranker.rerank("query", candidates, top_k=2)

    assert [result.chunk_id for result in results] == ["chunk-a", "chunk-b"]


def test_cross_encoder_reranker_validates_query_and_top_k_before_scoring() -> None:
    """Require invalid inputs to fail without invoking the model scorer."""

    scorer = RecordingScorer([1.0])
    reranker = CrossEncoderReranker(model="local-cross-encoder", scorer=scorer)

    with pytest.raises(ValueError, match="Rerank query must not be blank"):
        reranker.rerank(" ", [_candidate("chunk-a", "A")])
    with pytest.raises(ValueError, match="top_k must be greater than zero"):
        reranker.rerank("query", [_candidate("chunk-a", "A")], top_k=0)

    assert scorer.pairs is None


def test_cross_encoder_reranker_returns_empty_candidates_without_loading_scorer() -> None:
    """Require empty candidate lists to return quickly without model access."""

    reranker = CrossEncoderReranker(model="local-cross-encoder", scorer=None)

    assert reranker.rerank("query", []) == []


@pytest.mark.parametrize(
    "scores",
    [
        [0.1],
        [float("nan"), 0.2],
    ],
)
def test_cross_encoder_reranker_wraps_invalid_score_outputs(scores: list[float]) -> None:
    """Require malformed scorer outputs to cross the ProviderError boundary."""

    reranker = CrossEncoderReranker(
        model="local-cross-encoder",
        scorer=RecordingScorer(scores),
    )

    with pytest.raises(ProviderError, match="Cross-Encoder rerank failed"):
        reranker.rerank("query", [_candidate("a", "A"), _candidate("b", "B")])


def test_cross_encoder_reranker_wraps_scorer_failures() -> None:
    """Require scorer runtime failures to preserve the provider boundary."""

    scorer = SimpleNamespace(
        predict=lambda pairs: (_ for _ in ()).throw(RuntimeError("model offline"))
    )
    reranker = CrossEncoderReranker(model="local-cross-encoder", scorer=scorer)

    with pytest.raises(ProviderError, match="Cross-Encoder rerank failed") as captured:
        reranker.rerank("query", [_candidate("chunk-a", "A")])

    assert isinstance(captured.value.cause, RuntimeError)
    assert captured.value.context["provider"] == "cross_encoder"


def test_reranker_factory_creates_cross_encoder_with_injected_scorer() -> None:
    """Require Cross-Encoder provider registration in the reranker factory."""

    scorer = RecordingScorer([0.7])

    reranker = RerankerFactory.create(
        provider="cross_encoder",
        model="local-cross-encoder",
        scorer=scorer,
    )
    results = reranker.rerank("query", [_candidate("chunk-a", "A")])

    assert isinstance(reranker, CrossEncoderReranker)
    assert [result.chunk_id for result in results] == ["chunk-a"]
