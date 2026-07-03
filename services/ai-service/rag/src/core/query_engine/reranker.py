"""Orchestrate retrieval reranking with filtered-RRF fallback semantics.

``RerankController`` is the online-query boundary between metadata filtering
and response construction. It invokes one provider-independent
``BaseReranker`` when available, validates that the provider did not introduce
unknown candidates, and falls back to the exact filtered input order when the
provider is missing, times out, raises, or returns an invalid result.

The controller does not create providers, perform metadata filtering, or build
citations. Those responsibilities remain in factories, ``HybridSearch``, and
the response layer respectively.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from src.core.config import RagSettings, RerankSkipGateSettings
from src.core.errors import RagError
from src.core.query_engine.trace_snapshots import candidate_snapshots
from src.core.types import RetrievalResult
from src.libs.reranker.base_reranker import BaseReranker


class RerankTraceContext(Protocol):
    """Describe the minimal trace method used by rerank orchestration."""

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
        """Record one completed rerank stage.

        Args:
            stage: Stable query stage name.
            method: Trace-visible orchestration method.
            provider: Concrete reranker class or ``none``.
            duration_ms: End-to-end rerank or fallback duration.
            candidate_count: Number of returned candidates.
            status: ``success``, ``degraded``, or ``skipped``.
            details: Trace-safe rank order, limits, and fallback diagnostics.

        Returns:
            Trace implementations may return any value; the controller ignores
            it.
        """


@dataclass(frozen=True, slots=True)
class RerankOutcome:
    """Describe rerank results and explicit degradation state.

    ``RetrievalResult.metadata`` is provider-owned evidence data, not a reliable
    control-plane signal. This outcome object lets orchestration code determine
    whether fallback occurred without inspecting provider-specific metadata.

    Attributes:
        results: Defensive final candidates returned by rerank or fallback.
        fallback_used: Whether the controller preserved filtered RRF order due
            to unavailable, failed, or invalid reranking.
        fallback_reason: Stable reason code such as ``reranker_unavailable``.
            ``None`` means reranking succeeded or the input was empty.
    """

    results: list[RetrievalResult]
    fallback_used: bool
    fallback_reason: str | None


@dataclass(frozen=True, slots=True)
class RerankSkipDecision:
    """Describe whether filtered fusion candidates can bypass reranking."""

    should_skip: bool
    reason: str
    confidence_features: dict[str, Any]


class RerankSkipGate:
    """Decide whether filtered RRF candidates are already high-confidence."""

    def __init__(self, settings: RerankSkipGateSettings) -> None:
        """Configure the gate from validated rerank settings.

        Args:
            settings: High-confidence thresholds and optional consistency
                requirements loaded from ``settings.rerank.skip_gate``.
        """

        self._settings = settings

    def evaluate(self, candidates: Sequence[RetrievalResult]) -> RerankSkipDecision:
        """Return a binary skip decision for the whole candidate batch.

        Args:
            candidates: Filtered RRF candidates in fusion rank order.

        Returns:
            Decision containing a stable reason and trace-safe feature summary.
        """

        observed = list(candidates[: self._settings.max_candidates_for_skip])
        features = self._confidence_features(observed, total_count=len(candidates))
        if not self._settings.enabled:
            return RerankSkipDecision(False, "skip_gate_disabled", features)
        if len(candidates) < self._settings.min_candidates:
            return RerankSkipDecision(False, "insufficient_candidates", features)
        if features["dual_route_hits"] < self._settings.min_dual_route_hits:
            return RerankSkipDecision(False, "insufficient_dual_route_hits", features)
        if features["rrf_margin_ratio"] < self._settings.min_rrf_margin_ratio:
            return RerankSkipDecision(False, "insufficient_rrf_margin", features)
        if self._settings.require_document_consistency and not features["document_consistent"]:
            return RerankSkipDecision(False, "document_inconsistent", features)
        if self._settings.require_section_consistency and not features["section_consistent"]:
            return RerankSkipDecision(False, "section_inconsistent", features)
        return RerankSkipDecision(True, "high_confidence_fusion", features)

    @staticmethod
    def _confidence_features(
        candidates: Sequence[RetrievalResult],
        *,
        total_count: int,
    ) -> dict[str, Any]:
        """Build trace-safe confidence features from fused candidates."""

        top_score = candidates[0].score if candidates else 0.0
        second_score = candidates[1].score if len(candidates) > 1 else 0.0
        margin_ratio = (
            (top_score - second_score) / top_score if top_score > 0 and len(candidates) > 1 else 0.0
        )
        document_ids = [
            candidate.metadata.get("document_id")
            for candidate in candidates
            if candidate.metadata.get("document_id") is not None
        ]
        section_paths = [
            tuple(candidate.metadata.get("section_path") or ())
            for candidate in candidates
            if candidate.metadata.get("section_path")
        ]
        return {
            "candidate_count": total_count,
            "observed_count": len(candidates),
            "top_score": top_score,
            "second_score": second_score,
            "rrf_margin_ratio": margin_ratio,
            "dual_route_hits": sum(1 for candidate in candidates if _is_dual_route_hit(candidate)),
            "document_consistent": bool(document_ids) and len(set(document_ids)) == 1,
            "section_consistent": bool(section_paths) and len(set(section_paths)) == 1,
        }


class RerankController:
    """Execute reranking while preserving filtered candidates as fallback."""

    def __init__(
        self,
        *,
        settings: RagSettings,
        reranker: BaseReranker | None,
    ) -> None:
        """Configure rerank limits and the optional provider implementation.

        Args:
            settings: Validated settings providing ``rerank.top_k``.
            reranker: Provider-independent reranker. ``None`` represents an
                unavailable or intentionally unconfigured reranker.
        """

        self._settings = settings
        self._reranker = reranker
        self._skip_gate = RerankSkipGate(settings.rerank.skip_gate)

    def rerank_or_fallback(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
        trace_context: RerankTraceContext | None = None,
    ) -> list[RetrievalResult]:
        """Return only final rerank results for legacy callers.

        Args:
            query: Original or rewritten user query.
            candidates: Candidates that already passed metadata filtering.
            top_k: Optional positive result limit. When omitted,
                ``settings.rerank.top_k`` is used.
            trace_context: Optional low-intrusion trace recorder.

        Returns:
            Defensive candidate copies in provider order on success, or in the
            original filtered RRF order when fallback is required.
        """

        return self.rerank_with_outcome(
            query,
            candidates,
            top_k=top_k,
            trace_context=trace_context,
        ).results

    def rerank_with_outcome(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
        trace_context: RerankTraceContext | None = None,
    ) -> RerankOutcome:
        """Rerank filtered candidates and return explicit fallback metadata.

        Args:
            query: Original or rewritten user query.
            candidates: Candidates that already passed metadata filtering.
            top_k: Optional positive result limit. When omitted,
                ``settings.rerank.top_k`` is used.
            trace_context: Optional low-intrusion trace recorder.

        Returns:
            ``RerankOutcome`` with final candidates plus degradation state.
            This avoids inferring fallback from provider-specific result
            metadata, which may be absent for valid reranker implementations.

        Raises:
            ValueError: If query is blank or ``top_k`` is not a positive
                integer.

        Side Effects:
            Calls the configured reranker with deep candidate copies. Provider
            failures are intentionally converted into fallback results. When a
            trace context exists, records one rerank stage.

        Notes:
            Timeout enforcement belongs to provider adapters or their transport
            clients. This boundary recognizes direct and provider-wrapped
            timeout errors and applies the stable ``reranker_timeout`` fallback
            reason.
        """

        if not query.strip():
            raise ValueError("Rerank query must not be blank")
        candidate_limit = self._settings.rerank.top_k if top_k is None else top_k
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int):
            raise ValueError("top_k must be an integer")
        if candidate_limit <= 0:
            raise ValueError("top_k must be greater than zero")

        started_at = perf_counter()
        provider = type(self._reranker).__name__ if self._reranker is not None else "none"
        fallback_candidates = [candidate.model_copy(deep=True) for candidate in candidates]
        before_order = [candidate.chunk_id for candidate in fallback_candidates]

        if not fallback_candidates:
            self._record_trace(
                trace_context,
                started_at=started_at,
                provider=provider,
                status="skipped",
                results=[],
                before_candidates=[],
                top_k=candidate_limit,
                fallback_reason=None,
                error=None,
            )
            return RerankOutcome(
                results=[],
                fallback_used=False,
                fallback_reason=None,
            )

        skip_decision = self._skip_gate.evaluate(fallback_candidates)
        if skip_decision.should_skip:
            results = [
                candidate.model_copy(deep=True)
                for candidate in fallback_candidates[:candidate_limit]
            ]
            self._record_trace(
                trace_context,
                started_at=started_at,
                provider=provider,
                status="skipped",
                results=results,
                before_candidates=fallback_candidates,
                top_k=candidate_limit,
                fallback_reason=None,
                error=None,
                skip_decision=skip_decision,
            )
            return RerankOutcome(
                results=results,
                fallback_used=False,
                fallback_reason=None,
            )

        if self._reranker is None:
            return self._fallback(
                trace_context=trace_context,
                started_at=started_at,
                provider=provider,
                candidates=fallback_candidates,
                top_k=candidate_limit,
                fallback_reason="reranker_unavailable",
                error=None,
            )

        provider_candidates = [candidate.model_copy(deep=True) for candidate in fallback_candidates]
        try:
            provider_results = self._reranker.rerank(
                query,
                provider_candidates,
                top_k=candidate_limit,
            )
        except Exception as error:
            return self._fallback(
                trace_context=trace_context,
                started_at=started_at,
                provider=provider,
                candidates=fallback_candidates,
                top_k=candidate_limit,
                fallback_reason=(
                    "reranker_timeout" if self._is_timeout_error(error) else "reranker_error"
                ),
                error=error,
            )

        try:
            results = self._validate_provider_results(
                provider_results,
                allowed_chunk_ids=set(before_order),
                expected_count=min(len(fallback_candidates), candidate_limit),
            )
        except Exception as error:
            return self._fallback(
                trace_context=trace_context,
                started_at=started_at,
                provider=provider,
                candidates=fallback_candidates,
                top_k=candidate_limit,
                fallback_reason="invalid_reranker_output",
                error=error,
            )

        self._record_trace(
            trace_context,
            started_at=started_at,
            provider=provider,
            status="success",
            results=results,
            before_candidates=fallback_candidates,
            top_k=candidate_limit,
            fallback_reason=None,
            error=None,
        )
        return RerankOutcome(
            results=results,
            fallback_used=False,
            fallback_reason=None,
        )

    @staticmethod
    def _validate_provider_results(
        results: Sequence[RetrievalResult],
        *,
        allowed_chunk_ids: set[str],
        expected_count: int,
    ) -> list[RetrievalResult]:
        """Validate provider output against the filtered candidate boundary.

        Args:
            results: Candidate sequence returned by the configured reranker.
            allowed_chunk_ids: IDs present after metadata filtering.
            expected_count: Number of candidates a reorder-only provider must
                return after applying ``top_k``.

        Returns:
            Deep validated copies in provider order.

        Raises:
            ValueError: If output count is incorrect, repeats an ID, or
                introduces a candidate absent from the filtered input.
            pydantic.ValidationError: If an output item violates
                ``RetrievalResult``.
        """

        validated: list[RetrievalResult] = []
        seen: set[str] = set()
        for result in results:
            candidate = RetrievalResult.model_validate(result).model_copy(deep=True)
            if candidate.chunk_id not in allowed_chunk_ids:
                raise ValueError("Reranker output contains a candidate outside the filtered input")
            if candidate.chunk_id in seen:
                raise ValueError("Reranker output contains duplicate candidate IDs")
            seen.add(candidate.chunk_id)
            validated.append(candidate)
        if len(validated) != expected_count:
            raise ValueError("Reranker output count must match the filtered candidate limit")
        return validated

    @staticmethod
    def _is_timeout_error(error: Exception) -> bool:
        """Detect direct or provider-wrapped timeout failures.

        Args:
            error: Exception raised by a reranker or nested provider client.

        Returns:
            ``True`` when the exception itself or a linked cause is a built-in
            timeout or has a provider-specific timeout class name.

        Notes:
            OpenAI-compatible clients commonly wrap SDK timeout exceptions in
            ``ProviderError``. Traversing ``cause``, ``__cause__``, and
            ``__context__`` preserves timeout-specific diagnostics without
            importing a concrete SDK into the core query layer.
        """

        pending: list[BaseException] = [error]
        visited: set[int] = set()
        while pending:
            current = pending.pop()
            identity = id(current)
            if identity in visited:
                continue
            visited.add(identity)

            if isinstance(current, TimeoutError):
                return True
            if "timeout" in type(current).__name__.lower():
                return True

            for attribute in ("cause", "__cause__", "__context__"):
                nested = getattr(current, attribute, None)
                if isinstance(nested, BaseException):
                    pending.append(nested)
        return False

    def _fallback(
        self,
        *,
        trace_context: RerankTraceContext | None,
        started_at: float,
        provider: str,
        candidates: list[RetrievalResult],
        top_k: int,
        fallback_reason: str,
        error: Exception | None,
    ) -> RerankOutcome:
        """Return filtered RRF candidates and record degraded execution.

        Args:
            trace_context: Optional trace recorder.
            started_at: ``perf_counter`` value captured before reranking.
            provider: Reranker implementation name or ``none``.
            candidates: Pristine defensive copies captured before provider use.
            top_k: Positive output limit.
            fallback_reason: Stable machine-readable degradation code.
            error: Optional provider or validation exception.

        Returns:
            Outcome containing filtered candidates in unchanged RRF order,
            limited by ``top_k``, and the explicit fallback reason.
        """

        results = [candidate.model_copy(deep=True) for candidate in candidates[:top_k]]
        self._record_trace(
            trace_context,
            started_at=started_at,
            provider=provider,
            status="degraded",
            results=results,
            before_candidates=candidates,
            top_k=top_k,
            fallback_reason=fallback_reason,
            error=error,
        )
        return RerankOutcome(
            results=results,
            fallback_used=True,
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def _record_trace(
        trace_context: RerankTraceContext | None,
        *,
        started_at: float,
        provider: str,
        status: str,
        results: Sequence[RetrievalResult],
        before_candidates: Sequence[RetrievalResult],
        top_k: int,
        fallback_reason: str | None,
        error: Exception | None,
        skip_decision: RerankSkipDecision | None = None,
    ) -> None:
        """Write one best-effort rerank trace stage.

        Args:
            trace_context: Optional TraceContext-compatible recorder.
            started_at: ``perf_counter`` value captured before rerank work.
            provider: Concrete reranker implementation name or ``none``.
            status: ``success``, ``degraded``, or ``skipped``.
            results: Final candidates returned by the controller.
            before_candidates: Filtered candidates before reranking.
            top_k: Applied output limit.
            fallback_reason: Stable degradation code, when fallback occurred.
            error: Optional failure used only for trace-safe type and structured
                project-error context diagnostics.
        """

        if trace_context is None:
            return
        details: dict[str, Any] = {
            "top_k": top_k,
            "before_candidates": candidate_snapshots(before_candidates),
            "after_candidates": candidate_snapshots(results),
            "fallback_used": fallback_reason is not None,
            "fallback_reason": fallback_reason,
            "skipped": skip_decision.should_skip if skip_decision else False,
            "skip_reason": skip_decision.reason if skip_decision else None,
            "confidence_features": (
                dict(skip_decision.confidence_features) if skip_decision else {}
            ),
        }
        if error is not None:
            details["error_type"] = type(error).__name__
            if isinstance(error, RagError) and error.context:
                details["error_context"] = dict(error.context)
        try:
            trace_context.record_stage(
                stage="rerank",
                method="rerank_or_fallback",
                provider=provider,
                duration_ms=(perf_counter() - started_at) * 1000,
                candidate_count=len(results),
                status=status,
                details=details,
            )
        except Exception:
            # Trace output is diagnostic only. A broken trace sink must never
            # replace reranked or fallback query results.
            return


def _is_dual_route_hit(candidate: RetrievalResult) -> bool:
    """Return whether a fused candidate was found by Dense and Sparse routes."""

    fusion = candidate.metadata.get("fusion")
    if not isinstance(fusion, dict):
        return False
    sources = fusion.get("sources")
    if isinstance(sources, (list, tuple, set, frozenset)):
        return "dense" in sources and "sparse" in sources
    return fusion.get("dense_rank") is not None and fusion.get("sparse_rank") is not None
