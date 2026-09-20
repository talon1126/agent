"""Verify task evidence and quality profiles for one phase milestone."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_pipeline import (
    PipelineError,
    evaluate_quality_profile,
    evidence_file_entries,
    load_pipeline,
    load_structured,
    new_run_id,
    repository_root,
    utc_now,
    verify_milestone_dependency,
    verify_task_dependency,
    verify_taskbook_lock,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--milestone", required=True, help="A, M3, M4, M6-A, etc.")
    parser.add_argument(
        "--quality-report",
        help="quality report for a milestone without a task-level quality profile",
    )
    args = parser.parse_args()
    milestone_id = args.milestone.upper()
    root = repository_root()
    try:
        _, quality_config, _ = load_pipeline(root)
        lock = verify_taskbook_lock(root)
        if milestone_id not in quality_config["milestones"]:
            raise PipelineError(f"unknown milestone: {milestone_id}")
        milestone = quality_config["milestones"][milestone_id]

        task_evidence = {}
        for task_id in milestone.get("required_tasks", []):
            manifest = verify_task_dependency(root, task_id)
            task_evidence[task_id] = manifest["run_id"]
        milestone_evidence = {}
        for prerequisite in milestone.get("required_milestones", []):
            manifest = verify_milestone_dependency(root, prerequisite)
            milestone_evidence[prerequisite] = manifest["run_id"]

        quality_results = []
        evidence_paths: list[Path] = []
        report_copy: Path | None = None
        metric_ids = milestone.get("metric_ids", [])
        if metric_ids:
            gate_task = milestone.get("gate_task")
            gate_manifest = (
                verify_task_dependency(root, gate_task) if gate_task else None
            )
            if gate_manifest and gate_manifest.get("quality_profile") == milestone_id:
                quality_results = gate_manifest.get("quality_results", [])
            else:
                if not args.quality_report:
                    raise PipelineError(
                        f"milestone {milestone_id} requires --quality-report"
                    )
                report_path = (root / args.quality_report).resolve()
                if not report_path.is_relative_to(root):
                    raise PipelineError("quality report must be inside the repository")
                report = load_structured(report_path)
                quality_results = evaluate_quality_profile(
                    quality_config, milestone_id, report
                )
            if not quality_results or not all(
                item.get("passed") for item in quality_results
            ):
                raise PipelineError(f"quality checks failed for {milestone_id}")

        run_id = new_run_id(root)
        run_directory = root / "artifacts/phase-evidence" / milestone_id / run_id
        run_directory.mkdir(parents=True, exist_ok=False)
        if metric_ids and args.quality_report:
            report_path = (root / args.quality_report).resolve()
            if not report_path.is_relative_to(root):
                raise PipelineError("quality report must be inside the repository")
            report = load_structured(report_path)
            report_copy = run_directory / "quality-report.json"
            write_json(report_copy, report)
            evidence_paths.append(report_copy)

        manifest = {
            "schema_version": 1,
            "milestone_id": milestone_id,
            "recorded_at": utc_now(),
            "run_id": run_id,
            "verification_result": "passed",
            "taskbook_sha256": lock["taskbook_sha256"],
            "task_config_sha256": lock["task_config_sha256"],
            "quality_gates_sha256": lock["quality_gates_sha256"],
            "task_evidence": task_evidence,
            "milestone_evidence": milestone_evidence,
            "quality_results": quality_results,
            "evidence_files": evidence_file_entries(run_directory, evidence_paths),
        }
        write_json(run_directory / "manifest.json", manifest)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"Evidence: {run_directory.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
