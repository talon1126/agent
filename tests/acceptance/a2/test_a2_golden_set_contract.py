"""Frozen acceptance contract for task A2."""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_PATH = ROOT / "fixtures" / "evals" / "shopping_agent_scenarios.json"
ITEMS_PATH = ROOT / "fixtures" / "data" / "items.json"
VALIDATOR_PATH = ROOT / "scripts" / "validate_shopping_agent_scenarios.py"
TEST_PATH = (
    ROOT / "services" / "ai-service" / "tests" / "test_shopping_agent_scenarios.py"
)
REPORT_PATH = ROOT / "docs" / "shopping_agent_scenario_coverage.md"

REQUIRED_FIELDS = {
    "scenario_id",
    "category",
    "turns",
    "page_context",
    "expected_goal",
    "required_tools",
    "forbidden_tools",
    "expected_response_type",
    "hard_assertions",
    "tags",
    "failure_mode",
}
CORE_CATEGORIES = {
    "vague_need",
    "hard_constraint",
    "comparison",
    "review_summary",
    "constraint_conflict",
    "no_candidates",
    "tool_failure",
}
REQUIRED_TOPIC_TAGS = {
    "vague_need",
    "budget_boundary",
    "brand_preference",
    "brand_exclusion",
    "specification",
    "delivery_deadline",
    "comparison",
    "review_summary",
    "constraint_conflict",
    "no_candidates",
    "tool_timeout",
    "multi_turn",
    "modify_budget",
    "withdraw_preference",
    "add_exclusion",
}
REQUIRED_VALIDATOR_TESTS = {
    "test_scenario_document_validates_canonical_fixture",
    "test_validator_rejects_duplicate_scenario_ids",
    "test_validator_rejects_missing_required_field",
    "test_validator_rejects_invalid_response_type",
    "test_validator_rejects_unknown_item_reference",
    "test_coverage_report_matches_generated_output",
}


def _load_document() -> dict[str, Any]:
    assert SCENARIOS_PATH.is_file(), f"missing A2 fixture: {SCENARIOS_PATH}"
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def _item_references(value: Any, parent_key: str = "") -> list[str]:
    references: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"item_id", "current_item_id"} and child is not None:
                references.append(str(child))
            elif key in {"item_ids", "candidate_item_ids"}:
                references.extend(str(item) for item in child)
            else:
                references.extend(_item_references(child, key))
    elif isinstance(value, list):
        for child in value:
            references.extend(_item_references(child, parent_key))
    return references


def test_golden_set_has_complete_machine_readable_scenarios() -> None:
    document = _load_document()
    scenarios = document["scenarios"]

    assert document["schema_version"] == 1
    assert len(scenarios) >= 40
    assert len({scenario["scenario_id"] for scenario in scenarios}) == len(scenarios)
    for scenario in scenarios:
        assert REQUIRED_FIELDS <= set(scenario), scenario.get("scenario_id")
        assert scenario["turns"], scenario["scenario_id"]
        assert scenario["hard_assertions"], scenario["scenario_id"]
        for assertion in scenario["hard_assertions"]:
            assert {"assertion_id", "target", "operator", "expected"} <= set(
                assertion
            ), scenario["scenario_id"]
            assert assertion["operator"] in {
                "eq",
                "ne",
                "lte",
                "gte",
                "in",
                "not_in",
                "contains",
                "exists",
            }
        serialized_assertions = json.dumps(
            scenario["hard_assertions"], ensure_ascii=False
        )
        assert "回答合理" not in serialized_assertions
        assert "表现良好" not in serialized_assertions


def test_golden_set_meets_category_tag_and_multi_turn_coverage() -> None:
    scenarios = _load_document()["scenarios"]
    category_counts = Counter(scenario["category"] for scenario in scenarios)
    all_tags = {tag for scenario in scenarios for tag in scenario["tags"]}
    multi_turn = [scenario for scenario in scenarios if len(scenario["turns"]) > 1]

    assert CORE_CATEGORIES <= set(category_counts)
    assert all(category_counts[category] >= 4 for category in CORE_CATEGORIES)
    assert REQUIRED_TOPIC_TAGS <= all_tags
    assert len(multi_turn) >= 10
    assert all("multi_turn" in scenario["tags"] for scenario in multi_turn)
    for change_tag in ("modify_budget", "withdraw_preference", "add_exclusion"):
        assert any(change_tag in scenario["tags"] for scenario in multi_turn)


def test_every_fixture_item_reference_resolves_to_catalog() -> None:
    catalog_ids = {
        item["item_id"]
        for item in json.loads(ITEMS_PATH.read_text(encoding="utf-8"))
    }
    references = _item_references(_load_document()["scenarios"])

    assert references
    assert set(references) <= catalog_ids


def test_validator_and_regression_suite_expose_required_contracts() -> None:
    assert VALIDATOR_PATH.is_file(), f"missing A2 validator: {VALIDATOR_PATH}"
    assert TEST_PATH.is_file(), f"missing A2 tests: {TEST_PATH}"
    validator_tree = ast.parse(VALIDATOR_PATH.read_text(encoding="utf-8"))
    test_tree = ast.parse(TEST_PATH.read_text(encoding="utf-8"))
    validator_names = {
        node.name
        for node in ast.walk(validator_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    test_names = {
        node.name
        for node in ast.walk(test_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert {
        "ShoppingScenarioDocument",
        "validate_scenario_document",
        "render_coverage_report",
        "main",
    } <= validator_names
    assert REQUIRED_VALIDATOR_TESTS <= test_names


def test_coverage_report_is_marked_generated_and_exposes_all_dimensions() -> None:
    assert REPORT_PATH.is_file(), f"missing A2 report: {REPORT_PATH}"
    report = REPORT_PATH.read_text(encoding="utf-8")

    for token in (
        "<!-- generated by scripts/validate_shopping_agent_scenarios.py -->",
        "Total scenarios",
        "## Category Coverage",
        "## Tag Coverage",
        "## Response Type Coverage",
        "## Failure Mode Coverage",
        "## Multi-turn Coverage",
    ):
        assert token in report
