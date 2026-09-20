"""Capture privacy-safe, reconstructable events for one AImodel turn.

The module is the caller-side observability contract for the shopping Agent. It
owns event identity, lifecycle closure, redaction, legacy normalization, and
LangChain tool middleware. It does not store prompts, full external documents,
credentials, or unbounded user text.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from app.routers.AImodel.intent_router import AImodelIntentRoute

AGENT_TRACE_SCHEMA_VERSION = "v2"
_MAX_PREVIEW_CHARS = 120
_MAX_COLLECTION_ITEMS = 20
_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
_ADDRESS_HINT = re.compile(r"(?:省|市|区|县|街道|大道|路|号|单元|室)")
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "address",
)
_CONTENT_KEY_PARTS = ("content", "prompt", "document", "html", "body")
_LEGACY_EVENT_TYPES = {
    "intent": "goal",
    "allowed_tools": "plan",
    "rag_trace_link": "response",
}


class AgentTraceEventType(StrEnum):
    """Enumerate the only event types accepted by the v2 trace contract."""

    CONTEXT = "context"
    GOAL = "goal"
    PLAN = "plan"
    STEP = "step"
    TOOL_CALL = "tool_call"
    FILTER = "filter"
    RANK = "rank"
    VERIFY = "verify"
    RESPONSE = "response"
    ERROR = "error"


class AgentTraceStatus(StrEnum):
    """Describe the lifecycle state of a trace event."""

    STARTED = "started"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(slots=True)
class AgentTraceEvent:
    """Represent one timed, sanitized stage in an Agent decision."""

    trace_id: str
    event_type: AgentTraceEventType
    stage: str
    event_id: str = field(default_factory=lambda: f"event-{uuid4().hex}")
    status: AgentTraceStatus = AgentTraceStatus.STARTED
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float = 0.0
    summary: dict[str, Any] = field(default_factory=dict)
    related_ids: dict[str, str] = field(default_factory=dict)
    tool_name: str | None = None
    error: str | None = None
    _started_clock: float = field(default_factory=time.perf_counter, repr=False)

    def finish(
        self,
        status: AgentTraceStatus | str,
        *,
        summary: Mapping[str, Any] | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Close the event with a terminal state and safe merged summary."""

        terminal = AgentTraceStatus(status)
        if terminal is AgentTraceStatus.STARTED:
            raise ValueError("a trace event cannot finish with status=started")
        if self.status is not AgentTraceStatus.STARTED:
            return
        if summary:
            self.summary.update(_sanitize_mapping(summary))
        self.status = terminal
        elapsed = (time.perf_counter() - self._started_clock) * 1000
        chosen_duration = elapsed if duration_ms is None else duration_ms
        self.duration_ms = max(float(chosen_duration), 0.0)
        self.error = _redact_text(error) if error else None

    def to_record(self) -> dict[str, Any]:
        """Return the v2 event plus aliases used by legacy SQL readers."""

        safe_summary = _sanitize_mapping(self.summary)
        return {
            "trace_id": self.trace_id,
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "stage": self.stage,
            "status": self.status.value,
            "started_at": self.started_at,
            "duration_ms": max(float(self.duration_ms), 0.0),
            "summary": safe_summary,
            "related_ids": dict(self.related_ids),
            "tool_name": self.tool_name,
            "error": self.error,
            "summary_payload": safe_summary,
            "created_at": self.started_at,
        }


