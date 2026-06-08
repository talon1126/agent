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
_INGESTION_STAGES = {"dedup", "load", "split", "transform", "embed", "upsert"}
_DOCUMENT_STATUSES = {"success", "skipped", "failed"}
_SHA256_HEX_LENGTH = 64


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
        A deep JSON-compatible copy. ``None`` remains ``None`` when it appears
        inside a caller-provided payload; tuples become lists and datetimes
        become ISO strings so ``to_dict()`` snapshots can be passed directly to
        later JSON Lines or PostgreSQL JSONB writers.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe_copy(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe_copy(item) for item in value]
    return deepcopy(value)


def _json_section(value: Any) -> Any:
    """Normalize optional trace sections while preserving nested ``None``."""

    if value is None:
        return {}
    return _json_safe_copy(value)


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


def _validate_sha256(value: str, *, field_name: str) -> str:
    """Validate a SHA256 hex digest used by ingestion trace identity."""

    normalized = _validate_non_blank(value, field_name=field_name).lower()
    if len(normalized) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a SHA256 hexadecimal digest")
    return normalized


def _validate_non_negative_int(value: int, *, field_name: str) -> int:
    """Validate a non-negative integer ingestion metric."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _validate_optional_ratio(value: float | int | None, *, field_name: str) -> float | None:
    """Validate an optional quality ratio between 0 and 1."""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a number between 0 and 1")
    normalized = float(value)
    if normalized < 0 or normalized > 1:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return normalized


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

    @classmethod
    def ingestion(
        cls,
        *,
        collection: str,
        source_uri: str,
        source_hash: str,
        trace_id: str | None = None,
        started_at: datetime | None = None,
        extra_basic_info: dict[str, Any] | None = None,
    ) -> TraceContext:
        """Create a trace context with the documented ingestion identity.

        Args:
            collection: Target collection receiving the source document.
            source_uri: Original document path or external source URI.
            source_hash: SHA256 digest calculated before Loader conversion.
            trace_id: Optional stable trace ID generated by a caller.
            started_at: Optional deterministic start timestamp.
            extra_basic_info: Optional extra identity fields for future
                ingestion entry points.

        Returns:
            ``TraceContext`` configured with ``trace_type='ingestion'`` and
            validated source identity fields.
        """

        return cls(
            trace_id=trace_id,
            trace_type="ingestion",
            collection=collection,
            started_at=started_at or _utc_now(),
            source_uri=_validate_non_blank(source_uri, field_name="source_uri"),
            source_hash=_validate_sha256(source_hash, field_name="source_hash"),
            extra_basic_info=extra_basic_info,
        )

    def __post_init__(self) -> None:
        """Normalize identity fields immediately after construction."""

        if self.trace_type not in {"query", "ingestion"}:
            raise ValueError("trace_type must be 'query' or 'ingestion'")
        self.collection = _validate_non_blank(self.collection, field_name="collection")
        if self.trace_type == "ingestion":
            if self.source_uri is None:
                raise ValueError("source_uri is required for ingestion traces")
            if self.source_hash is None:
                raise ValueError("source_hash is required for ingestion traces")
            self.source_uri = _validate_non_blank(
                self.source_uri,
                field_name="source_uri",
            )
            self.source_hash = _validate_sha256(
                self.source_hash,
                field_name="source_hash",
            )
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
        self.summary_metrics = _json_section(self.summary_metrics)
        self.evaluation_metrics = _json_section(self.evaluation_metrics)
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
            "input_summary": _json_section(input_summary),
            "output_summary": _json_section(output_summary),
            "method": method,
            "provider": provider,
            "details": _json_section(details),
            "error": _json_safe_copy(error) if error is not None else None,
        }
        if candidate_count is not None:
            stage_record["candidate_count"] = int(candidate_count)
        self.stages.append(stage_record)
        return deepcopy(stage_record)

    def record_ingestion_stage(
        self,
        stage: str,
        *,
        duration_ms: float | int | None = None,
        input_summary: dict[str, Any] | None = None,
        output_summary: dict[str, Any] | None = None,
        method: str | None = None,
        provider: str | None = None,
        details: dict[str, Any] | None = None,
        status: StageStatus = "success",
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one documented Ingestion Pipeline stage.

        Args:
            stage: One of ``dedup``, ``load``, ``split``, ``transform``,
                ``embed``, or ``upsert``.
            duration_ms: Stage duration in milliseconds.
            input_summary: Source or stage input digest.
            output_summary: Document/chunk/index output digest.
            method: Loader, splitter, transform, embedding, or storage method.
            provider: Concrete component/provider name.
            details: Stage-specific diagnostics, such as skip reasons,
                generated counts, or failure context.
            status: Stage status.
            error: Optional structured stage failure.

        Returns:
            The stage dictionary appended to the trace.

        Raises:
            ValueError: If the context is not an ingestion trace or the stage is
                absent from the documented ingestion stage allowlist.
        """

        if self.trace_type != "ingestion":
            raise ValueError("record_ingestion_stage requires an ingestion trace")
        stage_name = _validate_non_blank(stage, field_name="ingestion stage")
        if stage_name not in _INGESTION_STAGES:
            raise ValueError(
                f"ingestion stage must be one of {sorted(_INGESTION_STAGES)}"
            )
        return self.record_stage(
            stage_name,
            duration_ms=duration_ms,
            input_summary=input_summary,
            output_summary=output_summary,
            method=method,
            provider=provider,
            details=details,
            status=status,
            error=error,
        )

    def finish_ingestion(
        self,
        *,
        status: TraceStatus,
        document_status: str,
        chunk_count: int,
        embedded_count: int,
        skipped_count: int,
        finished_at: datetime | None = None,
        error: dict[str, Any] | None = None,
        chunk_quality_score: float | int | None = None,
        noise_reduction_summary: dict[str, Any] | None = None,
        embedding_coverage: float | int | None = None,
        index_ready: bool | None = None,
    ) -> dict[str, Any]:
        """Finish an ingestion trace with the documented metric sections.

        Args:
            status: Final trace lifecycle status.
            document_status: Ingestion document status such as ``success``,
                ``skipped``, or ``failed``.
            chunk_count: Final retrievable chunk count.
            embedded_count: Number of chunks newly embedded.
            skipped_count: Number of document/chunk units skipped by hash reuse.
            finished_at: Optional deterministic completion timestamp.
            error: Optional link-level structured error.
            chunk_quality_score: Optional quality score between 0 and 1.
            noise_reduction_summary: Optional denoise effect evidence.
            embedding_coverage: Optional Dense/BM25 coverage ratio between 0
                and 1.
            index_ready: Optional boolean indicating searchable readiness.

        Returns:
            Completed ingestion trace snapshot.
        """

        if self.trace_type != "ingestion":
            raise ValueError("finish_ingestion requires an ingestion trace")
        normalized_document_status = _validate_status(
            document_status,
            field_name="document_status",
            allowed=_DOCUMENT_STATUSES,
        )
        summary_metrics = {
            "document_status": normalized_document_status,
            "chunk_count": _validate_non_negative_int(
                chunk_count,
                field_name="chunk_count",
            ),
            "embedded_count": _validate_non_negative_int(
                embedded_count,
                field_name="embedded_count",
            ),
            "skipped_count": _validate_non_negative_int(
                skipped_count,
                field_name="skipped_count",
            ),
            "error": _json_safe_copy(error) if error is not None else None,
        }
        if index_ready is not None and not isinstance(index_ready, bool):
            raise ValueError("index_ready must be a boolean")

        evaluation_metrics: dict[str, Any] = {}
        quality_score = _validate_optional_ratio(
            chunk_quality_score,
            field_name="chunk_quality_score",
        )
        coverage = _validate_optional_ratio(
            embedding_coverage,
            field_name="embedding_coverage",
        )
        if quality_score is not None:
            evaluation_metrics["chunk_quality_score"] = quality_score
        if noise_reduction_summary is not None:
            evaluation_metrics["noise_reduction_summary"] = _json_safe_copy(
                noise_reduction_summary
            )
        if coverage is not None:
            evaluation_metrics["embedding_coverage"] = coverage
        if index_ready is not None:
            evaluation_metrics["index_ready"] = index_ready

        return self.finish(
            status=status,
            finished_at=finished_at,
            summary_metrics=summary_metrics,
            evaluation_metrics=evaluation_metrics,
            error=error,
        )

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
        self.summary_metrics.update(_json_section(summary_metrics))
        self.evaluation_metrics.update(_json_section(evaluation_metrics))
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
