"""Verify concrete reranker adapters without external model calls.

These tests inject deterministic Cross-Encoder scorers and fake LLM clients so
reranker contracts, ranking behavior, error boundaries, and factory
registration can be validated without downloading model weights or calling an
external chat provider.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.errors import ProviderError
from src.core.types import RetrievalResult
from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse
from src.libs.reranker import CrossEncoderReranker, LLMReranker, RerankerFactory


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


class RecordingLLM(BaseLLM):
    """Fake chat client that records rerank prompts and returns JSON content."""

    def __init__(self, content: str) -> None:
        """Configure the response body returned by one or more chat calls.

        Args:
            content: Provider-independent LLM response text. Tests use JSON
                arrays matching ``config/prompts/rerank_prompt.yaml``.
        """

        self.content = content
        self.messages: list[ChatMessage] | None = None
        self.call_count = 0

    def chat(self, messages: list[ChatMessage]) -> LLMResponse:
        """Record the prompt and return the configured fake response.

        Args:
            messages: Normalized rerank system and user messages.

        Returns:
            A deterministic response object using trace-safe fake metadata.
        """

        self.messages = list(messages)
        self.call_count += 1
        return LLMResponse(
            content=self.content,
            provider="fake",
            model="fake-reranker",
        )


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


def test_llm_reranker_parses_structured_ranking_and_preserves_missing_candidates() -> None:
    """Require LLMReranker to apply returned IDs and append unmentioned results."""

    llm = RecordingLLM(
        """
        [
          {"candidate_id": "chunk-b", "score": 0.91, "reason": "Direct match"},
          {"candidate_id": "chunk-a", "score": 0.73, "reason": "Partial match"}
        ]
        """
    )
    reranker = LLMReranker(llm_client=llm, model="fake-reranker")
    candidates = [
        _candidate("chunk-a", "Fidget cube buying guide.", score=0.4),
        _candidate("chunk-b", "Quiet decompression toy comparison.", score=0.6),
        _candidate("chunk-c", "Desk organizer article.", score=0.3),
    ]

    results = reranker.rerank("quiet decompression toy", candidates)

    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-a", "chunk-c"]
    assert [message.role for message in llm.messages or []] == ["system", "user"]
    assert "chunk-b" in (llm.messages or [])[1].content
    assert results[0].score == 0.91
    assert results[0].metadata["rerank"] == {
        "provider": "llm",
        "model": "fake-reranker",
        "llm_provider": "fake",
        "original_score": 0.6,
        "reason": "Direct match",
    }
    assert results[2].score == 0.3
    assert "rerank" not in candidates[1].metadata


def test_llm_reranker_supports_top_k_and_empty_candidates_without_llm_call() -> None:
    """Require top_k truncation and empty input short-circuiting."""

    llm = RecordingLLM('[{"candidate_id": "chunk-b", "score": 0.8}]')
    reranker = LLMReranker(llm_client=llm, model="fake-reranker")

    assert reranker.rerank("query", [], top_k=2) == []
    results = reranker.rerank(
        "query",
        [_candidate("chunk-a", "A"), _candidate("chunk-b", "B")],
        top_k=1,
    )

    assert [result.chunk_id for result in results] == ["chunk-b"]
    assert llm.call_count == 1


def test_llm_reranker_validates_inputs_before_calling_provider() -> None:
    """Require invalid rerank inputs to fail before prompt construction."""

    llm = RecordingLLM("[]")
    reranker = LLMReranker(llm_client=llm, model="fake-reranker")

    with pytest.raises(ValueError, match="Rerank query must not be blank"):
        reranker.rerank(" ", [_candidate("chunk-a", "A")])
    with pytest.raises(ValueError, match="top_k must be greater than zero"):
        reranker.rerank("query", [_candidate("chunk-a", "A")], top_k=0)

    assert llm.call_count == 0


@pytest.mark.parametrize(
    "content,match",
    [
        ("not json", "LLM rerank failed"),
        ('[{"candidate_id": "missing", "score": 0.8}]', "LLM rerank failed"),
        (
            '[{"candidate_id": "chunk-a", "score": 0.8}, '
            '{"candidate_id": "chunk-a", "score": 0.7}]',
            "LLM rerank failed",
        ),
    ],
)
def test_llm_reranker_wraps_invalid_provider_output(
    content: str,
    match: str,
) -> None:
    """Require malformed LLM rankings to cross the ProviderError boundary."""

    reranker = LLMReranker(
        llm_client=RecordingLLM(content),
        model="fake-reranker",
    )

    with pytest.raises(ProviderError, match=match):
        reranker.rerank("query", [_candidate("chunk-a", "A")])


def test_reranker_factory_creates_llm_reranker_with_injected_client() -> None:
    """Require the reranker factory to expose the LLM provider explicitly."""

    llm = RecordingLLM('[{"candidate_id": "chunk-a", "score": 0.88}]')

    reranker = RerankerFactory.create(
        provider="llm",
        llm_client=llm,
        model="fake-reranker",
    )
    results = reranker.rerank("query", [_candidate("chunk-a", "A")])

    assert isinstance(reranker, LLMReranker)
    assert results[0].score == 0.88