@dataclass(frozen=True, slots=True)
class AgentTraceToolCall:
    """Preserve the pre-A5 tool-call inspection interface."""

    tool_name: str
    status: str
    duration_ms: float
    summary_payload: dict[str, Any]
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_event(self) -> dict[str, Any]:
        """Return the historical tool-call dictionary representation."""

        return {
            "event_type": "tool_call",
            "tool_name": self.tool_name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "summary_payload": self.summary_payload,
            "error": self.error,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class AgentTraceContext:
    """Own all events and legacy trace fields for one user turn."""

    agent_trace_id: str
    user_query: str
    conversation_id: int | None = None
    message_id: int | None = None
    intent_route: dict[str, Any] = field(default_factory=dict)
    intent_details: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    tool_calls: list[AgentTraceToolCall] = field(default_factory=list)
    query_trace_ids: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    events: list[AgentTraceEvent] = field(default_factory=list)
    status: AgentTraceStatus = AgentTraceStatus.STARTED

    @property
    def trace_id(self) -> str:
        """Return the canonical ID while retaining ``agent_trace_id``."""

        return self.agent_trace_id

    @property
    def is_terminal(self) -> bool:
        """Return whether the turn has reached success or error."""

        return self.status is not AgentTraceStatus.STARTED

    @classmethod
    def start(
        cls,
        *,
        user_query: str,
        conversation_id: int | None = None,
    ) -> AgentTraceContext:
        """Create a trace and immediately close its context-capture event."""

        context = cls(
            agent_trace_id=f"agent-{uuid4().hex}",
            user_query=_sanitize_user_query(user_query),
            conversation_id=conversation_id,
        )
        event = context.begin_event(
            AgentTraceEventType.CONTEXT,
            summary={
                "query_chars": len(user_query),
                "query_sha256": hashlib.sha256(user_query.encode("utf-8")).hexdigest(),
                "conversation_id": conversation_id,
            },
        )
        event.finish(AgentTraceStatus.SUCCESS)
        return context

    def begin_event(
        self,
        event_type: AgentTraceEventType | str,
        *,
        stage: str | None = None,
        summary: Mapping[str, Any] | None = None,
        related_ids: Mapping[str, Any] | None = None,
        tool_name: str | None = None,
    ) -> AgentTraceEvent:
        """Start and register one event under this turn's sole trace ID."""

        normalized_type = AgentTraceEventType(event_type)
        event = AgentTraceEvent(
            trace_id=self.trace_id,
            event_type=normalized_type,
            stage=_safe_text(stage or normalized_type.value),
            summary=_sanitize_mapping(summary or {}),
            related_ids=_sanitize_related_ids(related_ids),
            tool_name=_safe_text(tool_name) if tool_name else None,
        )
        self.events.append(event)
        return event

    def record_tool_call(
        self,
        *,
        tool_name: str,
        input_payload: Any,
        output_payload: Any | None,
        duration_ms: float,
        status: str,
        error: str | None = None,
    ) -> None:
        """Record a terminal LangChain tool event with summarized payloads."""

        summary_payload = {
            "input_summary": _summarize_payload(input_payload),
            "output_summary": _summarize_payload(output_payload),
        }
        safe_error = _redact_text(error) if error else None
        legacy_call = AgentTraceToolCall(
            tool_name=_safe_text(tool_name),
            status=_safe_text(status),
            duration_ms=max(float(duration_ms), 0.0),
            summary_payload=summary_payload,
            error=safe_error,
        )
        self.tool_calls.append(legacy_call)
        event = self.begin_event(
            AgentTraceEventType.TOOL_CALL,
            summary=summary_payload,
            tool_name=legacy_call.tool_name,
        )
        event.finish(
            AgentTraceStatus.ERROR if status == "error" else AgentTraceStatus.SUCCESS,
            error=safe_error,
            duration_ms=legacy_call.duration_ms,
        )

    def complete(
        self,
        *,
        message_id: int | None,
        query_trace_ids: Sequence[str] | None,
    ) -> None:
        """Close open work, link the response, and finish successfully."""

        if self.is_terminal:
            return
        self.message_id = message_id
        self.query_trace_ids = _unique_non_blank(query_trace_ids or [])
        self._close_open_events(AgentTraceStatus.SKIPPED, reason="not_executed")
        response = self.begin_event(
            AgentTraceEventType.RESPONSE,
            summary={
                "message_id": message_id,
                "query_trace_count": len(self.query_trace_ids),
            },
            related_ids={
                "message_id": message_id,
                **{
                    f"query_trace_id_{index}": trace_id
                    for index, trace_id in enumerate(self.query_trace_ids, 1)
                },
            },
        )
        response.finish(AgentTraceStatus.SUCCESS)
        self.status = AgentTraceStatus.SUCCESS
        self.completed_at = datetime.now(UTC)

    def fail(self, error: Exception) -> None:
        """Close open events and add a privacy-safe terminal error event."""

        if self.is_terminal:
            return
        self.error = _error_summary(error)
        self._close_open_events(AgentTraceStatus.ERROR, reason=error.__class__.__name__)
        terminal = self.begin_event(
            AgentTraceEventType.ERROR,
            summary={"reason": error.__class__.__name__},
        )
        terminal.finish(AgentTraceStatus.ERROR, error=self.error)
        self.status = AgentTraceStatus.ERROR
        self.completed_at = datetime.now(UTC)

    def cancel(self, reason: str = "client_cancelled") -> None:
        """Close a stream abandoned by its client."""

        if self.is_terminal:
            return
        safe_reason = _safe_text(reason) or "client_cancelled"
        self.error = safe_reason
        self._close_open_events(AgentTraceStatus.ERROR, reason=safe_reason)
        terminal = self.begin_event(
            AgentTraceEventType.ERROR,
            summary={"reason": safe_reason},
        )
        terminal.finish(AgentTraceStatus.ERROR, error=safe_reason)
        self.status = AgentTraceStatus.ERROR
        self.completed_at = datetime.now(UTC)

    def _close_open_events(self, status: AgentTraceStatus, *, reason: str) -> None:
        """Give every started event a deterministic terminal state."""

        for event in self.events:
            if event.status is AgentTraceStatus.STARTED:
                event.finish(status, summary={"terminal_reason": reason})

    def to_record(self) -> dict[str, Any]:
        """Return a PostgreSQL-ready v2 record with legacy fields."""

        if not self.is_terminal:
            self._close_open_events(AgentTraceStatus.SKIPPED, reason="snapshot")
        return {
            "trace_schema_version": AGENT_TRACE_SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "agent_trace_id": self.agent_trace_id,
            "status": self.status.value,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "user_query": self.user_query,
            "intent_route": _sanitize_mapping(self.intent_route),
            "allowed_tools": list(self.allowed_tools),
            "query_trace_ids": list(self.query_trace_ids),
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "events": [event.to_record() for event in self.events],
        }


class LangChainAgentTraceMiddleware:
    """Record sanitized success and failure events around LangChain tools."""

    def __init__(
        self,
        context: AgentTraceContext,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._context = context
        self._clock = clock

    def as_middleware(self) -> Any:
        """Return a LangChain ``wrap_tool_call`` middleware instance."""

        from langchain.agents.middleware import wrap_tool_call

        def _trace_tool_call(request: Any, handler: Callable[[Any], Any]) -> Any:
            return self.record_tool_call_for_test(
                tool_name=_tool_name_from_request(request),
                input_payload=getattr(request, "tool_call", None),
                invoke=lambda: handler(request),
            )

        return wrap_tool_call(_trace_tool_call)

    def record_tool_call_for_test(
        self,
        *,
        tool_name: str,
        input_payload: Any,
        invoke: Callable[[], Any],
    ) -> Any:
        """Invoke a tool-like callable and record its terminal outcome."""

        started = self._clock()
        try:
            response = invoke()
        except Exception as error:
            self._context.record_tool_call(
                tool_name=tool_name,
                input_payload=input_payload,
                output_payload=None,
                duration_ms=(self._clock() - started) * 1000,
                status="error",
                error=_error_summary(error),
            )
            raise
        self._context.record_tool_call(
            tool_name=tool_name,
            input_payload=input_payload,
            output_payload=response,
            duration_ms=(self._clock() - started) * 1000,
            status="success",
        )
        return response


def record_intent_route(
    context: AgentTraceContext,
    route: AImodelIntentRoute,
    *,
    candidates: Sequence[dict[str, Any]] | None = None,
) -> None:
    """Store the final intent route and emit its goal event."""

    final_result = {
        "action": route.action,
        "collection": route.collection,
        "collections": list(route.collections),
        "domain": route.domain,
        "category": route.category,
        "intent": route.intent,
        "confidence": route.confidence,
    }
    details = {
        "result": final_result,
        "reason": route.reason,
        "matched_rule": route.matched_rule,
        "matched_terms": list(route.matched_terms),
        "matched_regex": list(route.matched_regex),
        "fallback_used": route.fallback_used,
        "rag_enabled": route.rag_enabled,
        "top_candidates": _top_intent_candidates(candidates or []),
    }
    context.intent_route = _sanitize_mapping(final_result)
    context.intent_details = _sanitize_mapping(details)
    event = context.begin_event(
        AgentTraceEventType.GOAL, summary=context.intent_details
    )
    event.finish(AgentTraceStatus.SUCCESS)


def record_allowed_tools(context: AgentTraceContext, tools: Sequence[Any]) -> None:
    """Store authorized tool names and emit a plan event."""

    names = [_safe_text(getattr(tool, "name", str(tool))) for tool in tools]
    context.allowed_tools = _unique_non_blank(names)
    event = context.begin_event(
        AgentTraceEventType.PLAN,
        summary={
            "tools": list(context.allowed_tools),
            "tool_count": len(context.allowed_tools),
        },
    )
    event.finish(AgentTraceStatus.SUCCESS)


def normalize_trace_event(
    event: Mapping[str, Any],
    *,
    trace_id: str,
    sequence: int,
) -> dict[str, Any]:
    """Upgrade a legacy or v2 event dictionary to the complete v2 shape."""

    raw_type = _safe_text(event.get("event_type") or "step")
    normalized_type = _LEGACY_EVENT_TYPES.get(raw_type, raw_type)
    if normalized_type not in {item.value for item in AgentTraceEventType}:
        normalized_type = AgentTraceEventType.STEP.value
    status = _safe_text(event.get("status") or AgentTraceStatus.SKIPPED.value)
    if status == "cancelled":
        status = AgentTraceStatus.ERROR.value
    if status not in {item.value for item in AgentTraceStatus}:
        status = AgentTraceStatus.ERROR.value
    started_at = event.get("started_at") or event.get("created_at") or datetime.now(UTC)
    summary = event.get("summary") or event.get("summary_payload") or {}
    if not isinstance(summary, Mapping):
        summary = {"value": summary}
    duration = event.get("duration_ms")
    normalized_summary = _sanitize_mapping(summary)
    return {
        "trace_id": _safe_text(event.get("trace_id") or trace_id),
        "event_id": _safe_text(event.get("event_id") or f"{trace_id}-event-{sequence}"),
        "event_type": normalized_type,
        "stage": _safe_text(event.get("stage") or normalized_type),
        "status": status,
        "started_at": started_at,
        "duration_ms": max(float(duration or 0), 0.0),
        "summary": normalized_summary,
        "related_ids": _sanitize_related_ids(event.get("related_ids")),
        "tool_name": event.get("tool_name"),
        "error": _redact_text(event.get("error")) if event.get("error") else None,
        "summary_payload": normalized_summary,
        "created_at": started_at,
    }


def _top_intent_candidates(
    candidates: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return at most three stable, score-bearing candidate summaries."""

    return [
        {
            "domain": candidate.get("domain"),
            "category": candidate.get("category"),
            "intent": candidate.get("intent"),
            "domain_intent": candidate.get("domain_intent"),
            "action": candidate.get("action"),
            "collection": candidate.get("collection"),
            "score": candidate.get("score"),
            "matched_rule": candidate.get("matched_rule"),
            "matched_terms": list(candidate.get("matched_terms") or []),
            "matched_regex": list(candidate.get("matched_regex") or []),
        }
        for candidate in candidates[:3]
    ]


def _tool_name_from_request(request: Any) -> str:
    """Extract a readable tool name from a LangChain ToolCallRequest."""

    tool = getattr(request, "tool", None)
    if tool is not None and getattr(tool, "name", None):
        return str(tool.name)
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "unknown_tool")
    return "unknown_tool"


def _summarize_payload(payload: Any) -> dict[str, Any]:
    """Return counts, stable IDs, and bounded previews for a tool payload."""

    if payload is None:
        return {}
    if isinstance(payload, Mapping):
        summary: dict[str, Any] = {"type": "dict", "key_count": len(payload)}
        for key in ("query", "content", "text", "answer"):
            value = payload.get(key)
            if isinstance(value, str):
                summary[f"{key}_chars"] = len(value)
        for key in ("collection", "trace_id", "query_trace_id", "status", "ok"):
            value = payload.get(key)
            if isinstance(value, str | int | float | bool) or value is None:
                summary[key] = _sanitize_value(key, value)
        data = payload.get("data")
        if isinstance(data, Mapping):
            for key in ("trace_id", "query_trace_id"):
                value = data.get(key)
                if isinstance(value, str):
                    summary[key] = _safe_text(value)
        return summary
    if isinstance(payload, list | tuple):
        return {"type": type(payload).__name__, "item_count": len(payload)}
    if isinstance(payload, str):
        return {
            "type": "str",
            "chars": len(payload),
            "preview": _redact_text(_preview(payload)),
        }
    return {"type": type(payload).__name__}


def _sanitize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively bound and redact a mapping for trace persistence."""

    return {
        _safe_text(key): _sanitize_value(_safe_text(key), item)
        for key, item in list(value.items())[:_MAX_COLLECTION_ITEMS]
    }


def _sanitize_value(key: str, value: Any) -> Any:
    """Sanitize one trace value according to its key and shape."""

    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, list | tuple | set):
        return [
            _sanitize_value(key, item) for item in list(value)[:_MAX_COLLECTION_ITEMS]
        ]
    if isinstance(value, str):
        if any(part in lowered for part in _CONTENT_KEY_PARTS):
            return {
                "chars": len(value),
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
        return _redact_text(_preview(value))
    if value is None or isinstance(value, bool | int | float):
        return value
    return _redact_text(_preview(str(value)))


def _sanitize_related_ids(value: Any) -> dict[str, str]:
    """Return only nonblank scalar IDs from a relation payload."""

    if not isinstance(value, Mapping):
        return {}
    return {
        _safe_text(key): (
            "[REDACTED]"
            if any(part in _safe_text(key).lower() for part in _SENSITIVE_KEY_PARTS)
            else _redact_text(item)
        )
        for key, item in value.items()
        if _safe_text(key) and _safe_text(item)
    }


def _sanitize_user_query(value: str) -> str:
    """Keep a bounded safe query preview while removing likely address data."""

    if len(_ADDRESS_HINT.findall(value)) >= 2:
        return "[REDACTED_ADDRESS_QUERY]"
    return _redact_text(value)


def _unique_non_blank(values: Sequence[str]) -> list[str]:
    """Trim and de-duplicate nonblank values while preserving order."""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = _safe_text(value)
        if candidate and candidate not in seen:
            normalized.append(candidate)
            seen.add(candidate)
    return normalized


def _safe_text(value: Any) -> str:
    """Return a stripped scalar label."""

    return str(value).strip()


def _preview(value: str) -> str:
    """Return a whitespace-normalized, bounded string."""

    normalized = " ".join(value.split())
    if len(normalized) <= _MAX_PREVIEW_CHARS:
        return normalized
    return normalized[: _MAX_PREVIEW_CHARS - 3] + "..."


def _redact_text(value: Any) -> str:
    """Remove known credential forms from a bounded diagnostic string."""

    redacted = str(value)
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return _preview(redacted)


def _error_summary(error: Exception) -> str:
    """Return a bounded exception label with credentials removed."""

    return _redact_text(f"{error.__class__.__name__}: {error}")
