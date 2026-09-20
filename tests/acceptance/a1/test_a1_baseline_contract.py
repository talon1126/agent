"""Frozen acceptance contract for task A1."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[3]
ROUTES_PATH = (
    ROOT / "services" / "ai-service" / "app" / "routers" / "AImodel"
    / "intent_routes.yaml"
)
BASELINE_PATH = ROOT / "fixtures" / "evals" / "shopping_agent_baseline.json"
REPORT_PATH = ROOT / "docs" / "agent_baseline.md"
AGENT_TEST_PATH = (
    ROOT / "services" / "ai-service" / "tests" / "test_aimodel_agent.py"
)

TOOLS_BY_ACTION = {
    "rag": ["rag_tool"],
    "product_api": ["get_product_detail_from_link", "search_product_catalog"],
    "order_api": ["get_order_status"],
    "web": ["search_web_with_tavily"],
    "direct": [],
    "refuse": [],
}
REQUIRED_BEHAVIORS = {
    "product_search",
    "product_link_detail",
    "rag",
    "multi_collection",
    "tavily_unconfigured",
    "order_status",
    "direct",
    "refuse",
    "rag_unavailable",
    "mock_api_error",
    "conversation_history",
    "recommended_links",
    "sse_success",
    "sse_generation_error",
}
REQUIRED_REGRESSION_TESTS = {
    "test_agent_baseline_fixture_routes_are_deterministic",
    "test_agent_baseline_allowed_tools_match_route_action",
    "test_handle_chat_baseline_product_link_and_mock_api_error",
    "test_stream_chat_baseline_sse_contract",
    "test_stream_chat_baseline_generation_error_event",
}
REQUIRED_REPORT_TOKENS = {
    "## Environment",
    "## Route Inventory",
    "## Fast Paths",
    "## Failure Baseline",
    "## Performance Baseline",
    "## Reproduce",
    "model_ms",
    "tool_ms",
    "total_ms",
    "uv run --project services/ai-service pytest",
}


def _load_baseline() -> dict[str, Any]:
    assert BASELINE_PATH.is_file(), f"missing A1 deliverable: {BASELINE_PATH}"
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _configured_routes() -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(ROUTES_PATH.read_text(encoding="utf-8"))
    routes: dict[str, dict[str, Any]] = {}
    for domain, domain_node in payload["routers"].items():
        for category, category_node in domain_node["categories"].items():
            for intent, rule in category_node["intents"].items():
                routes[f"{domain}.{category}.{intent}"] = rule
    return routes


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _walk_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _walk_strings(child)]
    return []


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_bytes().decode("utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def test_route_cases_cover_every_configured_route_and_tool_boundary() -> None:
    baseline = _load_baseline()
    configured = _configured_routes()
    route_cases = baseline["route_cases"]

    assert baseline["schema_version"] == 1
    assert len({case["case_id"] for case in route_cases}) == len(route_cases)
    assert {case["expected_route"] for case in route_cases} == set(configured)

    for case in route_cases:
        route = configured[case["expected_route"]]
        assert case["input"].strip()
        assert case["expected_action"] == route["action"]
        assert case["expected_collection"] == route.get("collection")
        assert case["allowed_tools"] == TOOLS_BY_ACTION[route["action"]]
        assert case["execution_path"] in {
            "agent_with_tools",
            "agent_without_tools",
        }
        assert case["response_shape"] in {
            "text_answer",
            "text_answer_with_recommended_links",
            "controlled_refusal",
        }


def test_behavior_matrix_covers_a1_paths_and_degradations() -> None:
    baseline = _load_baseline()
    behaviors = baseline["behavior_cases"]
    by_name = {case["behavior"] for case in behaviors}

    assert REQUIRED_BEHAVIORS <= by_name
    assert len({case["case_id"] for case in behaviors}) == len(behaviors)
    for case in behaviors:
        assert case["expected_outcome"] in {"success", "degraded", "error"}
        assert case["observable_assertions"]

    success_sse = next(case for case in behaviors if case["behavior"] == "sse_success")
    assert success_sse["event_sequence"] == ["status", "delta", "done"]
    assert success_sse["done_count"] == 1
    assert success_sse["forbidden_output"] == [
        "raw_tool_json",
        "trace_id",
        "chunk_id",
    ]

    error_sse = next(
        case for case in behaviors if case["behavior"] == "sse_generation_error"
    )
    assert error_sse["event_sequence"] == ["status", "error"]
    assert error_sse["done_count"] == 0


def test_trace_baseline_is_representative_timed_and_sanitized() -> None:
    baseline = _load_baseline()
    traces = baseline["trace_summaries"]

    assert len(traces) >= 10
    assert len({trace["trace_id"] for trace in traces}) == len(traces)
    failure_categories = {trace["failure_category"] for trace in traces}
    assert {
        "none",
        "tavily_unconfigured",
        "rag_unavailable",
        "mock_api_error",
    } <= failure_categories

    for trace in traces:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", trace["input_fingerprint"])
        assert trace["capture_mode"] in {"deterministic_mock", "local_integration"}
        timing = trace["timing_ms"]
        assert set(timing) == {"model_ms", "tool_ms", "total_ms"}
        assert all(isinstance(value, (int, float)) for value in timing.values())
        assert all(value >= 0 for value in timing.values())
        assert timing["model_ms"] <= timing["total_ms"]
        assert timing["tool_ms"] <= timing["total_ms"]

    serialized = "\n".join(_walk_strings(traces))
    assert not re.search(r"(?i)(authorization|api[_-]?key|bearer\s+[a-z0-9])", serialized)
    assert not re.search(r"\b1[3-9]\d{9}\b", serialized)


def test_baseline_report_is_reproducible_and_bound_to_fixture() -> None:
    baseline = _load_baseline()
    assert REPORT_PATH.is_file(), f"missing A1 deliverable: {REPORT_PATH}"
    report = REPORT_PATH.read_text(encoding="utf-8")

    for token in REQUIRED_REPORT_TOKENS:
        assert token in report
    routes_hash = _normalized_text_sha256(ROUTES_PATH)
    assert baseline["metadata"]["intent_routes_sha256"] == routes_hash
    assert baseline["metadata"]["baseline_id"] in report
    assert routes_hash in report
    assert baseline["metadata"]["git_commit"] in report
    assert baseline["metadata"]["external_services"] == "mocked"


def test_agent_suite_contains_required_behavior_regressions() -> None:
    tree = ast.parse(AGENT_TEST_PATH.read_text(encoding="utf-8"))
    test_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert REQUIRED_REGRESSION_TESTS <= test_names
