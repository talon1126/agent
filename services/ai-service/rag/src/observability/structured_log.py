"""Configure lightweight structured JSON logging for local observability.

The observability layer uses Python's standard ``logging`` package as the
transport and a small JSON formatter as the serialization boundary. This keeps
pipeline code independent from file handles while still producing JSON Lines
that Streamlit Dashboard services can read without depending on external
platforms such as LangSmith.

This module does not decide which trace snapshots should be emitted, does not
open PostgreSQL connections, and does not start Dashboard pages. Those
responsibilities belong to ``TraceController`` callers, storage adapters, and
Dashboard services. The formatter only turns log records into deterministic
JSON strings and preserves caller-provided trace payloads.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord(
        name="",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__
)
_TRACE_PAYLOAD_FIELD = "json_payload"


def _json_safe(value: Any) -> Any:
    """Convert common Python objects into JSON-compatible values.

    Args:
        value: Any value supplied through ``logging`` extras or trace payloads.

    Returns:
        A JSON-compatible defensive copy. ``Path`` values become strings,
        ``datetime`` values become ISO timestamps, mappings/lists/tuples are
        processed recursively, and unknown objects fall back to their string
        representation so logging never fails because of diagnostic metadata.
    """

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return str(deepcopy(value))


class JsonFormatter(logging.Formatter):
    """Format ``logging`` records as one compact JSON object per line.

    The formatter has two modes:

    - Normal structured-log records become an envelope containing timestamp,
      logger, level, message, and all non-standard ``extra`` fields.
    - Records with the reserved ``json_payload`` extra are serialized as that
      payload directly. ``JsonlTraceWriter`` uses this mode so each line in
      ``traces.jsonl`` is the trace snapshot itself, not a logging envelope.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Return a JSON string for one log record.

        Args:
            record: Standard Python ``logging`` record.

        Returns:
            Compact UTF-8-safe JSON text. ``ensure_ascii=False`` keeps user
            questions and Chinese document titles readable in local logs.
        """

        payload = getattr(record, _TRACE_PAYLOAD_FIELD, None)
        if payload is not None:
            document = _json_safe(payload)
        else:
            document = {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "logger": record.name,
                "level": record.levelname,
                "message": record.getMessage(),
            }
            document.update(self._extra_fields(record))
        if record.exc_info:
            document["exception"] = self.formatException(record.exc_info)
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def make_record(
        *,
        logger_name: str,
        level_name: str,
        message: str,
        extra: Mapping[str, Any] | None = None,
    ) -> logging.LogRecord:
        """Build a log record for formatter unit tests and adapters.

        Args:
            logger_name: Logger name to place in the structured envelope.
            level_name: Standard logging level name such as ``INFO``.
            message: Human-readable message.
            extra: Optional structured fields to attach to the record.

        Returns:
            ``logging.LogRecord`` with the supplied extra fields attached.

        Raises:
            ValueError: If ``level_name`` does not map to a standard integer
                logging level.
        """

        level = logging.getLevelName(level_name.upper())
        if not isinstance(level, int):
            raise ValueError(f"Unknown logging level: {level_name}")
        record = logging.LogRecord(
            name=logger_name,
            level=level,
            pathname=__file__,
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        for key, value in dict(extra or {}).items():
            setattr(record, key, value)
        return record

    def _extra_fields(self, record: logging.LogRecord) -> dict[str, Any]:
        """Return non-standard log record attributes as JSON-safe fields."""

        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and key != _TRACE_PAYLOAD_FIELD:
                extras[key] = _json_safe(value)
        return extras


def configure_jsonl_logger(
    *,
    logger_name: str,
    log_path: str | Path,
    level: int = logging.INFO,
) -> logging.Logger:
    """Create an isolated file logger that writes JSON Lines.

    Args:
        logger_name: Dedicated logger name. Callers should use a component-
            specific name so handlers do not interfere with unrelated logs.
        log_path: Destination JSON Lines file.
        level: Minimum logging level for emitted records.

    Returns:
        Configured ``logging.Logger`` with exactly one UTF-8 file handler using
        ``JsonFormatter``.

    Side Effects:
        Creates the parent directory for ``log_path`` and opens the log file in
        append mode. Existing handlers on the same logger are closed and
        replaced to avoid duplicate writes in tests and repeated app starts.
    """

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    file_handler.setLevel(level)
    logger.addHandler(file_handler)
    return logger
