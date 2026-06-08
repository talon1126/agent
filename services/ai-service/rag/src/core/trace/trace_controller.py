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
