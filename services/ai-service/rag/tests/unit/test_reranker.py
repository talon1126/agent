"""Verify concrete reranker adapters without external model calls.

These tests inject deterministic Cross-Encoder scorers and fake LLM clients so
reranker contracts, ranking behavior, error boundaries, and factory
registration can be validated without downloading model weights or calling an
external chat provider.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.config import load_prompt, load_settings
from src.core.errors import ProviderError
from src.core.query_engine import RerankController, RerankOutcome
from src.core.types import RetrievalResult
from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse
from src.libs.reranker import (
    BaseReranker,
    CrossEncoderReranker,
    FakeReranker,
    LLMReranker,
    NoOpReranker,
    QwenReranker,
    RerankerFactory,
)
from src.libs.reranker.cross_encoder_reranker import CrossEncoderModelCache

RAG_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = RAG_ROOT / "config" / "settings.example.yaml"


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


class RecordingQwenScorer:
    """Deterministic Qwen reranker scorer used by unit tests."""

    def __init__(self, scores: list[float]) -> None:
        """Configure normalized relevance scores returned for one call."""

        self.scores = scores
        self.calls: list[dict[str, object]] = []

    def score(
        self,
        pairs: list[tuple[str, str]],
        *,
        instruction: str,
        max_length: int,
        batch_size: int,
    ) -> list[float]:
        """Record Qwen-specific scoring inputs and return fixed scores."""

        self.calls.append(
            {
                "pairs": pairs,
                "instruction": instruction,
                "max_length": max_length,
                "batch_size": batch_size,
            }
        )
        return self.scores


class CountingCrossEncoderLoader:
    """Fake Cross-Encoder loader that records cache and warmup behavior."""

    def __init__(self) -> None:
        """Initialize empty loader and scorer call logs."""

        self.load_calls: list[tuple[str, str | None]] = []
        self.scorers: list[DynamicScorer] = []

    def __call__(self, model: str, device: str | None) -> DynamicScorer:
        """Create one fake scorer and record its model cache key."""

        scorer = DynamicScorer()
        self.load_calls.append((model, device))
        self.scorers.append(scorer)
        return scorer


class DynamicScorer:
    """Return deterministic scores for any requested pair count."""

    def __init__(self) -> None:
        """Initialize predict call recording for cache tests."""

        self.predict_calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Record pairs and return descending scores of matching length."""

        self.predict_calls.append(list(pairs))
        return [float(len(pairs) - index) for index, _pair in enumerate(pairs)]


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


class RaisingReranker(BaseReranker):
    """Test reranker that can mutate its input before raising one error."""

    def __init__(self, error: Exception, *, mutate_input: bool = False) -> None:
        """Configure the provider failure exposed to RerankController.

        Args:
            error: Exception raised when reranking starts.
            mutate_input: Whether to corrupt the first candidate before raising,
                simulating an unsafe third-party adapter.
        """

        self.error = error
        self.mutate_input = mutate_input
        self.received_chunk_ids: list[str] = []

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Record candidate IDs, optionally mutate input, and raise the error."""

        self.received_chunk_ids = [candidate.chunk_id for candidate in candidates]
        if self.mutate_input and candidates:
            candidates[0].metadata["corrupted"] = True
        raise self.error


class RecordingReranker(BaseReranker):
    """Reranker fixture that records calls and returns reversed candidates."""

    def __init__(self) -> None:
        """Initialize an empty call log for skip-gate assertions."""

        self.call_count = 0
        self.received_chunk_ids: list[str] = []

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Record candidates and return a deterministic reversed order."""

        self.call_count += 1
        self.received_chunk_ids = [candidate.chunk_id for candidate in candidates]
        results = [candidate.model_copy(deep=True) for candidate in reversed(candidates)]
        return results if top_k is None else results[:top_k]


class ForeignCandidateReranker(BaseReranker):
    """Return a candidate that was not present in the filtered input."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Simulate an invalid provider that reintroduces a filtered candidate."""

        return [_candidate("filtered-out", "Must not be reintroduced.", score=1.0)]


class EmptyResultReranker(BaseReranker):
    """Return no candidates even though filtered input is available."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Simulate a provider that accidentally drops the candidate set."""

        return []


class DuplicateResultReranker(BaseReranker):
    """Return one allowed candidate twice to violate reorder-only semantics."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Duplicate the first candidate so controller validation must fallback."""

        return [candidates[0], candidates[0]]


