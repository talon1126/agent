"""Record AImodel Agent Trace events around LangChain execution.

This module belongs to the AImodel orchestration layer. It captures the pieces
that are hard to reconstruct from final messages alone: caller-side intent
routing, authorized tool lists, LangChain tool-call outcomes, RAG trace links,
and terminal status. It deliberately stores summaries and identifiers instead
of full prompts, full tool JSON, RAG contexts, API keys, or chunk text.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.routers.AImodel.intent_router import AImodelIntentRoute

_MAX_PREVIEW_CHARS = 120


@dataclass(frozen=True, slots=True)
class AgentTraceToolCall:
    """Represent one sanitized tool-call event in an AImodel turn.

    Args:
        tool_name: LangChain-visible tool name.
        status: Outcome status such as ``success`` or ``error``.
        duration_ms: Wall-clock duration for the tool invocation.
        summary_payload: Redacted payload containing counts, IDs, and status.
        error: Optional short exception summary for failed tool calls.
        created_at: UTC timestamp when the event was recorded.
    """

    tool_name: str
    status: str
    duration_ms: float
    summary_payload: dict[str, Any]
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_event(self) -> dict[str, Any]:
        """Return the database/event representation of this tool call."""

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
    """Hold trace-safe diagnostics for one AImodel user turn.

    Args:
        agent_trace_id: Stable trace ID for the AImodel turn.
        user_query: Original user-visible query. Stored because it is already the
            primary message content, but downstream payloads are still summarized.
        conversation_id: Conversation ID assigned by the memory store.
        message_id: Assistant message ID once the final answer is persisted.
        intent_route: Compact final intent result stored on the trace row.
        intent_details: Detailed intent diagnostics stored only in events.
        allowed_tools: Tool names available to LangChain after routing.
        tool_calls: Sanitized tool-call events collected by middleware.
        query_trace_ids: RAG trace IDs linked to the final assistant message.
        error: Optional terminal error summary.
        started_at: UTC timestamp for trace creation.
        completed_at: UTC timestamp set when the turn finishes.
    """

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

    @classmethod
    def start(
        cls,
        *,
        user_query: str,
        conversation_id: int | None = None,
    ) -> AgentTraceContext:
        """Create a trace context for a single AImodel turn.

        Args:
            user_query: Original user message for the turn.
            conversation_id: Optional conversation ID if already known.

        Returns:
            A mutable context that service code and middleware can enrich.
        """

        return cls(
            agent_trace_id=f"agent-{uuid4().hex}",
            user_query=user_query,
            conversation_id=conversation_id,
        )

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
        """Append one redacted tool-call event.

        Args:
            tool_name: LangChain tool name.
            input_payload: Raw tool input object. Only counts and short metadata
                are kept.
            output_payload: Raw tool output object. Only status, trace IDs, and
                small counts are kept.
            duration_ms: Tool duration in milliseconds.
            status: ``success`` or ``error``.
            error: Optional exception summary for failed calls.
        """

        self.tool_calls.append(
            AgentTraceToolCall(
                tool_name=_safe_text(tool_name),
                status=_safe_text(status),
                duration_ms=max(float(duration_ms), 0.0),
                summary_payload={
                    "input_summary": _summarize_payload(input_payload),
                    "output_summary": _summarize_payload(output_payload),
                },
                error=error,
            )
        )

    def complete(
        self,
        *,
        message_id: int | None,
        query_trace_ids: Sequence[str] | None,
    ) -> None:
        """Mark the turn as completed and attach final message/RAG IDs."""

        self.message_id = message_id
        self.query_trace_ids = _unique_non_blank(query_trace_ids or [])
        self.completed_at = datetime.now(UTC)

    def fail(self, error: Exception) -> None:
        """Mark the trace as failed without storing a full stack trace."""

        self.error = _error_summary(error)
        self.completed_at = datetime.now(UTC)

    def to_record(self) -> dict[str, Any]:
        """Return a PostgreSQL-ready trace record without answer summaries."""

        events = [
            _event("intent", status="success", summary_payload=self.intent_details),
            _event(
                "allowed_tools",
                status="success",
                summary_payload={"tools": list(self.allowed_tools)},
            ),
            *(tool_call.to_event() for tool_call in self.tool_calls),
        ]
        for query_trace_id in self.query_trace_ids:
            events.append(
                _event(
                    "rag_trace_link",
                    status="success",
                    summary_payload={"query_trace_id": query_trace_id},
                )
            )
        if self.error:
            events.append(
                _event("error", status="error", summary_payload={}, error=self.error)
            )
        return {
            "agent_trace_id": self.agent_trace_id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "user_query": self.user_query,
            "intent_route": dict(self.intent_route),
            "allowed_tools": list(self.allowed_tools),
            "query_trace_ids": list(self.query_trace_ids),
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "events": events,
        }


class LangChainAgentTraceMiddleware:
    """LangChain middleware adapter for AImodel tool-call tracing.

    The production hook is exposed via ``as_middleware`` so service code can add
    it to ``create_agent(..., middleware=[...])``. The explicit
    ``record_tool_call_for_test`` method keeps the redaction and duration logic
    testable without invoking a full LangChain graph.
    """

    def __init__(self, context: AgentTraceContext, *, clock: Callable[[], float] = time.perf_counter) -> None:
        """Create middleware bound to one trace context.

        Args:
            context: Trace context for the current AImodel turn.
            clock: Monotonic clock used for deterministic tests.
        """

        self._context = context
        self._clock = clock

    def as_middleware(self) -> Any:
        """Return a LangChain ``wrap_tool_call`` middleware instance.

        Returns:
            Middleware accepted by ``create_agent``. If LangChain internals change
            and the import is unavailable, the exception is allowed to surface in
            tests instead of silently disabling observability.
        """

        from langchain.agents.middleware import wrap_tool_call

        def _trace_tool_call(request: Any, handler: Callable[[Any], Any]) -> Any:
            tool_name = _tool_name_from_request(request)
            input_payload = getattr(request, "tool_call", None)
            return self.record_tool_call_for_test(
                tool_name=tool_name,
                input_payload=input_payload,
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
        """Invoke one tool-like callable and record success or error.

        Args:
            tool_name: Tool name to record.
            input_payload: Raw tool input object to summarize.
            invoke: Callable that executes the actual tool call.

        Returns:
            The callable return value.

        Raises:
            Exception: Re-raises any exception from ``invoke`` after writing an
                error event into the trace context.
        """

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
    """Record the final intent result and detailed event diagnostics."""

    final_result = {
        "action": route.action,
        "collection": route.collection,
        "collections": list(route.collections),
        "domain": route.domain,
        "category": route.category,
        "intent": route.intent,
        "confidence": route.confidence,
    }
    context.intent_route = final_result
    context.intent_details = {
        "result": final_result,
        "reason": route.reason,
        "matched_rule": route.matched_rule,
        "matched_terms": list(route.matched_terms),
        "matched_regex": list(route.matched_regex),
        "fallback_used": route.fallback_used,
        "rag_enabled": route.rag_enabled,
        "top_candidates": _top_intent_candidates(candidates or []),
    }


def _top_intent_candidates(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return at most three score-bearing candidate summaries for events."""

    safe_candidates: list[dict[str, Any]] = []
    for candidate in candidates[:3]:
        safe_candidates.append(
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
        )
    return safe_candidates


