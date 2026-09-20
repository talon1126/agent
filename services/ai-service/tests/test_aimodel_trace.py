"""Protect the AImodel v2 trace and quality-gate contracts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.routers.AImodel.agent_trace import (
    AGENT_TRACE_SCHEMA_VERSION,
    AgentTraceContext,
    AgentTraceEventType,
    AgentTraceStatus,
    normalize_trace_event,
)
from app.routers.AImodel.quality_gates import (
    AgentQualityGateConfig,
    assert_thresholds_not_relaxed,
    build_m3_quality_gate_config,
    build_quality_gate_report,
)

ROOT = Path(__file__).resolve().parents[3]


def _config_payload() -> dict:
    """Return one valid metric definition for focused model tests."""

    return {
        "schema_version": 1,
        "config_version": "m3-v1",
        "milestones": ["M3"],
        "metrics": [
            {
                "metric_id": "M3-01",
                "target": "Complete eligible shopping tasks.",
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


def test_trace_lifecycle_serializes_only_terminal_uniform_events() -> None:
    """A successful turn must serialize stable IDs and closed event timings."""

    context = AgentTraceContext.start(user_query="推荐冰箱", conversation_id=8)
    rank = context.begin_event(
        AgentTraceEventType.RANK,
        summary={"candidate_count": 12},
        related_ids={"candidate_set_id": "set-8"},
    )
    rank.finish(AgentTraceStatus.SUCCESS, summary={"ranked_count": 5})
    context.complete(message_id=81, query_trace_ids=["rag-8"])

    record = context.to_record()
    assert record["trace_schema_version"] == AGENT_TRACE_SCHEMA_VERSION == "v2"
    assert record["trace_id"] == record["agent_trace_id"]
    assert record["status"] == "success"
    assert all(event["trace_id"] == record["trace_id"] for event in record["events"])
    assert all(event["status"] != "started" for event in record["events"])
    assert all(event["duration_ms"] >= 0 for event in record["events"])


def test_trace_failure_closes_open_events_and_sanitizes_exception() -> None:
    """Failure finalization must not leave open spans or leak secret text."""

    context = AgentTraceContext.start(user_query="查询订单")
    context.begin_event(
        AgentTraceEventType.TOOL_CALL,
        summary={"authorization": "Bearer top-secret-token-value-123456789"},
    )
    context.fail(RuntimeError("failed with sk-abcdefghijklmnopqrstuvwxyz123456"))

    record = context.to_record()
    rendered = str(record)
    assert all(event["status"] != "started" for event in record["events"])
    assert "top-secret-token" not in rendered
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in rendered
    assert record["events"][-1]["event_type"] == "error"


def test_legacy_event_normalization_preserves_query_compatibility() -> None:
    """Rows written before A5 must receive deterministic v2 defaults."""

    event = normalize_trace_event(
        {
            "event_type": "intent",
            "status": "success",
            "summary_payload": {"intent": "recommendation"},
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        trace_id="legacy-trace",
        sequence=1,
    )

    assert event["event_id"] == "legacy-trace-event-1"
    assert event["event_type"] == "goal"
    assert event["stage"] == "goal"
    assert event["summary"] == {"intent": "recommendation"}
    assert event["summary_payload"] == event["summary"]


def test_quality_gate_model_rejects_incomplete_or_relaxed_contracts() -> None:
    """Metric lineage is mandatory and frozen thresholds cannot be weakened."""

    frozen = AgentQualityGateConfig.model_validate(_config_payload())

    incomplete = _config_payload()
    del incomplete["metrics"][0]["denominator"]
    with pytest.raises(ValidationError):
        AgentQualityGateConfig.model_validate(incomplete)

    relaxed_payload = deepcopy(_config_payload())
    relaxed_payload["config_version"] = "m3-v2"
    relaxed_payload["metrics"][0]["checks"][0]["threshold"] = 0.8
    relaxed = AgentQualityGateConfig.model_validate(relaxed_payload)
    with pytest.raises(ValueError, match="relax"):
        assert_thresholds_not_relaxed(frozen, relaxed)


def test_quality_report_records_exact_config_version_and_hash() -> None:
    """Every quality report must identify the evaluated immutable config."""

    config = AgentQualityGateConfig.model_validate(_config_payload())
    report = build_quality_gate_report(
        config,
        profile_id="M3",
        metrics={"M3-01": {"shopping_task_success_rate": 0.9}},
    )

    assert report.config_version == config.config_version
    assert len(report.config_sha256) == 64
    assert report.metrics["M3-01"]["shopping_task_success_rate"] == 0.9


def test_frozen_pipeline_thresholds_build_the_complete_m3_dictionary() -> None:
    """The protected gate file must populate the strict A5 metric contract."""

    raw_config = json.loads(
        (ROOT / "config" / "agent_quality_gates.yaml").read_text(encoding="utf-8")
    )
    config = build_m3_quality_gate_config(raw_config)

    assert config.config_version == "agent-quality-gates-v1"
    assert {metric.metric_id for metric in config.metrics} == {
        f"M3-{index:02d}" for index in range(1, 11)
    }
    assert all(metric.window == "rolling_7d" for metric in config.metrics)
