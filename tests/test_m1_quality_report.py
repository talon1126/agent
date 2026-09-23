"""The M1 quality report must account for every frozen scenario."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_pipeline import evaluate_quality_profile, load_structured, sha256_mapping  # noqa: E402
from build_m1_quality_report import _fixture_hash_matches, summarize  # noqa: E402


FINGERPRINT = "a" * 64


def _case(test_id: str, verdict: str, *, multi_turn: bool) -> tuple[dict, dict]:
    item = {"testId": test_id, "verdict": verdict}
    guard_evidence = [{"type": "runtime_fingerprint", "sha256": FINGERPRINT}]
    if multi_turn:
        evidence = {
            "metrics": {
                "talonmart_contract_guard": {
                    "passed": True,
                    "outcomes": [{"evidence": guard_evidence}],
                },
                "talonmart_abcd_quality": {"score": 0.95},
            }
        }
    else:
        evidence = {
            "metricResults": [
                {
                    "metricKey": "talonmart_contract_guard",
                    "passed": True,
                    "evidence": guard_evidence,
                },
                {"metricKey": "talonmart_abcd_quality", "score": 0.95},
            ]
        }
    return item, {**item, "evidence": evidence}


def _run(run_id: str, cases: list[tuple[dict, dict]]) -> dict:
    return {
        "summary": {"runId": run_id, "status": "COMPLETED"},
        "items": [item for item, _ in cases],
        "details": [detail for _, detail in cases],
    }


def _inputs() -> tuple[dict, dict, dict, dict]:
    fixture = {
        "scenarios": [
            {"scenario_id": "compare", "category": "comparison", "turns": [{"role": "user"}]},
            {"scenario_id": "review", "category": "review_summary", "turns": [{"role": "user"}]},
            {"scenario_id": "multi", "category": "hard_constraint", "turns": [{"role": "user"}, {"role": "user"}]},
        ]
    }
    manifest = {
        "runs": {"single_turn": "single", "multi_turn": "multi"},
        "test_id_to_scenario": {"t1": "compare", "t2": "review", "t3": "multi"},
        "deferred_scenarios": [],
        "implementation_fingerprint": FINGERPRINT,
        "target_runtime_fingerprint": FINGERPRINT,
        "git_worktree_dirty": False,
    }
    single = _run("single", [_case("t1", "PASS", multi_turn=False), _case("t2", "PASS", multi_turn=False)])
    multi = _run("multi", [_case("t3", "PASS", multi_turn=True)])
    return fixture, manifest, single, multi


def test_complete_run_satisfies_m1_metrics() -> None:
    fixture, manifest, single, multi = _inputs()
    metrics = summarize(
        fixture, manifest, single, multi, source_commit_matches=True
    )
    config = load_structured(ROOT / "config/agent_quality_gates.yaml")
    report = {
        "schema_version": 1,
        "profile_id": "D",
        "config_version": config["config_version"],
        "config_sha256": sha256_mapping(config),
        "metrics": metrics,
    }

    assert all(item["passed"] for item in evaluate_quality_profile(config, "D", report))


def test_deferred_and_not_evaluated_cases_cannot_pass_coverage() -> None:
    fixture, manifest, single, multi = _inputs()
    manifest["deferred_scenarios"] = [{"scenario_id": "review"}]
    del manifest["test_id_to_scenario"]["t2"]
    single["items"].pop()
    single["details"].pop()
    multi["items"][0]["verdict"] = "NOT_EVALUATED"
    multi["details"][0]["verdict"] = "NOT_EVALUATED"

    metrics = summarize(
        fixture, manifest, single, multi, source_commit_matches=False
    )

    assert metrics["M1-01"]["graded_coverage"] == pytest.approx(1 / 3)
    assert metrics["M1-03"]["multi_turn_success_rate"] == 0
    assert metrics["M1-05"]["review_summary_success_rate"] == 0
    assert metrics["M1-07"]["runtime_fingerprint_match"] == 0


def test_manifest_fingerprint_requires_matching_target_metric_evidence() -> None:
    fixture, manifest, single, multi = _inputs()
    single["details"][0]["evidence"]["metricResults"][0]["evidence"] = []

    metrics = summarize(
        fixture, manifest, single, multi, source_commit_matches=True
    )

    assert metrics["M1-07"]["runtime_fingerprint_match"] == 0


def test_mixed_target_runtime_fingerprints_fail_lineage() -> None:
    fixture, manifest, single, multi = _inputs()
    multi["details"][0]["evidence"]["metrics"]["talonmart_contract_guard"]["outcomes"][0]["evidence"][0]["sha256"] = "b" * 64

    metrics = summarize(
        fixture, manifest, single, multi, source_commit_matches=True
    )

    assert metrics["M1-07"]["runtime_fingerprint_match"] == 0


def test_unknown_or_duplicate_cases_are_rejected() -> None:
    fixture, manifest, single, multi = _inputs()
    single["items"].append({"testId": "t1", "verdict": "PASS"})
    with pytest.raises(ValueError, match="duplicated"):
        summarize(fixture, manifest, single, multi, source_commit_matches=True)


def test_fixture_hash_allows_only_line_ending_conversion() -> None:
    import hashlib

    content = b'{"key": 1}\n'
    expected = hashlib.sha256(content).hexdigest()

    assert _fixture_hash_matches(content.replace(b"\n", b"\r\n"), expected)
    assert not _fixture_hash_matches(b'{"key": 2}\r\n', expected)