def record_allowed_tools(context: AgentTraceContext, tools: Sequence[Any]) -> None:
    """Record the LangChain-visible tool names authorized for the turn."""

    names = [_safe_text(getattr(tool, "name", str(tool))) for tool in tools]
    context.allowed_tools = _unique_non_blank(names)


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
    """Return trace-safe counts and IDs for an arbitrary payload."""

    if payload is None:
        return {}
    if isinstance(payload, dict):
        summary: dict[str, Any] = {"type": "dict", "key_count": len(payload)}
        for key in ("query", "content", "text", "answer"):
            value = payload.get(key)
            if isinstance(value, str):
                summary[f"{key}_chars"] = len(value)
        for key in ("collection", "trace_id", "query_trace_id", "status", "ok"):
            value = payload.get(key)
            if isinstance(value, str | int | float | bool) or value is None:
                summary[key] = value
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("trace_id", "query_trace_id"):
                value = data.get(key)
                if isinstance(value, str):
                    summary[key] = value
        return summary
    if isinstance(payload, list | tuple):
        return {"type": type(payload).__name__, "item_count": len(payload)}
    if isinstance(payload, str):
        return {"type": "str", "chars": len(payload), "preview": _preview(payload)}
    return {"type": type(payload).__name__}


def _event(
    event_type: str,
    *,
    status: str,
    summary_payload: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    """Create a generic trace event payload."""

    return {
        "event_type": event_type,
        "tool_name": None,
        "status": status,
        "duration_ms": None,
        "summary_payload": summary_payload,
        "error": error,
        "created_at": datetime.now(UTC),
    }


def _unique_non_blank(values: Sequence[str]) -> list[str]:
    """Trim and de-duplicate non-blank values while preserving order."""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = _safe_text(value)
        if candidate and candidate not in seen:
            normalized.append(candidate)
            seen.add(candidate)
    return normalized


def _safe_text(value: Any) -> str:
    """Return a stripped string for trace IDs and labels."""

    return str(value).strip()


def _preview(value: str) -> str:
    """Return a short preview for scalar strings, never full large text."""

    normalized = " ".join(value.split())
    if len(normalized) <= _MAX_PREVIEW_CHARS:
        return normalized
    return normalized[: _MAX_PREVIEW_CHARS - 3] + "..."


def _error_summary(error: Exception) -> str:
    """Return a short exception label without stack trace details."""

    return f"{error.__class__.__name__}: {error}"
