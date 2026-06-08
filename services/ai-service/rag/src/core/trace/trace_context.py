"""Build one structured Query or Ingestion trace snapshot in memory.

``TraceContext`` is the low-intrusion object passed through ingestion/query
pipelines. Business stages only call ``record_stage()`` with trace-safe
summaries; they do not know where traces will be persisted. Later Phase F tasks
can serialize the same snapshot to JSON Lines, PostgreSQL, or Dashboard
services without changing retrieval and ingestion business code.

This module does not configure Python logging, open files, write PostgreSQL
rows, inspect provider SDK responses, or decide Dashboard rendering. It owns
only validation, defensive copying, timestamp normalization, and the stable
four-section trace structure: basic information, stage details, summary
metrics, and evaluation metrics.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

TraceType = Literal["query", "ingestion"]
TraceStatus = Literal["running", "success", "skipped", "failed", "degraded"]
StageStatus = Literal["success", "skipped", "failed", "degraded"]
_VALID_TRACE_STATUSES = {"running", "success", "skipped", "failed", "degraded"}
_VALID_STAGE_STATUSES = {"success", "skipped", "failed", "degraded"}


def _utc_now() -> datetime:
    """Return an aware UTC timestamp for trace creation and completion."""

    return datetime.now(UTC)


def _isoformat(value: datetime | None) -> str | None:
    """Convert an optional datetime into a JSON-compatible ISO string."""

    return value.isoformat() if value is not None else None


def _json_safe_copy(value: Any) -> Any:
    """Defensively copy and normalize a JSON-like pipeline value.

    Args:
        value: Mapping, sequence, scalar, or ``None`` supplied by a trace caller.

    Returns:
        A deep JSON-compatible copy. Tuples become lists and datetimes become
        ISO strings so ``to_dict()`` snapshots can be passed directly to later
        JSON Lines or PostgreSQL JSONB writers.
    """

    if value is None:
        return {}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe_copy(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe_copy(item) for item in value]
    return deepcopy(value)


def _validate_non_blank(value: str, *, field_name: str) -> str:
    """Validate and trim a required non-blank string field."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _validate_status(value: str, *, field_name: str, allowed: set[str]) -> str:
    """Validate a trace or stage status against its runtime allowlist."""

    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}")
    return value


