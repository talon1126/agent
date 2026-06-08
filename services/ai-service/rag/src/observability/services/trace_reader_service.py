"""Project stored pipeline traces into Dashboard-friendly read models.

``TraceReaderService`` sits between Streamlit pages and the durable
``TraceRepository``. Repository records preserve the complete PostgreSQL trace
schema, while Dashboard pages need compact history rows, waterfall-ready stage
items, candidate-count summaries, fallback flags, and detail payloads. Keeping
that projection here prevents page code from depending on table-specific
dataclasses or nested JSON key conventions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from src.storage.postgres import PostgresPool
from src.storage.repositories import (
    IngestionTraceRecord,
    QueryTraceRecord,
    TraceRepository,
)

TraceKind = Literal["query", "ingestion"]


@dataclass(frozen=True, slots=True)
class TraceStageWaterfallItem:
    """Represent one trace stage as a chart/table-ready Dashboard row.

    The Query Trace and Ingestion Trace pages use this DTO to render waterfall
    charts and expandable stage tables. It keeps only public-safe stage
    evidence from the trace JSON: stage name, duration, status, provider/method
    labels, candidate counts, details, and optional error payload.
    """

    stage: str
    duration_ms: float | None
    status: str
    method: str | None = None
    provider: str | None = None
    candidate_count: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TraceHistoryItem:
    """Represent one trace in the Dashboard history list.

    History rows intentionally flatten the repository trace record into fields
    that are cheap to sort, filter, and scan in Streamlit. ``display_input`` is
    the raw query for Query traces and source URI for Ingestion traces.
    """

    trace_id: str
    trace_type: TraceKind
    collection_id: str
    status: str
    display_input: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: float | None
    stage_count: int
    fallback_used: bool
    error: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TraceDetail:
    """Represent the complete Dashboard detail view for one trace.

    Detail rows preserve the four trace sections required by the DEV_SPEC while
    adding Dashboard-specific projections: waterfall rows, candidate-count
    comparisons, and rerank delta. Page modules should consume this DTO instead
    of reaching into PostgreSQL repository records directly.
    """

    trace_id: str
    trace_type: TraceKind
    collection_id: str
    status: str
    display_input: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: float | None
    waterfall: tuple[TraceStageWaterfallItem, ...]
    candidate_counts: Mapping[str, int] = field(default_factory=dict)
    summary_metrics: Mapping[str, Any] = field(default_factory=dict)
    evaluation_metrics: Mapping[str, Any] = field(default_factory=dict)
    rerank_delta: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None


class TraceReaderService:
    """Read query and ingestion traces for Dashboard history/detail pages."""

    def __init__(
        self,
        pool: PostgresPool,
        *,
        repository: TraceRepository | None = None,
    ) -> None:
        """Bind trace reads to a repository without opening new connections.

        Args:
            pool: Open PostgreSQL pool used when ``repository`` is omitted.
            repository: Optional repository injection for tests or alternate
                storage adapters. The service owns no persistence logic.
        """

        self._repository = repository or TraceRepository(pool)

    def list_query_traces(self, collection_id: str) -> list[TraceHistoryItem]:
        """Return query trace history rows newest-first for one collection.

        Args:
            collection_id: Dashboard-selected knowledge collection.

        Returns:
            Compact rows suitable for the Query Trace history table.
        """

        _validate_non_blank(collection_id, field_name="collection_id")
        return [
            _query_history_item(record)
            for record in self._repository.list_query_traces(collection_id)
        ]

    def list_ingestion_traces(self, collection_id: str) -> list[TraceHistoryItem]:
        """Return ingestion trace history rows newest-first for one collection.

        Args:
            collection_id: Dashboard-selected knowledge collection.

        Returns:
            Compact rows suitable for the Ingestion Trace history table.
        """

        _validate_non_blank(collection_id, field_name="collection_id")
        return [
            _ingestion_history_item(record)
            for record in self._repository.list_ingestion_traces(collection_id)
        ]

    def get_query_trace_detail(self, trace_id: str) -> TraceDetail | None:
        """Return one query trace detail projection by trace ID.

        Args:
            trace_id: Stable ID emitted by the Query Pipeline.

        Returns:
            Detail DTO with waterfall rows and query-specific metrics, or
            ``None`` when the trace is absent.
        """

        _validate_non_blank(trace_id, field_name="trace_id")
        record = self._repository.get_query_trace(trace_id)
        return _query_detail(record) if record is not None else None

    def get_ingestion_trace_detail(self, trace_id: str) -> TraceDetail | None:
        """Return one ingestion trace detail projection by trace ID.

        Args:
            trace_id: Stable ID emitted by the Ingestion Pipeline.

        Returns:
            Detail DTO with waterfall rows and ingestion-specific metrics, or
            ``None`` when the trace is absent.
        """

        _validate_non_blank(trace_id, field_name="trace_id")
        record = self._repository.get_ingestion_trace(trace_id)
        return _ingestion_detail(record) if record is not None else None


def _query_history_item(record: QueryTraceRecord) -> TraceHistoryItem:
    """Convert a repository query trace into a Dashboard history row."""

    return TraceHistoryItem(
        trace_id=record.trace_id,
        trace_type="query",
        collection_id=record.collection_id,
        status=record.status,
        display_input=record.raw_query,
        started_at=record.started_at,
        finished_at=record.finished_at,
        duration_ms=_duration(record),
        stage_count=len(record.stages),
        fallback_used=_fallback_used(record.summary_metrics, record.stages),
        error=record.error,
    )


def _ingestion_history_item(record: IngestionTraceRecord) -> TraceHistoryItem:
    """Convert a repository ingestion trace into a Dashboard history row."""

    return TraceHistoryItem(
        trace_id=record.trace_id,
        trace_type="ingestion",
        collection_id=record.collection_id,
        status=record.status,
        display_input=record.source_uri,
        started_at=record.started_at,
        finished_at=record.finished_at,
        duration_ms=_duration(record),
        stage_count=len(record.stages),
        fallback_used=False,
        error=record.error,
    )


def _query_detail(record: QueryTraceRecord) -> TraceDetail:
    """Convert a query trace into a Dashboard detail payload."""

    return TraceDetail(
        trace_id=record.trace_id,
        trace_type="query",
        collection_id=record.collection_id,
        status=record.status,
        display_input=record.raw_query,
        started_at=record.started_at,
        finished_at=record.finished_at,
        duration_ms=_duration(record),
        waterfall=_waterfall(record.stages),
        candidate_counts=_candidate_counts(record.summary_metrics, record.stages),
        summary_metrics=record.summary_metrics,
        evaluation_metrics=record.evaluation_metrics,
        rerank_delta=dict(record.evaluation_metrics.get("rerank_delta") or {}),
        error=record.error,
    )


def _ingestion_detail(record: IngestionTraceRecord) -> TraceDetail:
    """Convert an ingestion trace into a Dashboard detail payload."""

    return TraceDetail(
        trace_id=record.trace_id,
        trace_type="ingestion",
        collection_id=record.collection_id,
        status=record.status,
        display_input=record.source_uri,
        started_at=record.started_at,
        finished_at=record.finished_at,
        duration_ms=_duration(record),
        waterfall=_waterfall(record.stages),
        candidate_counts={},
        summary_metrics=record.summary_metrics,
        evaluation_metrics=record.evaluation_metrics,
        error=record.error,
    )


def _waterfall(
    stages: tuple[Mapping[str, Any], ...],
) -> tuple[TraceStageWaterfallItem, ...]:
    """Normalize trace stage dictionaries for waterfall rendering."""

    items: list[TraceStageWaterfallItem] = []
    for stage in stages:
        items.append(
            TraceStageWaterfallItem(
                stage=str(stage.get("stage") or ""),
                duration_ms=_optional_float(stage.get("duration_ms")),
                status=str(stage.get("status") or "success"),
                method=_optional_str(stage.get("method")),
                provider=_optional_str(stage.get("provider")),
                candidate_count=_optional_int(stage.get("candidate_count")),
                details=dict(stage.get("details") or {}),
                error=(
                    dict(stage.get("error"))
                    if isinstance(stage.get("error"), Mapping)
                    else None
                ),
            )
        )
    return tuple(items)


def _candidate_counts(
    summary_metrics: Mapping[str, Any],
    stages: tuple[Mapping[str, Any], ...],
) -> dict[str, int]:
    """Return query-stage candidate counts from summary or stage records."""

    summary_counts = summary_metrics.get("candidate_count_by_stage")
    if isinstance(summary_counts, Mapping):
        return {
            str(stage): int(count)
            for stage, count in summary_counts.items()
            if _is_int_like(count)
        }
    counts: dict[str, int] = {}
    for stage in stages:
        stage_name = stage.get("stage")
        candidate_count = stage.get("candidate_count")
        if isinstance(stage_name, str) and _is_int_like(candidate_count):
            counts[stage_name] = int(candidate_count)
    return counts


def _duration(record: QueryTraceRecord | IngestionTraceRecord) -> float | None:
    """Return total duration from summary metrics or timestamps."""

    total_duration = record.summary_metrics.get("total_duration_ms")
    if isinstance(total_duration, int | float) and not isinstance(total_duration, bool):
        return float(total_duration)
    if record.finished_at is None:
        return None
    return round((record.finished_at - record.started_at).total_seconds() * 1000, 3)


def _fallback_used(
    summary_metrics: Mapping[str, Any],
    stages: tuple[Mapping[str, Any], ...],
) -> bool:
    """Detect graceful fallback from summary metrics or degraded stages."""

    if isinstance(summary_metrics.get("fallback_used"), bool):
        return bool(summary_metrics["fallback_used"])
    for stage in stages:
        details = stage.get("details")
        if stage.get("status") == "degraded":
            return True
        if isinstance(details, Mapping) and details.get("fallback_reason"):
            return True
    return False


def _validate_non_blank(value: str, *, field_name: str) -> None:
    """Reject blank Dashboard trace identifiers before repository access."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _optional_float(value: Any) -> float | None:
    """Convert optional numeric trace values to float for charting."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    """Convert optional integer-like trace values to int for summaries."""

    return int(value) if _is_int_like(value) else None


def _optional_str(value: Any) -> str | None:
    """Convert optional non-empty values into Dashboard-safe strings."""

    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _is_int_like(value: Any) -> bool:
    """Return whether a value can be safely displayed as an integer count."""

    return isinstance(value, int) and not isinstance(value, bool)