def test_no_op_reranker_preserves_order_with_defensive_copies() -> None:
    """Protect the safe fallback provider used when model rerank is disabled."""

    candidates = [
        _candidate("chunk-a", "A", metadata={"rank": 1}),
        _candidate("chunk-b", "B", metadata={"rank": 2}),
    ]

    results = NoOpReranker().rerank("query", candidates, top_k=1)

    assert [result.chunk_id for result in results] == ["chunk-a"]
    assert results[0] is not candidates[0]
    results[0].metadata["rank"] = 99
    assert candidates[0].metadata["rank"] == 1


def test_no_op_reranker_validates_query_and_limit() -> None:
    """Require the fallback adapter to enforce the shared reranker contract."""

    reranker = NoOpReranker()

    with pytest.raises(ValueError, match="query must not be blank"):
        reranker.rerank(" ", [_candidate("chunk-a", "A")])
    with pytest.raises(ValueError, match="top_k must be greater than zero"):
        reranker.rerank("query", [_candidate("chunk-a", "A")], top_k=0)


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


def test_qwen_reranker_scores_pairs_with_instruction_and_sorts_descending() -> None:
    """Require QwenReranker to return normalized scores and diagnostics."""

    scorer = RecordingQwenScorer([0.15, 0.98, 0.44])
    reranker = QwenReranker(
        model="local-qwen-reranker",
        device="cuda",
        max_length=4096,
        batch_size=2,
        scorer=scorer,
    )
    candidates = [
        _candidate("chunk-a", "Generic paragraph.", score=0.01),
        _candidate("chunk-b", "Direct child seat evidence.", score=0.02),
        _candidate("chunk-c", "Partial safety context.", score=0.03),
    ]

    results = reranker.rerank("child seat reverse riding", candidates, top_k=2)

    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-c"]
    assert [result.score for result in results] == [0.98, 0.44]
    assert scorer.calls == [
        {
            "pairs": [
                ("child seat reverse riding", "Generic paragraph."),
                ("child seat reverse riding", "Direct child seat evidence."),
                ("child seat reverse riding", "Partial safety context."),
            ],
            "instruction": (
                "Given a web search query, retrieve relevant passages that answer the query"
            ),
            "max_length": 4096,
            "batch_size": 2,
        }
    ]
    assert results[0].metadata["rerank"] == {
        "provider": "qwen",
        "model": "local-qwen-reranker",
        "original_score": 0.02,
    }


def test_qwen_reranker_wraps_invalid_scores() -> None:
    """Require QwenReranker to expose invalid model output as ProviderError."""

    reranker = QwenReranker(
        model="local-qwen-reranker",
        scorer=RecordingQwenScorer([1.2]),
    )

    with pytest.raises(ProviderError, match="Qwen rerank failed") as captured:
        reranker.rerank("query", [_candidate("chunk-a", "A")])

    assert captured.value.context["provider"] == "qwen"


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


def test_cross_encoder_model_cache_reuses_same_model_device_scorer() -> None:
    """Require Cross-Encoder model loading to be process-level cached."""

    loader = CountingCrossEncoderLoader()
    CrossEncoderModelCache.clear()
    CrossEncoderModelCache.configure_loader(loader)
    try:
        reranker_a = CrossEncoderReranker(model="local-cross-encoder", device="cuda")
        reranker_b = CrossEncoderReranker(model="local-cross-encoder", device="cuda")
        reranker_c = CrossEncoderReranker(model="local-cross-encoder", device="cpu")
        candidates = [_candidate("a", "A"), _candidate("b", "B")]

        reranker_a.rerank("query", candidates)
        reranker_b.rerank("query", candidates)
        reranker_c.rerank("query", candidates)

        assert loader.load_calls == [
            ("local-cross-encoder", "cuda"),
            ("local-cross-encoder", "cpu"),
        ]
        assert len(loader.scorers[0].predict_calls) == 2
        assert len(loader.scorers[1].predict_calls) == 1
    finally:
        CrossEncoderModelCache.configure_loader(None)
        CrossEncoderModelCache.clear()


def test_cross_encoder_model_cache_warmup_uses_cached_scorer() -> None:
    """Require MCP startup warmup to load and reuse the cached scorer."""

    loader = CountingCrossEncoderLoader()
    CrossEncoderModelCache.clear()
    CrossEncoderModelCache.configure_loader(loader)
    try:
        CrossEncoderModelCache.warmup("local-cross-encoder", "cuda")
        reranker = CrossEncoderReranker(model="local-cross-encoder", device="cuda")

        reranker.rerank("query", [_candidate("a", "A")])

        assert loader.load_calls == [("local-cross-encoder", "cuda")]
        assert loader.scorers[0].predict_calls[0] == [("warmup query", "warmup document")]
        assert loader.scorers[0].predict_calls[1] == [("query", "A")]
    finally:
        CrossEncoderModelCache.configure_loader(None)
        CrossEncoderModelCache.clear()


