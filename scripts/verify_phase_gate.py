"""Verify task evidence and quality profiles for one phase milestone."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from agent_pipeline import (
    PipelineError,
    evaluate_quality_profile,
    evidence_file_entries,
    extract_git_snapshot,
    git,
    latest_manifest,
    load_pipeline,
    load_structured,
    new_run_id,
    repository_root,
    runner_metadata,
    utc_now,
    validate_evidence_manifest,
    verify_milestone_dependency,
    verify_acceptance_lock,
    verify_task_audit,
    verify_taskbook_lock,
    write_json,
)
from task_verify import run_command


def build_stage_verification_commands(
    root: Path,
    task_config: dict[str, Any],
    taskbook: dict[str, Any],
    task_ids: list[str],
) -> list[dict[str, str]]:
    """Build current-revision acceptance and task checks for a stage."""

    commands: list[dict[str, str]] = []
    for task_id in task_ids:
        acceptance, _ = verify_acceptance_lock(root, task_config, task_id)
        pytest_files = sorted(
            relative
            for relative in acceptance["files"]
            if relative.endswith(".py")
            and Path(relative).name.startswith(("test_", "tests_"))
        )
        if pytest_files:
            commands.append(
                {
                    "task_id": task_id,
                    "kind": "frozen_acceptance",
                    "command": "uv run --project services/ai-service pytest "
                    + " ".join(pytest_files)
                    + " -q",
                }
            )
        for command in taskbook[task_id].verification_commands:
            commands.append(
                {
                    "task_id": task_id,
                    "kind": "task_verification",
                    "command": command,
                }
            )
    return commands


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
    run_directory: Path | None = None
    try:
        runner = runner_metadata(require_independent=True)
        task_config, quality_config, taskbook = load_pipeline(root)
        lock = verify_taskbook_lock(root)
        if milestone_id not in quality_config["milestones"]:
            raise PipelineError(f"unknown milestone: {milestone_id}")
        milestone = quality_config["milestones"][milestone_id]
        target_commit = git(root, "rev-parse", "HEAD")
        worktree_dirty = bool(
            git(root, "status", "--porcelain=v1", "--untracked-files=all")
        )

        task_evidence = {}
        for task_id in milestone.get("required_tasks", []):
            verify_task_audit(root, task_id)
            manifest_path = latest_manifest(root, "task-evidence", task_id)
            if manifest_path is None:
                raise PipelineError(f"task {task_id} has no historical evidence")
            manifest = validate_evidence_manifest(
                root,
                manifest_path,
                task_id,
                "task_id",
                allow_quality_gate_drift=True,
            )
            task_evidence[task_id] = manifest["run_id"]
        milestone_evidence = {}
        for prerequisite in milestone.get("required_milestones", []):
            manifest = verify_milestone_dependency(root, prerequisite)
            milestone_evidence[prerequisite] = manifest["run_id"]

        run_id = new_run_id(root)
        run_directory = root / "artifacts/phase-evidence" / milestone_id / run_id
        command_directory = run_directory / "commands"
        command_directory.mkdir(parents=True, exist_ok=False)

        command_specs = build_stage_verification_commands(
            root,
            task_config,
            taskbook,
            list(milestone.get("required_tasks", [])),
        )
        command_results: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(
            prefix=f"agent-phase-{milestone_id.lower()}-"
        ) as raw:
            snapshot = Path(raw)
            extract_git_snapshot(root, target_commit, snapshot)
            for index, spec in enumerate(command_specs, 1):
                result = run_command(
                    spec["command"],
                    snapshot,
                    command_directory / f"{index:02d}-{spec['task_id'].lower()}.log",
                    spec["task_id"],
                )
                result.update({"task_id": spec["task_id"], "kind": spec["kind"]})
                command_results.append(result)

        quality_results = []
        evidence_paths: list[Path] = list(command_directory.glob("*.log"))
        report_copy: Path | None = None
        metric_ids = milestone.get("metric_ids", [])
        if metric_ids:
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

        if metric_ids and args.quality_report:
            report_path = (root / args.quality_report).resolve()
            if not report_path.is_relative_to(root):
                raise PipelineError("quality report must be inside the repository")
            report = load_structured(report_path)
            report_copy = run_directory / "quality-report.json"
            write_json(report_copy, report)
            evidence_paths.append(report_copy)

        commands_passed = bool(command_results) and all(
            item["exit_code"] == 0 for item in command_results
        )
        verification_result = "passed" if commands_passed else "failed"
        manifest = {
            "schema_version": 1,
            "milestone_id": milestone_id,
            "recorded_at": utc_now(),
            "run_id": run_id,
            "verification_result": verification_result,
            "commit": target_commit,
            "working_tree_dirty": worktree_dirty,
            "verification_workspace": "git_archive",
            "runner": runner,
            "taskbook_sha256": lock["taskbook_sha256"],
            "task_config_sha256": lock["task_config_sha256"],
            "quality_gates_sha256": lock["quality_gates_sha256"],
            "task_evidence": task_evidence,
            "milestone_evidence": milestone_evidence,
            "commands": command_results,
            "quality_results": quality_results,
            "evidence_files": evidence_file_entries(run_directory, evidence_paths),
        }
        write_json(run_directory / "manifest.json", manifest)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"Evidence: {run_directory.relative_to(root)}")
    return 0 if manifest["verification_result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
