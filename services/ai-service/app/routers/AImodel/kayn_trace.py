"""Export the existing privacy-safe Agent Trace as Kayn/OpenTelemetry spans."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .agent_trace import AgentTraceContext, AgentTraceStatus

_LOGGER = logging.getLogger(__name__)
_SPAN_NAME_PART = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass(frozen=True, slots=True)
class KaynAgentSpan:
    """Framework-neutral span projection used by the SDK exporter and tests."""

    name: str
    started_at: datetime
    duration_ms: float
    status: str
    attributes: dict[str, str | bool | int | float]


def build_kayn_agent_spans(context: AgentTraceContext) -> tuple[KaynAgentSpan, ...]:
    """Convert one terminal Agent trace without copying prompts or tool payloads."""

    completed_at = context.completed_at or context.started_at
    root_duration = max(
        (completed_at - context.started_at).total_seconds() * 1_000,
        0.0,
    )
    spans = [
        KaynAgentSpan(
            name="ai.agent.turn",
            started_at=context.started_at,
            duration_ms=root_duration,
            status=context.status.value,
            attributes=_span_attributes(
                {
                    "kayn.operation.type": "agent",
                    "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.agent.name": "talonmart-shopping-agent",
                    "talonmart.agent.trace_id": context.trace_id,
                    "talonmart.agent.event_count": len(context.events),
                    "talonmart.agent.conversation_id": context.conversation_id,
                    "talonmart.agent.message_id": context.message_id,
                }
            ),
        )
    ]
    for event in context.events:
        spans.append(
            KaynAgentSpan(
                name=(
                    f"ai.agent.{event.event_type.value}."
                    f"{_safe_span_part(event.stage)}"
                ),
                started_at=event.started_at,
                duration_ms=max(event.duration_ms, 0.0),
                status=event.status.value,
                attributes=_span_attributes(
                    {
                        "kayn.operation.type": _operation_type(event.event_type.value),
                        "talonmart.agent.trace_id": context.trace_id,
                        "talonmart.agent.event_id": event.event_id,
                        "talonmart.agent.event_type": event.event_type.value,
                        "talonmart.agent.stage": event.stage,
                        "talonmart.agent.status": event.status.value,
                        "talonmart.agent.duration_ms": round(event.duration_ms, 3),
                        "talonmart.agent.tool_name": event.tool_name,
                        "talonmart.agent.summary": _json_attribute(event.summary),
                        "talonmart.agent.related_ids": _json_attribute(
                            event.related_ids
                        ),
                        "error.type": event.error,
                    }
                ),
            )
        )
    return tuple(spans)


def export_agent_trace_to_kayn(client: Any, context: AgentTraceContext) -> bool:
    """Best-effort export; telemetry failure must never change the Agent answer."""

    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.trace import SpanKind, Status, StatusCode

        records = build_kayn_agent_spans(context)
        root_record = records[0]
        root = client.tracer.start_span(
            root_record.name,
            kind=SpanKind.INTERNAL,
            attributes=root_record.attributes,
            start_time=_nanoseconds(root_record.started_at),
        )
        root_context = otel_trace.set_span_in_context(root)
        for record in records[1:]:
            span = client.tracer.start_span(
                record.name,
                context=root_context,
                kind=SpanKind.INTERNAL,
                attributes=record.attributes,
                start_time=_nanoseconds(record.started_at),
            )
            if record.status == AgentTraceStatus.ERROR.value:
                span.set_status(Status(StatusCode.ERROR, "agent_event_failed"))
            span.end(end_time=_end_nanoseconds(record))
        if root_record.status == AgentTraceStatus.ERROR.value:
            root.set_status(Status(StatusCode.ERROR, "agent_turn_failed"))
        root.end(end_time=_end_nanoseconds(root_record))
        return True
    except Exception:
        _LOGGER.exception(
            "Kayn Agent Trace export failed",
            extra={"trace_id": context.trace_id},
        )
        return False


def _operation_type(event_type: str) -> str:
    if event_type == "tool_call":
        return "tool"
    if event_type == "context":
        return "retrieval"
    if event_type in {"goal", "plan", "step", "filter", "rank", "verify"}:
        return "chain"
    return "agent"


def _span_attributes(values: dict[str, Any]) -> dict[str, str | bool | int | float]:
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, (str, bool, int, float))
    }


def _json_attribute(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return rendered[:8_000]


def _safe_span_part(value: str) -> str:
    return _SPAN_NAME_PART.sub("_", value).strip("_")[:80] or "unknown"


def _nanoseconds(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _end_nanoseconds(record: KaynAgentSpan) -> int:
    return _nanoseconds(record.started_at) + int(record.duration_ms * 1_000_000)


__all__ = [
    "KaynAgentSpan",
    "build_kayn_agent_spans",
    "export_agent_trace_to_kayn",
]