def test_rerank_prompt_requires_a_strict_json_object_array() -> None:
    """Prevent chat models from returning ID-only arrays or explanatory prose."""

    reranker = LLMReranker(
        llm_client=RecordingLLM("[]"),
        prompt=load_prompt(RAG_ROOT / "config" / "prompts" / "rerank_prompt.yaml"),
    )
    messages = reranker._build_messages(  # noqa: SLF001 - prompt contract test
        query="quiet office toy",
        candidates=[_candidate("chunk-a", "Silent stress toy.")],
    )
    combined_prompt = "\n".join(message.content for message in messages)

    assert "Return only valid JSON" in combined_prompt
    assert "Do not return Markdown fences" in combined_prompt
    assert '"candidate_id": "<exact candidate_id from Candidate chunks>"' in combined_prompt
    assert '"score": 0.95' in combined_prompt
    assert '"reason": "Directly answers the query."' in combined_prompt


def test_reranker_factory_creates_qwen_with_injected_scorer() -> None:
    """Require Qwen provider registration in the reranker factory."""

    scorer = RecordingQwenScorer([0.8])

    reranker = RerankerFactory.create(
        provider="qwen",
        model="local-qwen-reranker",
        scorer=scorer,
    )
    results = reranker.rerank("query", [_candidate("chunk-a", "A")])

    assert isinstance(reranker, QwenReranker)
    assert [result.chunk_id for result in results] == ["chunk-a"]
    assert results[0].score == 0.8


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


def test_rerank_controller_skips_provider_for_high_confidence_fusion_candidates() -> None:
    """Require high-confidence fused candidates to bypass expensive reranking."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    reranker = RecordingReranker()
    trace = Mock()
    controller = RerankController(settings=settings, reranker=reranker)
    candidates = [
        _candidate(
            "chunk-a",
            "Strong dense and sparse match.",
            score=0.10,
            metadata={
                "fusion": {
                    "dense_rank": 1,
                    "sparse_rank": 1,
                    "sources": ["dense", "sparse"],
                }
            },
        ),
        _candidate("chunk-b", "Weaker second match.", score=0.08),
        _candidate("chunk-c", "Supporting third match.", score=0.07),
    ]

    outcome = controller.rerank_with_outcome(
        "query",
        candidates,
        top_k=2,
        trace_context=trace,
    )

    assert [result.chunk_id for result in outcome.results] == ["chunk-a", "chunk-b"]
    assert outcome.fallback_used is False
    assert outcome.fallback_reason is None
    assert reranker.call_count == 0
    assert trace.record_stage.call_args.kwargs["status"] == "skipped"
    details = trace.record_stage.call_args.kwargs["details"]
    assert details["skipped"] is True
    assert details["skip_reason"] == "high_confidence_fusion"
    assert details["confidence_features"]["dual_route_hits"] == 1


def test_rerank_controller_calls_provider_when_skip_gate_is_not_confident() -> None:
    """Require non-high-confidence candidates to keep the normal rerank path."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    reranker = RecordingReranker()
    controller = RerankController(settings=settings, reranker=reranker)
    candidates = [
        _candidate("chunk-a", "First weak match.", score=0.10),
        _candidate("chunk-b", "Close second weak match.", score=0.099),
        _candidate("chunk-c", "Close third weak match.", score=0.098),
    ]

    outcome = controller.rerank_with_outcome("query", candidates, top_k=2)

    assert reranker.call_count == 1
    assert reranker.received_chunk_ids == ["chunk-a", "chunk-b", "chunk-c"]
    assert [result.chunk_id for result in outcome.results] == ["chunk-c", "chunk-b"]
    assert outcome.fallback_used is False
    assert outcome.fallback_reason is None


