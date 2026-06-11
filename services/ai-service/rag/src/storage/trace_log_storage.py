"""Persist trace snapshots to local JSON Lines files.

``JsonlTraceWriter`` is the file-backed sink used by ``TraceController`` after
F4. It writes one completed trace snapshot per line to ``src/logs/traces.jsonl``
or a caller-provided path. The writer uses Python ``logging`` plus
``JsonFormatter`` so future observability components can share the same
structured logging boundary while Dashboard services still read plain JSON
Lines.

``PostgresTraceWriter`` converts the same final snapshot into the typed
repository records consumed by Dashboard services. ``CompositeTraceWriter``
fans one snapshot out to every configured persistence boundary so pipeline
business code never needs storage-specific branches.

This module intentionally does not build traces, mutate trace contents, or
manage Dashboard filtering. It persists already-finished snapshots through
injected storage boundaries.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from src.observability.structured_log import configure_jsonl_logger
from src.storage.repositories import (
    IngestionTraceRecord,
    QueryTraceRecord,
    TraceRepository,
)

_LOGGER_COUNTER = itertools.count()
TraceWriter = Callable[[dict[str, Any]], None]


def build_trace_writer(
    *,
    jsonl_path: str | Path | None = None,
    repository: TraceRepository | None = None,
) -> TraceWriter | None:
    """Compose enabled trace persistence boundaries into one controller sink.

    Args:
        jsonl_path: Optional local JSON Lines destination.
        repository: Optional PostgreSQL repository. Composition roots pass it
            only when ``observability.persist_to_postgresql`` is enabled.

    Returns:
        ``None`` when no boundary is enabled, one concrete writer when only one
        boundary is enabled, or ``CompositeTraceWriter`` for dual persistence.
    """

    writers: list[TraceWriter] = []
    if jsonl_path is not None:
        writers.append(JsonlTraceWriter(jsonl_path))
    if repository is not None:
        writers.append(PostgresTraceWriter(repository))
    if not writers:
        return None
    if len(writers) == 1:
        return writers[0]
    return CompositeTraceWriter(*writers)


class CompositeTraceWriter:
    """Dispatch one completed trace snapshot to multiple persistence writers.

    Args:
        writers: Ordered trace writers. Production uses JSON Lines first and
            PostgreSQL second, while tests can inject lightweight callables.

    Notes:
        Exceptions are intentionally propagated. A configured durable trace
        boundary failing to persist is an observability failure that the
        composition root should surface rather than silently hide.
    """

    def __init__(self, *writers: TraceWriter) -> None:
        """Store an immutable ordered writer sequence."""

        self._writers = tuple(writers)

    def __call__(self, trace_snapshot: dict[str, Any]) -> None:
        """Allow direct use as a ``TraceController`` sink."""

        self.write(trace_snapshot)

    def write(self, trace_snapshot: dict[str, Any]) -> None:
        """Send the same finished snapshot to every configured writer.

        Args:
            trace_snapshot: Completed Query or Ingestion trace dictionary.
        """

        for writer in self._writers:
            writer(trace_snapshot)


class PostgresTraceWriter:
    """Persist finished trace snapshots through ``TraceRepository``.

    Args:
        repository: Typed PostgreSQL repository used by Dashboard history
            services. The writer does not own the repository connection pool.
    """

    def __init__(self, repository: TraceRepository) -> None:
        """Bind the writer to an already configured trace repository."""

        self._repository = repository

    def __call__(self, trace_snapshot: dict[str, Any]) -> None:
        """Allow direct use as a ``TraceController`` sink."""

        self.write(trace_snapshot)

    def write(self, trace_snapshot: dict[str, Any]) -> None:
        """Convert and persist one completed Query or Ingestion snapshot.

        Args:
            trace_snapshot: Completed trace dictionary emitted by
                ``TraceContext.finish_query()`` or
                ``TraceContext.finish_ingestion()``.

        Raises:
            ValueError: If the snapshot is incomplete or has an unsupported
                trace type.
        """

        trace_type = _required_string(trace_snapshot, "trace_type")
        if trace_type == "query":
            self._repository.upsert_query_trace(_query_record(trace_snapshot))
            return
        if trace_type == "ingestion":
            self._repository.upsert_ingestion_trace(
                _ingestion_record(trace_snapshot)
            )
            return
        raise ValueError(f"Unsupported trace_type: {trace_type!r}")


def _query_record(snapshot: dict[str, Any]) -> QueryTraceRecord:
    """Convert one Query Trace snapshot into the repository contract."""

    basic_info = _required_mapping(snapshot, "basic_info")
    return QueryTraceRecord(
        trace_id=_required_string(snapshot, "trace_id"),
        collection_id=_required_string(snapshot, "collection"),
        raw_query=_required_string(basic_info, "raw_query"),
        request_source=_required_string(basic_info, "request_source"),
        started_at=_required_datetime(snapshot, "started_at"),
        finished_at=_optional_datetime(snapshot, "finished_at"),
        status=_required_string(snapshot, "status"),
        basic_info=basic_info,
        stages=tuple(_required_stage_list(snapshot)),
        query_result=_required_mapping(snapshot, "query_result"),
        summary_metrics=_required_mapping(snapshot, "summary_metrics"),
        evaluation_metrics=_required_mapping(snapshot, "evaluation_metrics"),
        error=_optional_mapping(snapshot, "error"),
    )


def _ingestion_record(snapshot: dict[str, Any]) -> IngestionTraceRecord:
    """Convert one Ingestion Trace snapshot into the repository contract."""

    basic_info = _required_mapping(snapshot, "basic_info")
    return IngestionTraceRecord(
        trace_id=_required_string(snapshot, "trace_id"),
        collection_id=_required_string(snapshot, "collection"),
        source_uri=_required_string(basic_info, "source_uri"),
        source_hash=_required_string(basic_info, "source_hash"),
        started_at=_required_datetime(snapshot, "started_at"),
        finished_at=_optional_datetime(snapshot, "finished_at"),
        status=_required_string(snapshot, "status"),
        basic_info=basic_info,
        stages=tuple(_required_stage_list(snapshot)),
        summary_metrics=_required_mapping(snapshot, "summary_metrics"),
        evaluation_metrics=_required_mapping(snapshot, "evaluation_metrics"),
        error=_optional_mapping(snapshot, "error"),
    )


def _required_string(values: Mapping[str, Any], key: str) -> str:
    """Read one required non-blank string from a trace snapshot section."""

    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"trace snapshot must include non-blank {key}")
    return value


def _required_mapping(
    values: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any]:
    """Read one required dictionary-shaped trace section."""

    value = values.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"trace snapshot must include mapping {key}")
    return value


def _optional_mapping(
    values: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any] | None:
    """Read one optional dictionary-shaped trace section."""

    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"trace snapshot {key} must be a mapping or null")
    return value


def _required_stage_list(values: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Read and validate the ordered stage list from a trace snapshot."""

    stages = values.get("stages")
    if not isinstance(stages, list) or any(
        not isinstance(stage, Mapping) for stage in stages
    ):
        raise ValueError("trace snapshot must include a list of stage mappings")
    return stages


