"""Frozen acceptance contract for task A5 trace observability."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[3]
AI_SERVICE_ROOT = ROOT / "services" / "ai-service"
sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.routers.AImodel import agent_trace as trace_module  # noqa: E402
from app.routers.AImodel import memory as aimodel_memory  # noqa: E402
from app.routers.AImodel import quality_gates  # noqa: E402
from app.routers.AImodel import schemas as aimodel_schemas  # noqa: E402
from app.routers.AImodel import service as aimodel_service  # noqa: E402


REQUIRED_EVENT_TYPES = {
    "context",
    "goal",
    "plan",
    "step",
    "tool_call",
    "filter",
    "rank",
    "verify",
    "response",
    "error",
}
TERMINAL_STATUSES = {"success", "error", "skipped"}


def _parse_sse_event(raw_event: str) -> tuple[str, dict[str, Any]]:
    lines = raw_event.strip().splitlines()
    event = next(
        line.removeprefix("event: ") for line in lines if line.startswith("event: ")
    )
    data = json.loads(
        next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
    )
    return event, data


def _quality_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "config_version": "m3-v1",
        "milestones": ["M3"],
        "metrics": [
            {
                "metric_id": "M3-01",
                "target": "Shopping tasks completed successfully.",
                "denominator": "eligible shopping tasks",
                "window": "rolling_7d",
                "applicable_milestones": ["M3"],
                "checks": [
                    {
                        "field": "shopping_task_success_rate",
                        "operator": "gte",
                        "threshold": 0.85,
                    }
                ],
            }
        ],
    }


def test_event_and_status_dictionaries_are_closed_and_versioned() -> None:
    assert {item.value for item in trace_module.AgentTraceEventType} == REQUIRED_EVENT_TYPES
    assert {item.value for item in trace_module.AgentTraceStatus} == {
        "started",
        "success",
        "error",
        "skipped",
    }
    assert trace_module.AGENT_TRACE_SCHEMA_VERSION == "v2"


def test_trace_events_have_uniform_identity_timing_summary_and_relations() -> None:
    context = trace_module.AgentTraceContext.start(
        user_query="帮我挑选一台微波炉",
        conversation_id=42,
    )
    event = context.begin_event(
        trace_module.AgentTraceEventType.FILTER,
        summary={"candidate_count": 8},
        related_ids={"candidate_set_id": "set-1"},
    )
    event.finish(
        trace_module.AgentTraceStatus.SUCCESS,
        summary={"remaining_count": 3},
    )
    context.complete(message_id=7, query_trace_ids=["rag-1"])

    record = context.to_record()
    assert record["trace_id"] == context.trace_id
    assert record["trace_schema_version"] == "v2"
    assert record["status"] == "success"
    assert record["events"]

    for item in record["events"]:
        assert item["trace_id"] == context.trace_id
        assert item["event_id"]
        assert item["event_type"] in REQUIRED_EVENT_TYPES
        assert item["stage"]
        assert item["status"] in TERMINAL_STATUSES
        assert item["started_at"] is not None
        assert item["duration_ms"] >= 0
        assert isinstance(item["summary"], dict)

    filtered = next(item for item in record["events"] if item["event_id"] == event.event_id)
    assert filtered["summary"] == {"candidate_count": 8, "remaining_count": 3}
    assert filtered["related_ids"] == {"candidate_set_id": "set-1"}


@pytest.mark.parametrize("terminal", ["failure", "cancel"])
def test_failure_and_cancel_close_every_started_event(terminal: str) -> None:
    context = trace_module.AgentTraceContext.start(user_query="推荐耳机")
    context.begin_event(trace_module.AgentTraceEventType.STEP, summary={"step": 1})
    context.begin_event(trace_module.AgentTraceEventType.RANK, summary={"count": 5})

    if terminal == "failure":
        context.fail(RuntimeError("model unavailable"))
    else:
        context.cancel("client_cancelled")

    record = context.to_record()
    assert context.is_terminal is True
    assert all(event["status"] in TERMINAL_STATUSES for event in record["events"])
    assert all(event["duration_ms"] >= 0 for event in record["events"])
    assert record["events"][-1]["event_type"] == "error"
    assert record["events"][-1]["summary"]["reason"] in {
        "RuntimeError",
        "client_cancelled",
    }


def test_trace_payload_redacts_secrets_addresses_and_unbounded_text() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    address = "北京市朝阳区望京街道 88 号 3 单元 1201"
    context = trace_module.AgentTraceContext.start(
        user_query=f"Authorization: Bearer {secret}，送到{address}。" + "很长" * 300
    )
    event = context.begin_event(
        trace_module.AgentTraceEventType.TOOL_CALL,
        summary={
            "api_key": secret,
            "authorization": f"Bearer {secret}",
            "shipping_address": address,
            "external_content": "外部网页正文" * 300,
            "candidate_ids": ["sku-1", "sku-2"],
        },
    )
    event.finish(trace_module.AgentTraceStatus.SUCCESS)
    context.complete(message_id=9, query_trace_ids=[])

    serialized = json.dumps(context.to_record(), ensure_ascii=False, default=str)
    assert secret not in serialized
    assert address not in serialized
    assert "外部网页正文" * 20 not in serialized
    assert "sku-1" in serialized


def test_legacy_trace_event_receives_compatible_defaults() -> None:
    normalized = trace_module.normalize_trace_event(
        {
            "event_type": "tool_call",
            "tool_name": "rag_tool",
            "status": "success",
            "duration_ms": None,
            "summary_payload": {"query_trace_id": "rag-old"},
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        trace_id="agent-old",
        sequence=3,
    )

    assert normalized["trace_id"] == "agent-old"
    assert normalized["event_id"] == "agent-old-event-3"
    assert normalized["stage"] == "tool_call"
    assert normalized["duration_ms"] == 0
    assert normalized["summary"] == {"query_trace_id": "rag-old"}
    assert normalized["summary_payload"] == normalized["summary"]


def test_trace_database_migration_is_idempotent_and_keeps_legacy_columns() -> None:
    statements = [
        " ".join(statement.split())
        for statement in aimodel_memory.POSTGRES_AIMODEL_MEMORY_SCHEMA_SQL
    ]
    trace_statements = [statement for statement in statements if "agent_trace" in statement]
    joined = "\n".join(trace_statements)

    for column in ("event_id", "stage", "started_at", "related_ids"):
        assert any(
            f"ADD COLUMN IF NOT EXISTS {column}" in statement
            for statement in trace_statements
        )
    for legacy_column in ("event_type", "summary_payload", "created_at"):
        assert legacy_column in joined
    assert "DROP TABLE" not in joined.upper()
    assert "TRUNCATE TABLE agent_trace" not in joined.upper()


def test_trace_storage_failure_keeps_done_response_and_is_observable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingTraceStore(aimodel_memory.NoopAiModelMemoryStore):
        def persist_agent_trace(self, trace_record: dict[str, Any]) -> None:
            raise RuntimeError("trace database unavailable")

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    before = aimodel_service.get_trace_persist_failure_count()
    events = [
        _parse_sse_event(event)
        for event in aimodel_service.stream_chat_events(
            aimodel_schemas.AiModelChatRequest(user_id=1, message="你好"),
            mock_api_url="http://mock-api",
            streaming_agent_runner=lambda _request, _results: ["正常回答"],
            memory_store=FailingTraceStore(),
        )
    ]

    assert events[-1][0] == "done"
    assert events[-1][1]["answer"] == "正常回答"
    assert aimodel_service.get_trace_persist_failure_count() == before + 1
    assert "Agent Trace persistence failed" in caplog.text


def test_closing_stream_records_client_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    class RecordingStore(aimodel_memory.NoopAiModelMemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.traces: list[dict[str, Any]] = []

        def persist_agent_trace(self, trace_record: dict[str, Any]) -> None:
            self.traces.append(trace_record)

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    store = RecordingStore()
    stream = aimodel_service.stream_chat_events(
        aimodel_schemas.AiModelChatRequest(user_id=1, message="推荐耳机"),
        mock_api_url="http://mock-api",
        streaming_agent_runner=lambda _request, _results: ["第一段", "第二段"],
        memory_store=store,
    )
    next(stream)
    next(stream)
    next(stream)
    stream.close()

    assert len(store.traces) == 1
    trace = store.traces[0]
    assert trace["status"] == "error"
    assert trace["events"][-1]["event_type"] == "error"
    assert trace["events"][-1]["summary"]["reason"] == "client_cancelled"


def test_quality_gate_schema_rejects_duplicates_unknown_milestones_and_missing_window() -> None:
    valid = _quality_config()
    parsed = quality_gates.AgentQualityGateConfig.model_validate(valid)
    assert parsed.config_version == "m3-v1"

    duplicate = _quality_config()
    duplicate["metrics"].append(dict(duplicate["metrics"][0]))
    with pytest.raises(ValidationError):
        quality_gates.AgentQualityGateConfig.model_validate(duplicate)

    unknown_milestone = _quality_config()
    unknown_milestone["metrics"][0]["applicable_milestones"] = ["UNKNOWN"]
    with pytest.raises(ValidationError):
        quality_gates.AgentQualityGateConfig.model_validate(unknown_milestone)

    missing_window = _quality_config()
    del missing_window["metrics"][0]["window"]
    with pytest.raises(ValidationError):
        quality_gates.AgentQualityGateConfig.model_validate(missing_window)


def test_quality_thresholds_cannot_be_relaxed_and_reports_bind_config_hash() -> None:
    frozen = quality_gates.AgentQualityGateConfig.model_validate(_quality_config())
    relaxed_payload = _quality_config()
    relaxed_payload["config_version"] = "m3-v2"
    relaxed_payload["metrics"][0]["checks"][0]["threshold"] = 0.8
    relaxed = quality_gates.AgentQualityGateConfig.model_validate(relaxed_payload)

    with pytest.raises(ValueError, match="relax"):
        quality_gates.assert_thresholds_not_relaxed(frozen, relaxed)

    report = quality_gates.build_quality_gate_report(
        frozen,
        profile_id="M3",
        metrics={"M3-01": {"shopping_task_success_rate": 0.9}},
    )
    assert report.config_version == "m3-v1"
    assert len(report.config_sha256) == 64
    assert report.profile_id == "M3"


def test_m3_dictionary_and_trace_document_cover_frozen_contract() -> None:
    metric_ids = set(quality_gates.M3_METRIC_DICTIONARY)
    assert metric_ids == {f"M3-{index:02d}" for index in range(1, 11)}
    for definition in quality_gates.M3_METRIC_DICTIONARY.values():
        assert definition.target
        assert definition.denominator
        assert definition.window
        assert "M3" in definition.applicable_milestones

    document = ROOT / "docs" / "agent_trace_dictionary.md"
    text = document.read_text(encoding="utf-8")
    for event_type in REQUIRED_EVENT_TYPES:
        assert f"`{event_type}`" in text
    for metric_id in metric_ids:
        assert metric_id in text
    for phrase in ("trace_id", "event_id", "duration_ms", "脱敏", "config_sha256"):
        assert phrase in text