def test_rerank_controller_returns_provider_order_and_records_success() -> None:
    """Require successful reranking to preserve provider order and diagnostics."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    trace = Mock()
    controller = RerankController(
        settings=settings,
        reranker=FakeReranker(ordered_chunk_ids=["chunk-b", "chunk-a"]),
    )
    candidates = [
        _candidate("chunk-a", "A", metadata={"rank": 1}),
        _candidate("chunk-b", "B", metadata={"rank": 2}),
        _candidate("chunk-c", "C", metadata={"rank": 3}),
    ]

    results = controller.rerank_or_fallback(
        "query",
        candidates,
        top_k=2,
        trace_context=trace,
    )

    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-a"]
    assert results[0] is not candidates[1]
    assert trace.record_stage.call_args.kwargs["stage"] == "rerank"
    assert trace.record_stage.call_args.kwargs["status"] == "success"
    details = trace.record_stage.call_args.kwargs["details"]
    assert "before_order" not in details
    assert "after_order" not in details
    assert [candidate["chunk_id"] for candidate in details["before_candidates"]] == [
        "chunk-a",
        "chunk-b",
        "chunk-c",
    ]
    assert [candidate["chunk_id"] for candidate in details["after_candidates"]] == [
        "chunk-b",
        "chunk-a",
    ]


def test_rerank_controller_outcome_reports_success_without_metadata_heuristics() -> None:
    """Return explicit success state even when a valid reranker adds no metadata."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    controller = RerankController(
        settings=settings,
        reranker=FakeReranker(ordered_chunk_ids=["chunk-b", "chunk-a"]),
    )
    candidates = [
        _candidate("chunk-a", "A"),
        _candidate("chunk-b", "B"),
    ]

    outcome = controller.rerank_with_outcome("query", candidates, top_k=2)

    assert isinstance(outcome, RerankOutcome)
    assert [result.chunk_id for result in outcome.results] == ["chunk-b", "chunk-a"]
    assert outcome.fallback_used is False
    assert outcome.fallback_reason is None
    assert all("rerank" not in result.metadata for result in outcome.results)


def test_rerank_controller_falls_back_when_reranker_is_unavailable() -> None:
    """Require a missing reranker to preserve filtered RRF order and top_k."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    trace = Mock()
    controller = RerankController(settings=settings, reranker=None)
    candidates = [
        _candidate("chunk-b", "B", score=0.8),
        _candidate("chunk-a", "A", score=0.7),
    ]

    results = controller.rerank_or_fallback(
        "query",
        candidates,
        top_k=1,
        trace_context=trace,
    )

    assert [result.chunk_id for result in results] == ["chunk-b"]
    assert results[0] is not candidates[0]
    assert trace.record_stage.call_args.kwargs["status"] == "degraded"
    assert (
        trace.record_stage.call_args.kwargs["details"]["fallback_reason"] == "reranker_unavailable"
    )


def test_rerank_controller_outcome_reports_fallback_reason() -> None:
    """Expose fallback state directly to query orchestration callers."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    controller = RerankController(settings=settings, reranker=None)
    candidates = [
        _candidate("chunk-a", "A"),
        _candidate("chunk-b", "B"),
    ]

    outcome = controller.rerank_with_outcome("query", candidates, top_k=1)

    assert [result.chunk_id for result in outcome.results] == ["chunk-a"]
    assert outcome.fallback_used is True
    assert outcome.fallback_reason == "reranker_unavailable"


def test_rerank_controller_uses_configured_top_k_when_override_is_absent() -> None:
    """Require the controller to apply settings.rerank.top_k by default."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    controller = RerankController(
        settings=settings,
        reranker=FakeReranker(),
    )
    candidates = [
        _candidate(f"chunk-{index}", f"Candidate {index}")
        for index in range(settings.rerank.top_k + 2)
    ]

    results = controller.rerank_or_fallback("query", candidates)

    assert len(results) == settings.rerank.top_k
    assert [result.chunk_id for result in results] == [
        candidate.chunk_id for candidate in candidates[: settings.rerank.top_k]
    ]


@pytest.mark.parametrize(
    "error,expected_reason",
    [
        (TimeoutError("provider timeout"), "reranker_timeout"),
        (
            ProviderError(
                "provider call failed",
                cause=TimeoutError("wrapped provider timeout"),
            ),
            "reranker_timeout",
        ),
        (ProviderError("provider unavailable"), "reranker_error"),
        (RuntimeError("unexpected provider failure"), "reranker_error"),
    ],
)
def test_rerank_controller_falls_back_on_timeout_or_provider_exception(
    error: Exception,
    expected_reason: str,
) -> None:
    """Require provider failures to preserve only the filtered candidate list."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    trace = Mock()
    reranker = RaisingReranker(error, mutate_input=True)
    controller = RerankController(settings=settings, reranker=reranker)
    candidates = [
        _candidate("chunk-a", "A", metadata={"filtered": True}),
        _candidate("chunk-b", "B"),
    ]

    results = controller.rerank_or_fallback(
        "query",
        candidates,
        trace_context=trace,
    )

    assert reranker.received_chunk_ids == ["chunk-a", "chunk-b"]
    assert [result.chunk_id for result in results] == ["chunk-a", "chunk-b"]
    assert "corrupted" not in results[0].metadata
    assert "corrupted" not in candidates[0].metadata
    details = trace.record_stage.call_args.kwargs["details"]
    assert details["fallback_reason"] == expected_reason
    assert details["error_type"] == type(error).__name__
    assert "error_message" not in details


