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
_QUERY_STAGES = {
    "query_processing",
    "dense",
    "sparse",
    "fusion",
    "filter",
    "rerank",
    "response",
}
_QUERY_CANDIDATE_STAGES = {"dense", "sparse", "fusion", "filter", "rerank"}
_INGESTION_STAGES = {
    "dedup",
    "load",
    "split",
    "transform",
    "image_caption",
    "embed",
    "upsert",
}
_DOCUMENT_STATUSES = {"success", "skipped", "failed"}
_TRANSFORM_SNAPSHOT_CHANGE_TYPES = {"changed", "unchanged", "added", "removed"}
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


def _normalize_sub_stages(
    sub_stages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Validate and defensively copy nested implementation timing records.

    Args:
        sub_stages: Ordered child records produced inside one documented
            pipeline stage. ``None`` means no implementation breakdown exists.

    Returns:
        JSON-safe child records, or ``None`` when the caller supplied no
        breakdown.

    Raises:
        ValueError: If required identity, duration, status, or count fields are
            missing or invalid.
    """

    if sub_stages is None:
        return None
    normalized: list[dict[str, Any]] = []
    for item in sub_stages:
        if not isinstance(item, dict):
            raise ValueError("sub_stages items must be dictionaries")
        name = _validate_non_blank(
            str(item.get("name") or ""),
            field_name="sub_stages name",
        )
        duration_ms = item.get("duration_ms")
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int | float)
            or duration_ms < 0
        ):
            raise ValueError("sub_stages duration_ms must be a non-negative number")
        status = _validate_status(
            str(item.get("status") or ""),
            field_name="sub_stages status",
            allowed=_VALID_STAGE_STATUSES,
        )
        input_count = _validate_non_negative_int(
            item.get("input_count"),
            field_name="sub_stages input_count",
        )
        output_count = _validate_non_negative_int(
            item.get("output_count"),
            field_name="sub_stages output_count",
        )
        child_record = {
            "name": name,
            "duration_ms": float(duration_ms),
            "status": status,
            "input_count": input_count,
            "output_count": output_count,
            "method": (
                str(item["method"]) if item.get("method") is not None else None
            ),
            "provider": (
                str(item["provider"])
                if item.get("provider") is not None
                else None
            ),
            "error": (
                _json_safe_copy(item.get("error"))
                if item.get("error") is not None
                else None
            ),
        }
        for count_name in ("changed_count", "unchanged_count"):
            if item.get(count_name) is not None:
                child_record[count_name] = _validate_non_negative_int(
                    item.get(count_name),
                    field_name=f"sub_stages {count_name}",
                )
        if item.get("snapshots") is not None:
            child_record["snapshots"] = _normalize_transform_snapshots(
                item.get("snapshots")
            )
        normalized.append(child_record)
    return normalized


def _normalize_transform_snapshots(value: Any) -> list[dict[str, Any]]:
    """Validate bounded before/after chunk previews for a Transform step.

    Args:
        value: Snapshot list supplied by ``TransformPipeline`` after one
            concrete implementation runs.

    Returns:
        JSON-safe snapshots preserving order and boolean truncation flags.

    Raises:
        ValueError: If the snapshot list or one of its stable fields violates
            the trace contract.
    """

    if not isinstance(value, list | tuple):
        raise ValueError("transform snapshots must be a list")
    normalized: list[dict[str, Any]] = []
    for snapshot in value:
        if not isinstance(snapshot, dict):
            raise ValueError("transform snapshot items must be dictionaries")
        change_type = str(snapshot.get("change_type") or "")
        if change_type not in _TRANSFORM_SNAPSHOT_CHANGE_TYPES:
            raise ValueError(
                "snapshot change_type must be one of "
                f"{sorted(_TRANSFORM_SNAPSHOT_CHANGE_TYPES)}"
            )
        normalized.append(
            {
                "chunk_id": _validate_non_blank(
                    str(snapshot.get("chunk_id") or ""),
                    field_name="snapshot chunk_id",
                ),
                "chunk_index": _validate_non_negative_int(
                    snapshot.get("chunk_index"),
                    field_name="snapshot chunk_index",
                ),
                "change_type": change_type,
                "before_preview": str(snapshot.get("before_preview") or ""),
                "after_preview": str(snapshot.get("after_preview") or ""),
                "before_truncated": _validate_bool(
                    snapshot.get("before_truncated"),
                    field_name="snapshot before_truncated",
                ),
                "after_truncated": _validate_bool(
                    snapshot.get("after_truncated"),
                    field_name="snapshot after_truncated",
                ),
            }
        )
    return normalized


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


def _validate_bool(value: bool, *, field_name: str) -> bool:
    """Validate a required boolean metric without accepting truthy strings."""

    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _validate_top_k_results(value: list[dict[str, Any]], *, field_name: str) -> list[Any]:
    """Validate and copy the query trace Top-k result summary list.

    Args:
        value: Public-safe summaries of final ranked results. The trace stores
            only compact objects supplied by query/response builders, not full
            internal provider payloads.
        field_name: Metric name used in validation errors.

    Returns:
        A JSON-safe defensive copy of the result list.

    Raises:
        ValueError: If the value is not a list of dictionaries.
    """

    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of result summaries")
    if any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field_name} must contain dictionaries only")
    return _json_safe_copy(value)


def _validate_candidate_count_by_stage(
    value: dict[str, int],
    *,
    field_name: str,
) -> dict[str, int]:
    """Validate query-stage candidate counts for Dashboard comparisons.

    Args:
        value: Mapping from retrieval stage names to non-negative candidate
            counts. The accepted keys match the summary metric contract:
            ``dense``, ``sparse``, ``fusion``, ``filter``, and ``rerank``.
        field_name: Metric name used in validation errors.

    Returns:
        A normalized dictionary with trimmed stage names and integer counts.

    Raises:
        ValueError: If the mapping shape, stage names, or counts violate the
            query trace summary contract.
    """

    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dictionary")
    normalized: dict[str, int] = {}
    for stage, count in value.items():
        stage_name = _validate_non_blank(stage, field_name=field_name)
        if stage_name not in _QUERY_CANDIDATE_STAGES:
            raise ValueError(
                f"{field_name} keys must be one of {sorted(_QUERY_CANDIDATE_STAGES)}"
            )
        normalized[stage_name] = _validate_non_negative_int(
            count,
            field_name=field_name,
        )
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
    def query(
        cls,
        *,
        collection: str,
        raw_query: str,
        request_source: str | None = None,
        trace_id: str | None = None,
        started_at: datetime | None = None,
        extra_basic_info: dict[str, Any] | None = None,
    ) -> TraceContext:
        """Create a trace context with the documented query identity.

        Args:
            collection: Knowledge collection searched by the query.
            raw_query: Original user question before normalization or rewrite.
            request_source: Optional caller label such as ``aimodel``, ``mcp``,
                ``dashboard``, or ``query_cli``.
            trace_id: Optional stable trace ID generated by a caller.
            started_at: Optional deterministic start timestamp.
            extra_basic_info: Optional extra identity fields for future query
                entry points.

        Returns:
            ``TraceContext`` configured with ``trace_type='query'`` and
            validated request identity fields.
        """

        return cls(
            trace_id=trace_id,
            trace_type="query",
            collection=collection,
            started_at=started_at or _utc_now(),
            raw_query=_validate_non_blank(raw_query, field_name="raw_query"),
            request_source=(
                _validate_non_blank(request_source, field_name="request_source")
                if request_source is not None
                else None
            ),
            extra_basic_info=extra_basic_info,
        )

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
        if self.trace_type == "query":
            if self.raw_query is None:
                raise ValueError("raw_query is required for query traces")
            self.raw_query = _validate_non_blank(
                self.raw_query,
                field_name="raw_query",
            )
            if self.request_source is not None:
                self.request_source = _validate_non_blank(
                    self.request_source,
                    field_name="request_source",
                )
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
        sub_stages: list[dict[str, Any]] | None = None,
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
            sub_stages: Optional implementation-level records executed inside
                this documented top-level stage.
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
        normalized_sub_stages = _normalize_sub_stages(sub_stages)
        if normalized_sub_stages is not None:
            stage_record["sub_stages"] = normalized_sub_stages
        if candidate_count is not None:
            stage_record["candidate_count"] = int(candidate_count)
        self.stages.append(stage_record)
        return deepcopy(stage_record)

    def record_query_stage(
        self,
        stage: str,
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
        """Record one documented Query Pipeline stage.

        Args:
            stage: One of ``query_processing``, ``dense``, ``sparse``,
                ``fusion``, ``filter``, ``rerank``, or ``response``.
            duration_ms: Stage duration in milliseconds.
            input_summary: Query or candidate input digest.
            output_summary: Candidate, ranking, or rewrite output digest.
            method: Algorithm name such as ``rrf`` or ``pgvector_search``.
            provider: Concrete component/provider name.
            details: Stage-specific diagnostics, including fallback reasons,
                hit terms, filtered reasons, or rerank deltas.
            candidate_count: Optional number of candidates produced by this
                query stage.
            status: Stage status.
            error: Optional structured stage failure.

        Returns:
            The stage dictionary appended to the trace.

        Raises:
            ValueError: If the context is not a query trace or the stage is
                absent from the documented query stage allowlist.
        """

        if self.trace_type != "query":
            raise ValueError("record_query_stage requires a query trace")
        stage_name = _validate_non_blank(stage, field_name="query stage")
        if stage_name not in _QUERY_STAGES:
            raise ValueError(f"query stage must be one of {sorted(_QUERY_STAGES)}")
        return self.record_stage(
            stage_name,
            duration_ms=duration_ms,
            input_summary=input_summary,
            output_summary=output_summary,
            method=method,
            provider=provider,
            details=details,
            candidate_count=candidate_count,
            status=status,
            error=error,
        )

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
        sub_stages: list[dict[str, Any]] | None = None,
        status: StageStatus = "success",
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one documented Ingestion Pipeline stage.

        Args:
            stage: One of ``dedup``, ``load``, ``split``, ``transform``,
                ``image_caption``, ``embed``, or ``upsert``.
            duration_ms: Stage duration in milliseconds.
            input_summary: Source or stage input digest.
            output_summary: Document/chunk/index output digest.
            method: Loader, splitter, transform, embedding, or storage method.
            provider: Concrete component/provider name.
            details: Stage-specific diagnostics, such as skip reasons,
                generated counts, or failure context.
            sub_stages: Optional ordered implementation records nested under
                the top-level ingestion stage.
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
            sub_stages=sub_stages,
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

    def finish_query(
        self,
        *,
        status: TraceStatus,
        top_k_results: list[dict[str, Any]],
        candidate_count_by_stage: dict[str, int],
        fallback_used: bool,
        finished_at: datetime | None = None,
        error: dict[str, Any] | None = None,
        query_document_relevance: float | int | None = None,
        citation_hit_rate: float | int | None = None,
        rerank_delta: dict[str, Any] | None = None,
        empty_result: bool | None = None,
    ) -> dict[str, Any]:
        """Finish a query trace with retrieval, rerank, and quality metrics.

        Args:
            status: Final trace lifecycle status.
            top_k_results: Public-safe final Top-k result summaries.
            candidate_count_by_stage: Candidate counts for Dense, Sparse,
                Fusion, Filter, and Rerank comparison views.
            fallback_used: Whether the query path used a degradation path, such
                as rerank fallback to filtered RRF order.
            finished_at: Optional deterministic completion timestamp.
            error: Optional link-level structured error.
            query_document_relevance: Optional relevance score between 0 and 1.
            citation_hit_rate: Optional citation correctness score between 0
                and 1.
            rerank_delta: Optional mapping that explains rank changes before
                and after rerank.
            empty_result: Optional boolean indicating no final result.

        Returns:
            Completed query trace snapshot.
        """

        if self.trace_type != "query":
            raise ValueError("finish_query requires a query trace")
        summary_metrics = {
            "top_k_results": _validate_top_k_results(
                top_k_results,
                field_name="top_k_results",
            ),
            "candidate_count_by_stage": _validate_candidate_count_by_stage(
                candidate_count_by_stage,
                field_name="candidate_count_by_stage",
            ),
            "fallback_used": _validate_bool(
                fallback_used,
                field_name="fallback_used",
            ),
            "error": _json_safe_copy(error) if error is not None else None,
        }

        evaluation_metrics: dict[str, Any] = {}
        relevance = _validate_optional_ratio(
            query_document_relevance,
            field_name="query_document_relevance",
        )
        citation_rate = _validate_optional_ratio(
            citation_hit_rate,
            field_name="citation_hit_rate",
        )
        if relevance is not None:
            evaluation_metrics["query_document_relevance"] = relevance
        if citation_rate is not None:
            evaluation_metrics["citation_hit_rate"] = citation_rate
        if rerank_delta is not None:
            evaluation_metrics["rerank_delta"] = _json_safe_copy(rerank_delta)
        if empty_result is not None:
            evaluation_metrics["empty_result"] = _validate_bool(
                empty_result,
                field_name="empty_result",
            )

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
