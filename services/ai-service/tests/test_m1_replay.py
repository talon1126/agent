"""Exercise every frozen M1 case and its machine-readable gate report."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_pipeline import evaluate_quality_profile, load_structured  # noqa: E402
from run_m1_replay import build_report, load_fixture, run_case  # noqa: E402


def test_frozen_m1_replay_and_quality_gate() -> None:
    fixture = load_fixture()
    results = [run_case(case) for case in fixture["cases"]]
    report = build_report(fixture, results)
    config = load_structured(ROOT / "config/agent_quality_gates.yaml")
    checks = evaluate_quality_profile(config, "M1", report)

    assert len(results) == len(fixture["cases"])
    assert all(result["passed"] for result in results), results
    assert all(check["passed"] for check in checks), checks


def test_m1_gate_rejects_an_unscored_case() -> None:
    fixture = load_fixture()
    results = [run_case(case) for case in fixture["cases"][:-1]]
    report = build_report(fixture, results)
    config = load_structured(ROOT / "config/agent_quality_gates.yaml")
    checks = evaluate_quality_profile(config, "M1", report)

    assert report["metrics"]["M1-01"]["unscored_cases"] == 1
    assert not all(check["passed"] for check in checks)
