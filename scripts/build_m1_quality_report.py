"""Build the D/M1 quality report from one complete Kayn evaluation run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from agent_pipeline import load_structured, sha256_mapping


GUARD_KEY = "talonmart_contract_guard"
JUDGE_KEY = "talonmart_abcd_quality"
GRADED_VERDICTS = {"PASS", "FAIL"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise ValueError("the frozen set must contain each required scenario group")
    return numerator / denominator


def _guard_passed(detail: dict[str, Any], *, multi_turn: bool) -> bool:
    evidence = detail.get("evidence") or {}
    if multi_turn:
        result = (evidence.get("metrics") or {}).get(GUARD_KEY) or {}
        return result.get("passed") is True and not any(
            item.get("error") for item in result.get("outcomes") or []
        )
    results = evidence.get("metricResults") or []
    matches = [item for item in results if item.get("metricKey") == GUARD_KEY]
    return (
        len(matches) == 1
        and matches[0].get("passed") is True
        and not matches[0].get("error")
    )


def _runtime_fingerprint(detail: dict[str, Any], *, multi_turn: bool) -> str | None:
    evidence = detail.get("evidence") or {}
    if multi_turn:
        metric = (evidence.get("metrics") or {}).get(GUARD_KEY) or {}
        outcomes = metric.get("outcomes") or []
        if not outcomes or any(item.get("error") for item in outcomes):
            return None
    else:
        outcomes = [
            item
            for item in evidence.get("metricResults") or []
            if item.get("metricKey") == GUARD_KEY
        ]
        if len(outcomes) != 1 or outcomes[0].get("error"):
            return None
    fingerprints = [
        item.get("sha256")
        for outcome in outcomes
        for item in outcome.get("evidence") or []
        if isinstance(item, dict) and item.get("type") == "runtime_fingerprint"
    ]
    if (
        len(fingerprints) != len(outcomes)
        or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in fingerprints)
        or len(set(fingerprints)) != 1
    ):
        return None
    return fingerprints[0]


def _judge_scored(detail: dict[str, Any], *, multi_turn: bool) -> bool:
    evidence = detail.get("evidence") or {}
    if multi_turn:
        result = (evidence.get("metrics") or {}).get(JUDGE_KEY) or {}
        return isinstance(result.get("score"), int | float) and not any(
            item.get("error") for item in result.get("outcomes") or []
        )
    results = evidence.get("metricResults") or []
    matches = [item for item in results if item.get("metricKey") == JUDGE_KEY]
    return (
        len(matches) == 1
        and isinstance(matches[0].get("score"), int | float)
        and not matches[0].get("error")
    )


def _fixture_hash_matches(content: bytes, expected: str) -> bool:
    variants = {content, content.replace(b"\r\n", b"\n")}
    variants.add(content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    return any(hashlib.sha256(item).hexdigest() == expected for item in variants)


def _source_commit_matches(root: Path, manifest: dict[str, Any]) -> bool:
    if manifest.get("git_worktree_dirty") is not False:
        return False
    commit = manifest.get("git_commit")
    paths = manifest.get("evaluated_files")
    if not isinstance(commit, str) or not isinstance(paths, list) or not paths:
        return False
    if any(
        not isinstance(path, str)
        or not path
        or not (root / path).resolve().is_relative_to(root)
        for path in paths
    ):
        return False
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=root
    )
    if ancestor.returncode != 0:
        return False
    comparison = subprocess.run(
        ["git", "diff", "--quiet", commit, "HEAD", "--", *paths], cwd=root
    )
    return comparison.returncode == 0


def summarize(
    fixture: dict[str, Any],
    manifest: dict[str, Any],
    single: dict[str, Any],
    multi: dict[str, Any],
    *,
    source_commit_matches: bool,
) -> dict[str, Any]:
    scenarios = {item["scenario_id"]: item for item in fixture["scenarios"]}
    if len(scenarios) != len(fixture["scenarios"]):
        raise ValueError("duplicate scenario_id in frozen set")
    test_map = manifest["test_id_to_scenario"]
    if len(set(test_map.values())) != len(test_map.values()):
        raise ValueError("multiple test IDs map to one scenario")
    if not set(test_map.values()).issubset(scenarios):
        raise ValueError("run contains scenarios outside the frozen set")

    outcomes: dict[str, tuple[str, bool, bool, str | None]] = {}
    for name, run in (("single_turn", single), ("multi_turn", multi)):
        summary = run["summary"]
        if summary.get("runId") != manifest["runs"][name]:
            raise ValueError(f"{name} run ID does not match the manifest")
        if summary.get("status") != "COMPLETED":
            raise ValueError(f"{name} Kayn run did not complete")
        items = run.get("items") or []
        if len({str(item["testId"]) for item in items}) != len(items):
            raise ValueError(f"{name} contains duplicated test IDs")
        details = {str(item["testId"]): item for item in run.get("details") or []}
        if len(details) != len(run.get("details") or []) or len(details) != len(items):
            raise ValueError(f"{name} results lack unique case details")
        for item in items:
            test_id = str(item["testId"])
            if test_id not in test_map or test_id not in details:
                raise ValueError(f"{name} result is not mapped to one frozen case")
            scenario_id = test_map[test_id]
            scenario = scenarios[scenario_id]
            is_multi = len(scenario["turns"]) > 1
            if is_multi != (name == "multi_turn") or scenario_id in outcomes:
                raise ValueError(f"{scenario_id} is duplicated or in the wrong run")
            verdict = str(item.get("verdict") or "")
            if details[test_id].get("verdict") != verdict:
                raise ValueError(f"{scenario_id} detail verdict differs from summary")
            outcomes[scenario_id] = (
                verdict,
                _guard_passed(details[test_id], multi_turn=is_multi),
                _judge_scored(details[test_id], multi_turn=is_multi),
                _runtime_fingerprint(details[test_id], multi_turn=is_multi),
            )

    deferred = {item["scenario_id"] for item in manifest.get("deferred_scenarios", [])}
    if not deferred.issubset(scenarios) or deferred & outcomes.keys():
        raise ValueError("deferred scenarios are unknown or have results")
    if set(scenarios) != outcomes.keys() | deferred:
        raise ValueError("some frozen scenarios are neither evaluated nor deferred")

    total = len(scenarios)
    graded = sum(
        verdict in GRADED_VERDICTS and judged
        for verdict, _, judged, _ in outcomes.values()
    )
    passed = sum(
        verdict == "PASS" and judged for verdict, _, judged, _ in outcomes.values()
    )
    guarded = sum(guard for _, guard, _, _ in outcomes.values())
    groups = {
        "multi_turn": {sid for sid, item in scenarios.items() if len(item["turns"]) > 1},
        "comparison": {sid for sid, item in scenarios.items() if item["category"] == "comparison"},
        "review_summary": {sid for sid, item in scenarios.items() if item["category"] == "review_summary"},
        "hard_constraint": {sid for sid, item in scenarios.items() if item["category"] == "hard_constraint"},
    }
    group_counts = {
        name: {
            "passed": sum(
                outcomes.get(sid, (None, False, False, None))[0] == "PASS"
                and outcomes[sid][2]
                for sid in ids
            ),
            "total": len(ids),
        }
        for name, ids in groups.items()
    }
    lineage = (
        isinstance(manifest.get("implementation_fingerprint"), str)
        and _SHA256.fullmatch(manifest["implementation_fingerprint"])
        and manifest.get("target_runtime_fingerprint")
        == manifest.get("implementation_fingerprint")
        and len(outcomes) == total
        and all(
            fingerprint == manifest["implementation_fingerprint"]
            for _, _, _, fingerprint in outcomes.values()
        )
        and source_commit_matches
    )
    return {
        "M1-01": {"graded_coverage": _ratio(graded, total), "graded": graded, "total": total},
        "M1-02": {"task_success_rate": _ratio(passed, total), "passed": passed, "total": total},
        "M1-03": {"multi_turn_success_rate": _ratio(group_counts["multi_turn"]["passed"], group_counts["multi_turn"]["total"]), **group_counts["multi_turn"]},
        "M1-04": {"comparison_success_rate": _ratio(group_counts["comparison"]["passed"], group_counts["comparison"]["total"]), **group_counts["comparison"]},
        "M1-05": {"review_summary_success_rate": _ratio(group_counts["review_summary"]["passed"], group_counts["review_summary"]["total"]), **group_counts["review_summary"]},
        "M1-06": {"tool_contract_pass_rate": _ratio(guarded, total), "passed": guarded, "total": total},
        "M1-07": {"runtime_fingerprint_match": int(lineage)},
        "M1-08": {"hard_constraint_success_rate": _ratio(group_counts["hard_constraint"]["passed"], group_counts["hard_constraint"]["total"]), **group_counts["hard_constraint"]},
    }


def build_report(root: Path, run_dir: Path) -> dict[str, Any]:
    manifest = load_structured(run_dir / "manifest.json")
    fixture_path = (root / manifest["fixture"]).resolve()
    if not fixture_path.is_relative_to(root) or not fixture_path.is_file():
        raise ValueError("frozen fixture must be inside the repository")
    fixture_bytes = fixture_path.read_bytes()
    if not _fixture_hash_matches(fixture_bytes, manifest["fixture_sha256"]):
        raise ValueError("frozen fixture hash differs from the evaluated run")
    fixture = json.loads(fixture_bytes)
    if fixture["metadata"]["version"] != manifest["fixture_version"]:
        raise ValueError("frozen fixture version differs from the evaluated run")
    quality_config = load_structured(root / "config/agent_quality_gates.yaml")
    return {
        "schema_version": 1,
        "profile_id": "D",
        "config_version": quality_config["config_version"],
        "config_sha256": sha256_mapping(quality_config),
        "source": {
            "run_ids": manifest["runs"],
            "fixture_sha256": manifest["fixture_sha256"],
            "implementation_fingerprint": manifest.get("implementation_fingerprint"),
            "target_runtime_fingerprint": manifest.get("target_runtime_fingerprint"),
        },
        "metrics": summarize(
            fixture,
            manifest,
            load_structured(run_dir / "single-turn-results.json"),
            load_structured(run_dir / "multi-turn-results.json"),
            source_commit_matches=_source_commit_matches(root, manifest),
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--check-report", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = build_report(root, args.run_dir.resolve())
    output = (args.output or args.check_report).resolve()
    if not output.is_relative_to(root):
        raise ValueError("quality report output must be inside the repository")
    if args.check_report:
        if load_structured(output) != report:
            raise ValueError("quality report differs from the Kayn source results")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report["metrics"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
