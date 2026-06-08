"""Persist trace snapshots to local JSON Lines files.

``JsonlTraceWriter`` is the file-backed sink used by ``TraceController`` after
F4. It writes one completed trace snapshot per line to ``src/logs/traces.jsonl``
or a caller-provided path. The writer uses Python ``logging`` plus
``JsonFormatter`` so future observability components can share the same
structured logging boundary while Dashboard services still read plain JSON
Lines.

This module intentionally does not build traces, mutate trace contents, query
PostgreSQL, or manage Dashboard filtering. It only validates that the incoming
snapshot is dictionary-shaped and appends it atomically through a dedicated file
logger.
"""

from __future__ import annotations

import itertools
import logging
from pathlib import Path
from typing import Any

from src.observability.structured_log import configure_jsonl_logger

_LOGGER_COUNTER = itertools.count()


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