def test_rerank_controller_rejects_foreign_results_by_falling_back() -> None:
    """Require invalid reranker output to never reintroduce filtered candidates."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    trace = Mock()
    controller = RerankController(
        settings=settings,
        reranker=ForeignCandidateReranker(),
    )
    candidates = [_candidate("allowed", "Allowed filtered candidate.")]

    results = controller.rerank_or_fallback(
        "query",
        candidates,
        trace_context=trace,
    )

    assert [result.chunk_id for result in results] == ["allowed"]
    assert trace.record_stage.call_args.kwargs["status"] == "degraded"
    assert (
        trace.record_stage.call_args.kwargs["details"]["fallback_reason"]
        == "invalid_reranker_output"
    )


def test_rerank_controller_rejects_candidate_loss_by_falling_back() -> None:
    """Require rerankers to reorder candidates instead of silently filtering."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    trace = Mock()
    controller = RerankController(
        settings=settings,
        reranker=EmptyResultReranker(),
    )
    candidates = [
        _candidate("chunk-a", "A"),
        _candidate("chunk-b", "B"),
    ]

    results = controller.rerank_or_fallback(
        "query",
        candidates,
        trace_context=trace,
    )

    assert [result.chunk_id for result in results] == ["chunk-a", "chunk-b"]
    assert trace.record_stage.call_args.kwargs["status"] == "degraded"
    assert (
        trace.record_stage.call_args.kwargs["details"]["fallback_reason"]
        == "invalid_reranker_output"
    )


def test_rerank_controller_rejects_duplicate_provider_results_by_falling_back() -> None:
    """Prevent rerank providers from duplicating filtered candidates."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    trace = Mock()
    controller = RerankController(
        settings=settings,
        reranker=DuplicateResultReranker(),
    )
    candidates = [
        _candidate("chunk-a", "A"),
        _candidate("chunk-b", "B"),
    ]

    results = controller.rerank_or_fallback(
        "query",
        candidates,
        trace_context=trace,
    )

    assert [result.chunk_id for result in results] == ["chunk-a", "chunk-b"]
    assert trace.record_stage.call_args.kwargs["status"] == "degraded"
    assert (
        trace.record_stage.call_args.kwargs["details"]["fallback_reason"]
        == "invalid_reranker_output"
    )


def test_rerank_controller_records_empty_candidates_as_skipped() -> None:
    """Short-circuit empty filtered input without invoking the provider."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    reranker = Mock(spec=BaseReranker)
    trace = Mock()
    controller = RerankController(settings=settings, reranker=reranker)

    assert (
        controller.rerank_or_fallback(
            "query",
            [],
            trace_context=trace,
        )
        == []
    )

    reranker.rerank.assert_not_called()
    assert trace.record_stage.call_args.kwargs["status"] == "skipped"
    assert trace.record_stage.call_args.kwargs["candidate_count"] == 0


def test_rerank_controller_validates_inputs_before_fallback_or_provider_calls() -> None:
    """Require invalid query limits to fail instead of being hidden by fallback."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    reranker = RaisingReranker(RuntimeError("must not be called"))
    controller = RerankController(settings=settings, reranker=reranker)

    with pytest.raises(ValueError, match="Rerank query must not be blank"):
        controller.rerank_or_fallback(" ", [_candidate("chunk-a", "A")])
    with pytest.raises(ValueError, match="top_k must be greater than zero"):
        controller.rerank_or_fallback(
            "query",
            [_candidate("chunk-a", "A")],
            top_k=0,
        )

    assert reranker.received_chunk_ids == []


def test_rerank_controller_ignores_trace_sink_failures() -> None:
    """Require diagnostic trace failures not to replace rerank results."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    trace = Mock()
    trace.record_stage.side_effect = RuntimeError("trace unavailable")
    controller = RerankController(
        settings=settings,
        reranker=FakeReranker(ordered_chunk_ids=["chunk-b"]),
    )

    results = controller.rerank_or_fallback(
        "query",
        [_candidate("chunk-a", "A"), _candidate("chunk-b", "B")],
        trace_context=trace,
    )

    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-a"]