@dataclass(slots=True)
class TraceContext:
    """Manage one in-flight trace for either Query or Ingestion pipelines.

    Args:
        trace_type: Stable trace family. F1 supports ``query`` and
            ``ingestion`` because Dashboard pages and PostgreSQL trace tables
            are split by those two links.
        collection: Knowledge collection used by the request.
        trace_id: Optional externally supplied trace ID. When omitted, a stable
            UUID-based ID is generated with the trace type as prefix.
        started_at: Optional request start time. Tests and deterministic
            pipeline runs can inject it; production callers usually omit it.
        raw_query: Query-only original user text.
        request_source: Query-only caller label such as ``aimodel``, ``mcp``,
            or ``dashboard``.
        source_uri: Ingestion-only source path or external URI.
        source_hash: Ingestion-only original source SHA256 digest.
        extra_basic_info: Optional additional JSON-safe identity fields.

    Raises:
        ValueError: If trace type, trace ID, collection, or stage fields violate
            the public trace contract.
    """

    trace_type: TraceType
    collection: str
    trace_id: str | None = None
    started_at: datetime = field(default_factory=_utc_now)
    raw_query: str | None = None
    request_source: str | None = None
    source_uri: str | None = None
    source_hash: str | None = None
    extra_basic_info: dict[str, Any] | None = None
    status: TraceStatus = "running"
    finished_at: datetime | None = None
    stages: list[dict[str, Any]] = field(default_factory=list)
    summary_metrics: dict[str, Any] = field(default_factory=dict)
    evaluation_metrics: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Normalize identity fields immediately after construction."""

        if self.trace_type not in {"query", "ingestion"}:
            raise ValueError("trace_type must be 'query' or 'ingestion'")
        self.collection = _validate_non_blank(self.collection, field_name="collection")
        self.trace_id = (
            _validate_non_blank(self.trace_id, field_name="trace_id")
            if self.trace_id is not None
            else f"{self.trace_type}-{uuid4().hex}"
        )
        self.status = _validate_status(
            self.status,
            field_name="status",
            allowed=_VALID_TRACE_STATUSES,
        )
        self.summary_metrics = _json_safe_copy(self.summary_metrics)
        self.evaluation_metrics = _json_safe_copy(self.evaluation_metrics)
        self.error = _json_safe_copy(self.error) if self.error is not None else None

    def record_stage(
        self,
        stage: str | None = None,
        *,
        duration_ms: float | int | None = None,
        input_summary: dict[str, Any] | None = None,
        output_summary: dict[str, Any] | None = None,
        method: str | None = None,
        provider: str | None = None,
        details: dict[str, Any] | None = None,
        candidate_count: int | None = None,
        status: StageStatus = "success",
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one completed pipeline stage to the trace.

        Args:
            stage: Stable stage name such as ``dense``, ``sparse``, ``rerank``,
                ``load``, or ``transform``. Existing query-engine components
                call this as a keyword argument; tests and future pipelines may
                pass it positionally.
            duration_ms: Non-negative elapsed time in milliseconds.
            input_summary: Trace-safe input digest, never full source text when
                a compact summary is enough.
            output_summary: Trace-safe output digest, such as document/chunk IDs
                or candidate counts.
            method: Algorithm or logical method name.
            provider: Concrete component/provider name.
            details: Extra JSON-safe method/provider details, including
                fallback reasons or route parameters.
            candidate_count: Optional candidate count used by query stages.
            status: Stage outcome.
            error: Optional structured failure details.

        Returns:
            The defensive stage dictionary appended to ``stages``.
        """

        stage_name = _validate_non_blank(stage or "", field_name="stage")
        if duration_ms is not None:
            if isinstance(duration_ms, bool) or duration_ms < 0:
                raise ValueError("duration_ms must be a non-negative number")
            normalized_duration: float | None = float(duration_ms)
        else:
            normalized_duration = None
        if candidate_count is not None and (
            isinstance(candidate_count, bool) or candidate_count < 0
        ):
            raise ValueError("candidate_count must be a non-negative integer")
        normalized_status = _validate_status(
            status,
            field_name="status",
            allowed=_VALID_STAGE_STATUSES,
        )

        stage_record = {
            "stage": stage_name,
            "duration_ms": normalized_duration,
            "status": normalized_status,
            "input_summary": _json_safe_copy(input_summary),
            "output_summary": _json_safe_copy(output_summary),
            "method": method,
            "provider": provider,
            "details": _json_safe_copy(details),
            "error": _json_safe_copy(error) if error is not None else None,
        }
        if candidate_count is not None:
            stage_record["candidate_count"] = int(candidate_count)
        self.stages.append(stage_record)
        return deepcopy(stage_record)

    def finish(
        self,
        *,
        status: TraceStatus,
        finished_at: datetime | None = None,
        summary_metrics: dict[str, Any] | None = None,
        evaluation_metrics: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mark the trace complete and return its JSON-compatible snapshot.

        Args:
            status: Final trace status.
            finished_at: Optional completion timestamp. ``None`` records the
                current UTC time.
            summary_metrics: End-to-end metrics merged into existing summary
                metrics. ``total_duration_ms`` is added when absent.
            evaluation_metrics: Quality metrics merged into existing evaluation
                metrics.
            error: Optional trace-level structured error.

        Returns:
            The completed trace snapshot.
        """

        self.status = _validate_status(
            status,
            field_name="status",
            allowed=_VALID_TRACE_STATUSES,
        )
        self.finished_at = finished_at or _utc_now()
        self.summary_metrics.update(_json_safe_copy(summary_metrics))
        self.evaluation_metrics.update(_json_safe_copy(evaluation_metrics))
        if error is not None:
            self.error = _json_safe_copy(error)
        self.summary_metrics.setdefault(
            "total_duration_ms",
            round((self.finished_at - self.started_at).total_seconds() * 1000, 3),
        )
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible snapshot of the current trace state."""

        basic_info = self._basic_info()
        return {
            "trace_id": self.trace_id,
            "trace_type": self.trace_type,
            "collection": self.collection,
            "status": self.status,
            "started_at": _isoformat(self.started_at),
            "finished_at": _isoformat(self.finished_at),
            "basic_info": basic_info,
            "stages": deepcopy(self.stages),
            "summary_metrics": deepcopy(self.summary_metrics),
            "evaluation_metrics": deepcopy(self.evaluation_metrics),
            "error": deepcopy(self.error),
        }

    def _basic_info(self) -> dict[str, Any]:
        """Build the trace-type-specific identity section."""

        info: dict[str, Any] = {
            "trace_id": self.trace_id,
            "trace_type": self.trace_type,
            "started_at": _isoformat(self.started_at),
            "collection": self.collection,
        }
        if self.trace_type == "query":
            if self.raw_query is not None:
                info["raw_query"] = self.raw_query
            if self.request_source is not None:
                info["request_source"] = self.request_source
        else:
            if self.source_uri is not None:
                info["source_uri"] = self.source_uri
            if self.source_hash is not None:
                info["source_hash"] = self.source_hash
        if self.extra_basic_info:
            info.update(_json_safe_copy(self.extra_basic_info))
        return info
