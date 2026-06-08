"""Coordinate stage recording and trace snapshot flushing.

``TraceController`` is a small orchestration wrapper around ``TraceContext``.
Pipeline code may use the context directly, but composition roots can keep a
controller when they need a single object that both records stages and flushes
the finished snapshot to an injected sink.

F1 deliberately keeps the sink abstract. F4 will provide the Python logging and
JSONFormatter implementation that appends these snapshots to ``traces.jsonl``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from src.core.trace.trace_context import TraceContext, TraceStatus

TraceSink = Callable[[dict[str, Any]], None]
TraceClock = Callable[[], datetime]


def _default_clock() -> datetime:
    """Return an aware UTC timestamp for controller completion."""

    return datetime.now(UTC)


class TraceController:
    """Record stages through a ``TraceContext`` and flush one final snapshot.

    Args:
        context: In-flight trace context owned by the pipeline request.
        sink: Optional callable that receives the finished JSON-compatible
            snapshot. Tests use list appenders; F4 will inject a structured log
            writer. ``None`` keeps flush side-effect free.
        clock: Completion timestamp provider for deterministic tests.
    """

    def __init__(
        self,
        context: TraceContext,
        *,
        sink: TraceSink | None = None,
        clock: TraceClock = _default_clock,
    ) -> None:
        """Store trace dependencies without opening files or databases."""

        self._context = context
        self._sink = sink
        self._clock = clock

    @property
    def context(self) -> TraceContext:
        """Return the managed context for pipeline-level trace ID access."""

        return self._context

    def record_stage(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate stage recording to the managed context.

        Args:
            *args: Positional arguments accepted by ``TraceContext.record_stage``.
            **kwargs: Keyword arguments accepted by ``TraceContext.record_stage``.

        Returns:
            The defensive stage dictionary appended to the context.
        """

        return self._context.record_stage(*args, **kwargs)

    def flush(
        self,
        *,
        status: TraceStatus = "success",
        summary_metrics: dict[str, Any] | None = None,
        evaluation_metrics: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Finish the trace and pass its snapshot to the configured sink.

        Args:
            status: Final trace status.
            summary_metrics: End-to-end summary metrics merged by the context.
            evaluation_metrics: Quality metrics merged by the context.
            error: Optional trace-level structured error.

        Returns:
            The JSON-compatible snapshot sent to the sink.

        Side Effects:
            Calls the sink exactly once when one was provided. Sink exceptions
            are intentionally not swallowed because flushing belongs to the
            observability boundary rather than a best-effort in-stage trace
            hook.
        """

        snapshot = self._context.finish(
            status=status,
            finished_at=self._clock(),
            summary_metrics=summary_metrics,
            evaluation_metrics=evaluation_metrics,
            error=error,
        )
        if self._sink is not None:
            self._sink(snapshot)
        return snapshot

    def flush_ingestion(
        self,
        *,
        status: TraceStatus,
        document_status: str,
        chunk_count: int,
        embedded_count: int,
        skipped_count: int,
        error: dict[str, Any] | None = None,
        chunk_quality_score: float | int | None = None,
        noise_reduction_summary: dict[str, Any] | None = None,
        embedding_coverage: float | int | None = None,
        index_ready: bool | None = None,
    ) -> dict[str, Any]:
        """Finish and write an Ingestion trace using the typed trace contract.

        Args:
            status: Final ingestion trace lifecycle status.
            document_status: Source document status such as ``success`` or
                ``skipped``.
            chunk_count: Number of final chunks produced by the run.
            embedded_count: Number of Dense vectors newly generated.
            skipped_count: Number of document/chunk units skipped by hashes.
            error: Optional trace-level error object.
            chunk_quality_score: Optional aggregate chunk quality score.
            noise_reduction_summary: Optional transform quality evidence.
            embedding_coverage: Optional ratio of indexed chunks.
            index_ready: Optional flag indicating searchable readiness.

        Returns:
            The JSON-compatible snapshot sent to the sink.
        """

        snapshot = self._context.finish_ingestion(
            status=status,
            finished_at=self._clock(),
            document_status=document_status,
            chunk_count=chunk_count,
            embedded_count=embedded_count,
            skipped_count=skipped_count,
            error=error,
            chunk_quality_score=chunk_quality_score,
            noise_reduction_summary=noise_reduction_summary,
            embedding_coverage=embedding_coverage,
            index_ready=index_ready,
        )
        if self._sink is not None:
            self._sink(snapshot)
        return snapshot

    def flush_query(
        self,
        *,
        status: TraceStatus,
        top_k_results: list[dict[str, Any]],
        candidate_count_by_stage: dict[str, int],
        fallback_used: bool,
        error: dict[str, Any] | None = None,
        query_document_relevance: float | int | None = None,
        citation_hit_rate: float | int | None = None,
        rerank_delta: dict[str, Any] | None = None,
        empty_result: bool | None = None,
    ) -> dict[str, Any]:
        """Finish and write a Query trace using the typed trace contract.

        Args:
            status: Final query trace lifecycle status.
            top_k_results: Public-safe final ranked result summaries.
            candidate_count_by_stage: Candidate counts for Dashboard charts.
            fallback_used: Whether retrieval or rerank degraded gracefully.
            error: Optional trace-level error object.
            query_document_relevance: Optional aggregate relevance score.
            citation_hit_rate: Optional citation quality score.
            rerank_delta: Optional before/after rerank movement evidence.
            empty_result: Optional flag indicating no final evidence.

        Returns:
            The JSON-compatible snapshot sent to the sink.
        """

        snapshot = self._context.finish_query(
            status=status,
            finished_at=self._clock(),
            top_k_results=top_k_results,
            candidate_count_by_stage=candidate_count_by_stage,
            fallback_used=fallback_used,
            error=error,
            query_document_relevance=query_document_relevance,
            citation_hit_rate=citation_hit_rate,
            rerank_delta=rerank_delta,
            empty_result=empty_result,
        )
        if self._sink is not None:
            self._sink(snapshot)
        return snapshot
