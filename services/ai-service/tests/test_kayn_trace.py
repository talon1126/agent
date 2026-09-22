from __future__ import annotations

from app.routers.AImodel.agent_trace import (
    AgentTraceContext,
    AgentTraceEventType,
    AgentTraceStatus,
)
from app.routers.AImodel.kayn_trace import build_kayn_agent_spans


def test_kayn_trace_projection_reuses_safe_agent_events() -> None:
    trace = AgentTraceContext.start(
        user_query="联系 13800138000 推荐手机",
        conversation_id=12,
    )
    event = trace.begin_event(
        AgentTraceEventType.STEP,
        stage="tool_executor",
        summary={"status": "success", "authorization": "Bearer private-token"},
        related_ids={"plan_id": "plan-1", "step_id": "search"},
        tool_name="product_search",
    )
    event.finish(AgentTraceStatus.SUCCESS, duration_ms=18.5)
    trace.complete(message_id=99, query_trace_ids=[])

    spans = build_kayn_agent_spans(trace)

    assert spans[0].name == "ai.agent.turn"
    assert spans[0].attributes["talonmart.agent.trace_id"] == trace.trace_id
    step = next(span for span in spans if ".step." in span.name)
    assert step.duration_ms == 18.5
    assert step.attributes["kayn.operation.type"] == "chain"
    assert "private-token" not in str(spans)
    assert "13800138000" not in str(spans)
