"""Run one task's frozen checks and emit a reproducible evidence manifest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from agent_pipeline import (
    PipelineError,
    changed_files,
    evaluate_quality_profile,
    evidence_file_entries,
    git,
    is_generated_evidence_path,
    load_pipeline,
    load_structured,
    new_run_id,
    repository_root,
    run_preflight,
    utc_now,
    validate_changed_paths,
    verify_taskbook_lock,
    write_json,
)


def run_command(command: str, root: Path, log_path: Path, task_id: str) -> dict:
    started = time.monotonic()
    environment = os.environ.copy()
    environment["AGENT_TASK_ID"] = task_id
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        process = subprocess.run(
            command,
            cwd=root,
            shell=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
        )
    return {
        "command": command,
        "exit_code": process.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "log": log_path.name,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, help="task ID, for example G1")
    parser.add_argument(
        "--quality-report",
        help="machine-readable report required by F5, G5, H5 and I5",
    )
    args = parser.parse_args()
    task_id = args.task.upper()
    root = repository_root()
    run_directory: Path | None = None
    try:
        preflight = run_preflight(root, task_id, prepare=False)
        task_config, quality_config, taskbook = load_pipeline(root)
        lock = verify_taskbook_lock(root)
        paths = [
            path
            for path in changed_files(root, preflight["baseline_commit"])
            if not is_generated_evidence_path(path)
        ]
        if not paths:
            raise PipelineError(
                "no implementation changes found after the acceptance baseline"
            )
        scope_violations = validate_changed_paths(task_config, task_id, paths)
        if scope_violations:
            raise PipelineError("; ".join(scope_violations))

        run_id = new_run_id(root)
        run_directory = root / "artifacts/task-evidence" / task_id / run_id
        command_directory = run_directory / "commands"
        command_directory.mkdir(parents=True, exist_ok=False)

        command_results = []
        for index, command in enumerate(taskbook[task_id].verification_commands, 1):
            result = run_command(
                command,
                root,
                command_directory / f"{index:02d}.log",
                task_id,
            )
            command_results.append(result)

        quality_profile = task_config["tasks"][task_id].get("quality_profile")
        quality_results = []
        quality_report_copy: Path | None = None
        if quality_profile:
            if not args.quality_report:
                raise PipelineError(
                    f"task {task_id} requires --quality-report for {quality_profile}"
                )
            report_path = (root / args.quality_report).resolve()
            if not report_path.is_relative_to(root):
                raise PipelineError("quality report must be inside the repository")
            if not report_path.is_file():
                raise PipelineError(f"quality report does not exist: {report_path}")
            report = load_structured(report_path)
            quality_results = evaluate_quality_profile(
                quality_config, quality_profile, report
            )
            quality_report_copy = run_directory / "quality-report.json"
            write_json(quality_report_copy, report)

        commands_passed = all(item["exit_code"] == 0 for item in command_results)
        quality_passed = all(item["passed"] for item in quality_results)
        result = "passed" if commands_passed and quality_passed else "failed"

        evidence_paths = list(command_directory.glob("*.log"))
        if quality_report_copy is not None:
            evidence_paths.append(quality_report_copy)
        manifest = {
            "schema_version": 1,
            "task_id": task_id,
            "task_title": taskbook[task_id].title,
            "phase": task_config["tasks"][task_id]["phase"],
            "run_id": run_id,
            "recorded_at": utc_now(),
            "verification_result": result,
            "commit": git(root, "rev-parse", "HEAD"),
            "baseline_commit": preflight["baseline_commit"],
            "working_tree_dirty": bool(git(root, "status", "--porcelain=v1")),
            "taskbook_sha256": lock["taskbook_sha256"],
            "task_config_sha256": lock["task_config_sha256"],
            "quality_gates_sha256": lock["quality_gates_sha256"],
            "acceptance_lock_sha256": preflight["acceptance_lock_sha256"],
            "acceptance_files": preflight["acceptance_files"],
            "changed_files": paths,
            "commands": command_results,
            "quality_profile": quality_profile,
            "quality_results": quality_results,
            "runner": {
                "ci": os.environ.get("CI", "").lower() == "true",
                "verifier_id": os.environ.get("AGENT_VERIFIER_ID"),
            },
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
