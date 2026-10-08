#!/usr/bin/env python3
"""Replay frozen M1 shopping conversations through the production SSE path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/ai-service"))
sys.path.insert(0, str(ROOT / "services/ai-service/tests"))

from agent_pipeline import (  # noqa: E402
    evaluate_quality_profile,
    load_structured,
    sha256_mapping,
)
from app.kayn_runtime_fingerprint import (  # noqa: E402
    evaluated_files,
    implementation_fingerprint,
)
from app.routers.AImodel.evaluation import evaluate_chat_non_streaming  # noqa: E402
from app.routers.AImodel.goal_orchestrator import ShoppingGoalOrchestrator  # noqa: E402
from app.routers.AImodel.goal_repository import InMemoryShoppingGoalRepository  # noqa: E402
from app.routers.AImodel.memory import NoopAiModelMemoryStore  # noqa: E402
from app.routers.AImodel.schemas import AiModelChatRequest  # noqa: E402
from m1_replay_support import ITEMS, product_client  # noqa: E402


FIXTURE = ROOT / "fixtures/evals/m1_shopping_replay_v2.json"
PRICE_CLAIM = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s+(CNY|USD)\b")


def load_fixture() -> dict[str, Any]:
    fixture = json.loads(FIXTURE.read_text("utf-8"))
    if fixture.get("schema_version") != 1 or not fixture.get("cases"):
        raise ValueError("invalid M1 replay fixture")
    for name, expected in fixture["sources"].items():
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"frozen M1 data source changed: {name}")
    ids = [case["id"] for case in fixture["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate M1 replay case ID")
    return fixture


def _check_turn(
    result: Any,
    expected: dict[str, Any],
    case: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    metadata = result.metadata
    payload = metadata.get("payload", {})
    if metadata.get("response_type") != expected["response_type"]:
        errors.append("response_type")
    if expected.get("reason_code") != payload.get("reason_code") and "reason_code" in expected:
        errors.append("reason_code")
    products = payload.get("products") or payload.get("candidates") or []
    actual_ids = [item["item_id"] for item in products]
    if set(actual_ids) != set(expected["item_ids"]):
        errors.append("item_ids")
    for term in expected.get("answer_contains", []):
        if term not in result.output:
            errors.append(f"answer:{term}")
    tool_names = [call["name"] for call in result.toolCalls]
    for tool in expected.get("required_tools", []):
        if tool not in tool_names:
            errors.append(f"missing_tool:{tool}")
    for tool in expected.get("forbidden_tools", []):
        if tool in tool_names:
            errors.append(f"unauthorized_tool:{tool}")
    allowed_prices = {
        str(item["price"])
        for item in ITEMS.values()
        if item["item_id"] in actual_ids
    }
    for price, currency in PRICE_CLAIM.findall(result.output):
        if float(price) not in {float(value) for value in allowed_prices}:
            errors.append(f"unsupported_price:{price}")
        if currency != case.get("snapshot_currency", "CNY"):
            errors.append(f"unsupported_currency:{currency}")
    if case["id"] == "unknown_stock_recommendation" and "有货" in result.output:
        errors.append("unsupported_stock")
    return errors


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    os.environ.setdefault("DASHSCOPE_API_KEY", "m1-local-replay")
    calls: list[str] = []
    memory = NoopAiModelMemoryStore()
    repository = InMemoryShoppingGoalRepository(
        conversation_owner=memory.get_conversation_owner
    )
    orchestrator = ShoppingGoalOrchestrator(repository, memory, model_extractor=None)
    client = product_client(
        calls,
        reviews_by_item=case.get("reviews"),
        use_fixture_inventory=True,
        search_item_ids=set(case["search_item_ids"]) if "search_item_ids" in case else None,
        snapshot_currency=case.get("snapshot_currency", "CNY"),
        failure_paths=set(case.get("failure_paths", [])),
        search_results_by_query=case.get("search_results_by_query"),
    )
    conversation_id: int | None = None
    turns: list[dict[str, Any]] = []
    for index, expected in enumerate(case["turns"], start=1):
        before = len(calls)
        try:
            result = evaluate_chat_non_streaming(
                AiModelChatRequest.model_validate(
                    {
                        "user_id": 81001,
                        "conversation_id": conversation_id,
                        "message": expected["message"],
                        "page_context": expected["page_context"],
                        "request_version": "v2",
                    }
                ),
                mock_api_url="http://mock-api",
                http_client=client,
                memory_store=memory,
                goal_orchestrator=orchestrator,
            )
            conversation_id = result.metadata["conversation_id"]
            errors = _check_turn(result, expected, case)
            turns.append(
                {
                    "turn": index,
                    "response_type": result.metadata["response_type"],
                    "output": result.output,
                    "errors": errors,
                    "tool_calls": result.toolCalls,
                    "mock_api_paths": calls[before:],
                }
            )
        except Exception as exc:
            turns.append(
                {
                    "turn": index,
                    "response_type": "execution_error",
                    "errors": [f"execution_error:{type(exc).__name__}"],
                    "tool_calls": [],
                    "mock_api_paths": calls[before:],
                }
            )
            break
    return {
        "id": case["id"],
        "category": case["category"],
        "passed": len(turns) == len(case["turns"])
        and all(not turn["errors"] for turn in turns),
        "turns": turns,
    }


def build_report(fixture: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    quality = load_structured(ROOT / "config/agent_quality_gates.yaml")
    by_category = Counter(case["category"] for case in fixture["cases"])
    passed_by_category = Counter(
        result["category"] for result in results if result["passed"]
    )
    errors = [
        error
        for result in results
        for turn in result["turns"]
        for error in turn["errors"]
    ]
    total = len(fixture["cases"])
    passed = sum(result["passed"] for result in results)
    return {
        "schema_version": 1,
        "profile_id": "M1",
        "config_version": quality["config_version"],
        "config_sha256": sha256_mapping(quality),
        "fixture_version": fixture["version"],
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "implementation_fingerprint": implementation_fingerprint(
            ROOT, evaluated_files(ROOT)
        ),
        "metrics": {
            "M1-01": {
                "expected_cases": total,
                "scored_cases": len(results),
                "unscored_cases": total - len(results),
                "execution_errors": sum(
                    error.startswith("execution_error:") for error in errors
                ),
            },
            "M1-02": {
                "task_success_rate": passed / total,
                "minimum_category_success_rate": min(
                    passed_by_category[category] / count
                    for category, count in by_category.items()
                ),
            },
            "M1-03": {
                "hard_constraint_violations": sum(
                    error in {"item_ids", "unsupported_stock"}
                    for error in errors
                ),
                "unsupported_price_claims": sum(
                    error.startswith(("unsupported_price:", "unsupported_currency:"))
                    for error in errors
                ),
                "unauthorized_tool_calls": sum(
                    error.startswith("unauthorized_tool:") for error in errors
                ),
            },
        },
        "category_counts": dict(by_category),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    fixture = load_fixture()
    results = [run_case(case) for case in fixture["cases"]]
    report = build_report(fixture, results)
    quality = load_structured(ROOT / "config/agent_quality_gates.yaml")
    checks = evaluate_quality_profile(quality, "M1", report)
    passed = all(item["passed"] for item in checks)
    output_dir = args.output_dir or ROOT / "artifacts/m1-evaluations" / datetime.now(
        UTC
    ).strftime("%Y%m%dT%H%M%SZ")
    output_dir.mkdir(parents=True, exist_ok=False)
    path = output_dir / "quality-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"M1 replay: {sum(item['passed'] for item in results)}/{len(results)} cases")
    print(f"Quality checks: {sum(item['passed'] for item in checks)}/{len(checks)}")
    for result in results:
        if not result["passed"]:
            print(f"FAIL {result['id']}: {[turn['errors'] for turn in result['turns']]}")
    print(path)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