def _required_datetime(values: Mapping[str, Any], key: str) -> datetime:
    """Parse one required ISO timestamp from a trace snapshot."""

    value = _optional_datetime(values, key)
    if value is None:
        raise ValueError(f"trace snapshot must include {key}")
    return value


def _optional_datetime(
    values: Mapping[str, Any],
    key: str,
) -> datetime | None:
    """Parse one optional ISO timestamp from a trace snapshot."""

    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"trace snapshot {key} must be an ISO timestamp or null")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"trace snapshot {key} must be an ISO timestamp") from error


class JsonlTraceWriter:
    """Append finished trace snapshots to a JSON Lines file.

    Args:
        log_path: Destination file. Production configuration points this to
            ``src/logs/traces.jsonl``; tests can supply a temporary path.
        logger_name: Optional base logger name. A per-instance suffix is added
            so multiple writers targeting different files do not share handlers.
        level: Logging level used for trace writes.

    Side Effects:
        Creates the log file parent directory during construction and appends a
        line every time ``write()`` or ``__call__()`` is invoked.
    """

    def __init__(
        self,
        log_path: str | Path,
        *,
        logger_name: str = "aimodel_rag.trace",
        level: int = logging.INFO,
    ) -> None:
        """Create a file-backed trace sink without touching trace state."""

        self.log_path = Path(log_path)
        self._logger = configure_jsonl_logger(
            logger_name=f"{logger_name}.{next(_LOGGER_COUNTER)}",
            log_path=self.log_path,
            level=level,
        )

    def __call__(self, trace_snapshot: dict[str, Any]) -> None:
        """Allow the writer to be passed directly as a ``TraceController`` sink.

        Args:
            trace_snapshot: Completed trace dictionary returned by
                ``TraceContext.finish()``, ``finish_query()``, or
                ``finish_ingestion()``.
        """

        self.write(trace_snapshot)

    def write(self, trace_snapshot: dict[str, Any]) -> None:
        """Append one trace snapshot as a single JSON Lines record.

        Args:
            trace_snapshot: Completed trace dictionary. The writer does not
                require a concrete dataclass so it can persist both query and
                ingestion snapshots.

        Raises:
            ValueError: If the snapshot is not a dictionary or omits the
                top-level ``trace_id`` required by Dashboard trace lists.
        """

        if not isinstance(trace_snapshot, dict):
            raise ValueError("trace_snapshot must be a dictionary")
        if not trace_snapshot.get("trace_id"):
            raise ValueError("trace_snapshot must include trace_id")
        self._logger.info("trace flushed", extra={"json_payload": trace_snapshot})
        for handler in self._logger.handlers:
            handler.flush()
