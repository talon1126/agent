import copy
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_shopping_agent_scenarios import (  # noqa: E402
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_ITEMS_PATH,
    DEFAULT_REPORT_PATH,
    DEFAULT_SCENARIOS_PATH,
    ScenarioValidationError,
    coverage_summary,
    main,
    render_coverage_report,
    validate_scenario_document,
)


def _raw_document() -> dict:
    return json.loads(DEFAULT_SCENARIOS_PATH.read_text(encoding="utf-8"))


def _validate(raw_document: dict):
    return validate_scenario_document(
        raw_document,
        items_path=DEFAULT_ITEMS_PATH,
        categories_path=DEFAULT_CATEGORIES_PATH,
    )


def _run_cli_with_payload(tmp_path: Path, payload: dict) -> int:
    input_path = tmp_path / "scenarios.json"
    input_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return main(
        [
            "--input",
            str(input_path),
            "--report",
            str(tmp_path / "coverage.md"),
        ]
    )


def test_scenario_document_validates_canonical_fixture() -> None:
    document = _validate(_raw_document())
    summary = coverage_summary(document)

    assert summary["total_scenarios"] == 40
    assert summary["multi_turn"] >= 10
    assert all(count >= 4 for count in summary["categories"].values())


def test_validator_rejects_duplicate_scenario_ids(tmp_path: Path) -> None:
    payload = _raw_document()
    payload["scenarios"][1]["scenario_id"] = payload["scenarios"][0]["scenario_id"]

    with pytest.raises(ValidationError, match="globally unique"):
        _validate(payload)
    assert _run_cli_with_payload(tmp_path, payload) == 2


def test_validator_rejects_missing_required_field(tmp_path: Path) -> None:
    payload = _raw_document()
    payload["scenarios"][0].pop("hard_assertions")

    with pytest.raises(ValidationError, match="hard_assertions"):
        _validate(payload)
    assert _run_cli_with_payload(tmp_path, payload) == 2


def test_validator_rejects_invalid_response_type(tmp_path: Path) -> None:
    payload = _raw_document()
    payload["scenarios"][0]["expected_response_type"] = "sounds_good"

    with pytest.raises(ValidationError, match="expected_response_type"):
        _validate(payload)
    assert _run_cli_with_payload(tmp_path, payload) == 2


def test_validator_rejects_unknown_item_reference(tmp_path: Path) -> None:
    payload = _raw_document()
    payload["scenarios"][0]["page_context"]["current_item_id"] = "item_missing"

    with pytest.raises(ScenarioValidationError, match="item_missing"):
        _validate(payload)
    assert _run_cli_with_payload(tmp_path, payload) == 2


def test_coverage_report_matches_generated_output() -> None:
    document = _validate(_raw_document())

    assert render_coverage_report(document) == DEFAULT_REPORT_PATH.read_text(
        encoding="utf-8"
    )
    assert main(["--check-report"]) == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload["scenarios"].pop(), "at least 40"),
        (
            lambda payload: payload["scenarios"][0]["required_tools"].append(
                payload["scenarios"][0]["forbidden_tools"][0]
            ),
            "must be disjoint",
        ),
        (
            lambda payload: payload["scenarios"][0]["turns"][0].update(
                {"content": "联系邮箱 private@example.com"}
            ),
            "sensitive-looking",
        ),
        (
            lambda payload: payload["scenarios"][-1].update({"failure_mode": "none"}),
            "failure_mode for tool_failure",
        ),
    ],
)
def test_validator_rejects_invalid_document_invariants(mutation, message) -> None:
    payload = copy.deepcopy(_raw_document())
    mutation(payload)

    with pytest.raises((ValidationError, ScenarioValidationError), match=message):
        _validate(payload)
