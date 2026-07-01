"""Gate reranked evidence with lightweight Self-RAG checks.

``SelfRagController`` runs after reranking and before response construction. It
uses rerank scores for cheap pass/empty decisions and calls one LLM judge only
for medium-confidence evidence. The judge evaluates context relevance and
evidence sufficiency together so query orchestration can avoid unsupported
answers without adding a second model round trip.

The controller does not retrieve, rerank, build citations, or call web search.
The only first-release fallback is an empty result set with trace-visible reason
codes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, Protocol

from src.core.config import PromptTemplate, RagSettings, load_prompt
from src.core.query_engine.trace_snapshots import candidate_snapshots
from src.core.types import RetrievalResult
from src.libs.llm import BaseLLM, ChatMessage

SelfRagDecisionName = Literal["accepted", "empty"]
SelfRagScoreBand = Literal["high_confidence", "medium_confidence", "low_confidence"]


class SelfRagTraceContext(Protocol):
    """Describe the minimal trace method used by Self-RAG orchestration."""

    def record_stage(
        self,
        *,
        stage: str,
        method: str,
        provider: str,
        duration_ms: float,
        candidate_count: int,
        status: str,
        details: dict[str, Any],
    ) -> Any:
        """Record one completed Self-RAG stage.

        Args:
            stage: Stable query stage name.
            method: Trace-visible gating method.
            provider: Controller or judge provider name.
            duration_ms: End-to-end gating duration.
            candidate_count: Number of candidates allowed into response
                construction.
            status: ``success``, ``degraded``, or ``skipped``.
            details: Trace-safe score band, judge, fallback, and chunk IDs.

        Returns:
            Trace implementations may return any value; the controller ignores
            it.
        """


@dataclass(frozen=True, slots=True)
class SelfRagJudgeResult:
    """Represent the one-call LLM judgement for medium-confidence evidence.

    Attributes:
        relevant: Whether the retained candidates are topically relevant to the
            user query.
        relevance_score: Numeric relevance score in the inclusive ``0..1``
            range, as returned by the judge.
        sufficient: Whether the retained candidates contain enough evidence to
            answer without guessing.
        evidence_sufficiency_score: Numeric sufficiency score in the inclusive
            ``0..1`` range.
        missing_evidence: Optional missing facts or source types that explain a
            sufficiency failure.
        reason: Short trace-safe explanation from the judge.
    """

    relevant: bool
    relevance_score: float
    sufficient: bool
    evidence_sufficiency_score: float
    missing_evidence: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SelfRagDecision:
    """Describe final Self-RAG gating output for query orchestration.

    Attributes:
        decision: ``accepted`` when candidates may enter response construction,
            or ``empty`` when the configured empty fallback must run.
        score_band: Score segment used before optional judging.
        selected_results: Defensive retrieval result copies allowed into the
            response layer. Empty when ``decision`` is ``empty``.
        fallback_action: ``empty`` for fallback decisions, otherwise ``None``.
        judge_result: Parsed LLM judgement when a judge call completed.
        reason: Stable reason code such as ``high_confidence`` or
            ``judge_rejected``.
    """

    decision: SelfRagDecisionName
    score_band: SelfRagScoreBand
    selected_results: list[RetrievalResult]
    fallback_action: str | None
    judge_result: SelfRagJudgeResult | None
    reason: str


class SelfRagController:
    """Apply score gates and optional one-call judging to reranked candidates."""

    def __init__(
        self,
        *,
        settings: RagSettings,
        llm_client: BaseLLM | None,
        prompt: PromptTemplate | None = None,
    ) -> None:
        """Configure thresholds, judge client, and Prompt template.

        Args:
            settings: Validated runtime settings containing ``self_rag``.
            llm_client: Optional LLM used only for medium-confidence judging.
                ``None`` makes medium-confidence evidence fall back to empty.
            prompt: Optional preloaded Prompt used by tests. Production loads
                ``settings.self_rag.judge_prompt_path``.
        """

        self._settings = settings.self_rag
        self._llm_client = llm_client
        self._prompt = prompt or load_prompt(self._settings.judge_prompt_path)

    def evaluate(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        trace_context: SelfRagTraceContext | None = None,
    ) -> SelfRagDecision:
        """Return candidates allowed into response construction.

        Args:
            query: Normalized user query used for relevance judgement.
            candidates: Reranked candidates. The method never mutates these
                objects and returns deep copies.
            trace_context: Optional low-intrusion trace recorder.

        Returns:
            A ``SelfRagDecision`` describing selected results, score band,
            fallback action, and judge diagnostics.

        Raises:
            ValueError: If the query is blank.
        """

        if not query.strip():
            raise ValueError("Self-RAG query must not be blank")
        started_at = perf_counter()
        candidate_copies = self._sort_candidates_by_score(candidates)
        score_band = self._classify_score_band(candidate_copies)
        judge_called = False
        trimmed_count = 0
        judge_error: str | None = None

        if not candidate_copies:
            decision = self._empty_decision(score_band, reason="no_candidates")
        elif score_band == "high_confidence":
            decision = SelfRagDecision(
                decision="accepted",
                score_band=score_band,
                selected_results=[
                    candidate.model_copy(deep=True) for candidate in candidate_copies
                ],
                fallback_action=None,
                judge_result=None,
                reason="high_confidence",
            )
        elif score_band == "low_confidence":
            decision = self._empty_decision(score_band, reason="low_confidence")
        else:
            retained = self._trim_low_score_candidates(candidate_copies)
            trimmed_count = len(candidate_copies) - len(retained)
            if not retained:
                decision = self._empty_decision(score_band, reason="no_candidates_after_trim")
            elif self._llm_client is None:
                decision = self._empty_decision(score_band, reason="judge_unavailable")
            else:
                judge_called = True
                try:
                    judge_result = self._judge_relevance_and_sufficiency(query, retained)
                except Exception as error:
                    judge_error = type(error).__name__
                    decision = self._empty_decision(score_band, reason="invalid_judge_output")
                else:
                    relevance_passed = (
                        judge_result.relevant
                        and judge_result.relevance_score >= self._settings.relevance_threshold
                    )
                    sufficiency_passed = (
                        judge_result.sufficient
                        and judge_result.evidence_sufficiency_score
                        >= self._settings.evidence_sufficiency_threshold
                    )
                    if relevance_passed and sufficiency_passed:
                        decision = SelfRagDecision(
                            decision="accepted",
                            score_band=score_band,
                            selected_results=[
                                candidate.model_copy(deep=True) for candidate in retained
                            ],
                            fallback_action=None,
                            judge_result=judge_result,
                            reason="judge_passed",
                        )
                    else:
                        decision = SelfRagDecision(
                            decision="empty",
                            score_band=score_band,
                            selected_results=[],
                            fallback_action=self._settings.fallback_action,
                            judge_result=judge_result,
                            reason="judge_rejected",
                        )

        self._record_trace(
            trace_context,
            started_at=started_at,
            input_candidates=candidate_copies,
            decision=decision,
            judge_called=judge_called,
            trimmed_count=trimmed_count,
            judge_error=judge_error,
        )
        return decision

    @staticmethod
    def _sort_candidates_by_score(
        candidates: Sequence[RetrievalResult],
    ) -> list[RetrievalResult]:
        """Return defensive copies ordered by descending relevance score."""

        indexed_candidates = [
            (index, candidate.model_copy(deep=True))
            for index, candidate in enumerate(candidates)
        ]
        indexed_candidates.sort(key=lambda item: (-item[1].score, item[0]))
        return [candidate for _, candidate in indexed_candidates]
    def _classify_score_band(self, candidates: Sequence[RetrievalResult]) -> SelfRagScoreBand:
        """Classify rerank score confidence before any LLM judging."""

        if not candidates:
            return "low_confidence"
        top_candidates = list(candidates[: self._settings.high_confidence_top_n])
        if len(top_candidates) >= self._settings.high_confidence_top_n and all(
            candidate.score >= self._settings.high_confidence_min_score
            for candidate in top_candidates
        ):
            return "high_confidence"
        if candidates[0].score >= self._settings.medium_confidence_min_top_score:
            return "medium_confidence"
        return "low_confidence"

    def _trim_low_score_candidates(
        self,
        candidates: Sequence[RetrievalResult],
    ) -> list[RetrievalResult]:
        """Drop very weak candidates before constructing the judge Prompt."""

        return [
            candidate.model_copy(deep=True)
            for candidate in candidates
            if candidate.score >= self._settings.judge_min_candidate_score
        ]

    def _judge_relevance_and_sufficiency(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
    ) -> SelfRagJudgeResult:
        """Call the judge once and parse its strict JSON object response."""

        if self._llm_client is None:
            raise RuntimeError("Self-RAG judge LLM is unavailable")
        candidate_payload = [
            {
                "candidate_id": candidate.chunk_id,
                "rank": rank,
                "score": candidate.score,
                "text": candidate.text,
            }
            for rank, candidate in enumerate(candidates, start=1)
        ]
        messages = [
            ChatMessage(role="system", content=self._prompt.system_prompt),
            ChatMessage(
                role="user",
                content=self._prompt.user_prompt.format(
                    query=query,
                    candidates=json.dumps(candidate_payload, ensure_ascii=False),
                ),
            ),
        ]
        response = self._llm_client.chat(messages)
        payload = json.loads(response.content)
        if not isinstance(payload, dict):
            raise ValueError("Self-RAG judge output must be a JSON object")
        return self._parse_judge_result(payload)

    def _empty_decision(
        self,
        score_band: SelfRagScoreBand,
        *,
        reason: str,
    ) -> SelfRagDecision:
        """Build the configured empty fallback decision."""

        return SelfRagDecision(
            decision="empty",
            score_band=score_band,
            selected_results=[],
            fallback_action=self._settings.fallback_action,
            judge_result=None,
            reason=reason,
        )

    @staticmethod
    def _parse_judge_result(payload: dict[str, Any]) -> SelfRagJudgeResult:
        """Validate and normalize the strict judge JSON object."""

        required_fields = {
            "relevant",
            "relevance_score",
            "sufficient",
            "evidence_sufficiency_score",
            "missing_evidence",
            "reason",
        }
        missing_fields = required_fields - set(payload)
        if missing_fields:
            raise ValueError(f"Self-RAG judge output is missing fields: {sorted(missing_fields)}")
        relevant = payload["relevant"]
        sufficient = payload["sufficient"]
        if not isinstance(relevant, bool) or not isinstance(sufficient, bool):
            raise ValueError("Self-RAG judge boolean fields must be true or false")
        missing_evidence = payload["missing_evidence"]
        if not isinstance(missing_evidence, list) or not all(
            isinstance(item, str) for item in missing_evidence
        ):
            raise ValueError("missing_evidence must be a list of strings")
        reason = payload["reason"]
        if not isinstance(reason, str):
            raise ValueError("Self-RAG judge reason must be a string")
        relevance_score = float(payload["relevance_score"])
        sufficiency_score = float(payload["evidence_sufficiency_score"])
        if not 0 <= relevance_score <= 1 or not 0 <= sufficiency_score <= 1:
            raise ValueError("Self-RAG judge scores must be between 0 and 1")
        return SelfRagJudgeResult(
            relevant=relevant,
            relevance_score=relevance_score,
            sufficient=sufficient,
            evidence_sufficiency_score=sufficiency_score,
            missing_evidence=tuple(missing_evidence),
            reason=reason,
        )

    def _record_trace(
        self,
        trace_context: SelfRagTraceContext | None,
        *,
        started_at: float,
        input_candidates: Sequence[RetrievalResult],
        decision: SelfRagDecision,
        judge_called: bool,
        trimmed_count: int,
        judge_error: str | None,
    ) -> None:
        """Write one best-effort Self-RAG trace stage."""

        if trace_context is None:
            return
        judge_result = None
        if decision.judge_result is not None:
            judge_result = {
                "relevant": decision.judge_result.relevant,
                "relevance_score": decision.judge_result.relevance_score,
                "sufficient": decision.judge_result.sufficient,
                "evidence_sufficiency_score": decision.judge_result.evidence_sufficiency_score,
                "missing_evidence": list(decision.judge_result.missing_evidence),
                "reason": decision.judge_result.reason,
            }
        details: dict[str, Any] = {
            "score_band": decision.score_band,
            "top_scores": [
                candidate.score
                for candidate in input_candidates[: self._settings.high_confidence_top_n]
            ],
            "trimmed_count": trimmed_count,
            "judge_called": judge_called,
            "judge_result": judge_result,
            "selected_chunk_ids": [candidate.chunk_id for candidate in decision.selected_results],
            "fallback_action": decision.fallback_action,
            "reason": decision.reason,
            "before_candidates": candidate_snapshots(input_candidates),
            "after_candidates": candidate_snapshots(decision.selected_results),
        }
        if judge_error is not None:
            details["judge_error"] = judge_error
        try:
            trace_context.record_stage(
                stage="self_rag",
                method="score_gate_or_llm_judge",
                provider="SelfRagController",
                duration_ms=(perf_counter() - started_at) * 1000,
                candidate_count=len(decision.selected_results),
                status="success" if decision.decision == "accepted" else "degraded",
                details=details,
            )
        except Exception:
            return
