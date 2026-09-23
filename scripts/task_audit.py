"""Run two-layer, task-scoped code audits against an exact Git revision."""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

from agent_pipeline import (
    PipelineError,
    acceptance_lock_path,
    evidence_file_entries,
    git,
    implementation_fingerprint,
    is_generated_evidence_path,
    is_task_metadata_path,
    load_pipeline,
    repository_root,
    sha256_file,
    utc_now,
    validate_changed_paths,
    verify_acceptance_lock,
    write_json,
)


MAX_AUDIT_ROUNDS = 2
SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[ps]_[A-Za-z0-9]{30,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{24,}")),
)


def _run_process(
    command: str | Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    shell: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        process = subprocess.run(
            command,
            cwd=cwd,
            shell=shell,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    return {
        "command": command if isinstance(command, str) else " ".join(command),
        "exit_code": process.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "log": log_path.name,
        "passed": process.returncode == 0,
    }


def _extract_snapshot(root: Path, target_commit: str, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", target_commit],
        cwd=root,
        check=True,
        capture_output=True,
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
        bundle.extractall(destination, filter="data")


def _target_changed_paths(
    root: Path, task_id: str, baseline_commit: str, target_commit: str
) -> list[str]:
    output = git(
        root,
        "diff",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
        f"{baseline_commit}..{target_commit}",
    )
    return sorted(
        path.replace("\\", "/")
        for path in output.splitlines()
        if path
        and not is_generated_evidence_path(path)
        and not is_task_metadata_path(path, task_id)
    )


def _added_diff(root: Path, baseline_commit: str, target_commit: str) -> str:
    return git(
        root,
        "diff",
        "--unified=0",
        "--no-ext-diff",
        f"{baseline_commit}..{target_commit}",
        "--",
    )


def _changes_from_commits(
    root: Path, change_commits: Sequence[str]
) -> tuple[list[str], str]:
    """Return the union of files and patches introduced by selected commits."""

    paths: set[str] = set()
    patches: list[str] = []
    for commit in change_commits:
        paths.update(
            path.replace("\\", "/")
            for path in git(
                root,
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-r",
                commit,
            ).splitlines()
            if path and not is_generated_evidence_path(path)
        )
        patches.append(
            git(
                root,
                "show",
                "--format=",
                "--unified=0",
                "--no-ext-diff",
                commit,
                "--",
            )
        )
    return sorted(paths), "\n".join(patches)


def scan_added_secrets(diff_text: str) -> list[str]:
    """Return secret categories found only in added diff lines."""

    added = "\n".join(
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    return [name for name, pattern in SECRET_PATTERNS if pattern.search(added)]


def syntax_findings(snapshot: Path, changed_paths: Sequence[str]) -> list[str]:
    findings: list[str] = []
    for relative in changed_paths:
        path = snapshot / relative
        if not path.is_file():
            continue
        try:
            if path.suffix == ".py":
                ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            elif path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            findings.append(f"{relative}: {exc}")
    return findings


def _frozen_acceptance_findings(
    snapshot: Path, acceptance_files: dict[str, str]
) -> list[str]:
    findings: list[str] = []
    for relative, expected_hash in acceptance_files.items():
        path = snapshot / relative
        if not path.is_file():
            findings.append(f"missing frozen acceptance file: {relative}")
        elif sha256_file(path) != expected_hash:
            findings.append(f"frozen acceptance hash mismatch: {relative}")
    return findings


def _record_check(
    name: str, findings: Sequence[str], *, detail: str | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": not findings,
        "findings": list(findings),
        "detail": detail,
    }


def _run_static_layer(
    *,
    root: Path,
    snapshot: Path,
    task_id: str,
    baseline_commit: str,
    target_commit: str,
    changed_paths: list[str],
    acceptance_files: dict[str, str],
    added_diff: str,
    log_directory: Path,
) -> dict[str, Any]:
    task_config, _, _ = load_pipeline(root)
    checks: list[dict[str, Any]] = []
    checks.append(
        _record_check(
            "task_scope",
            validate_changed_paths(task_config, task_id, changed_paths),
            detail=f"{len(changed_paths)} changed files",
        )
    )
    checks.append(
        _record_check(
            "frozen_acceptance",
            _frozen_acceptance_findings(snapshot, acceptance_files),
            detail=f"{len(acceptance_files)} frozen files",
        )
    )
    checks.append(
        _record_check(
            "syntax_and_json",
            syntax_findings(snapshot, changed_paths),
        )
    )
    checks.append(
        _record_check(
            "added_secret_scan",
            scan_added_secrets(added_diff),
        )
    )

    diff_check = _run_process(
        ["git", "diff", "--check", f"{baseline_commit}..{target_commit}"],
        cwd=root,
        log_path=log_directory / "diff-check.log",
    )
    diff_check["name"] = "diff_check"
    checks.append(diff_check)

    python_paths = [
        relative
        for relative in changed_paths
        if relative.endswith(".py") and (snapshot / relative).is_file()
    ]
    if python_paths:
        ruff_check = _run_process(
            [
                "uv",
                "run",
                "--project",
                "services/ai-service",
                "ruff",
                "check",
                *python_paths,
            ],
            cwd=snapshot,
            log_path=log_directory / "ruff-check.log",
        )
        ruff_check["name"] = "ruff_check"
        checks.append(ruff_check)
        format_check = _run_process(
            [
                "uv",
                "run",
                "--project",
                "services/ai-service",
                "ruff",
                "format",
                "--check",
                *python_paths,
            ],
            cwd=snapshot,
            log_path=log_directory / "ruff-format.log",
        )
        format_check["name"] = "ruff_format"
        checks.append(format_check)

    return {
        "name": "static_and_security",
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def _apply_safe_repairs(
    *,
    root: Path,
    snapshot: Path,
    changed_paths: Sequence[str],
    log_directory: Path,
) -> list[str]:
    python_paths = [
        relative
        for relative in changed_paths
        if relative.endswith(".py") and (snapshot / relative).is_file()
    ]
    if not python_paths:
        return []

    before = {relative: sha256_file(snapshot / relative) for relative in python_paths}
    _run_process(
        [
            "uv",
            "run",
            "--project",
            "services/ai-service",
            "ruff",
            "check",
            "--fix",
            *python_paths,
        ],
        cwd=snapshot,
        log_path=log_directory / "ruff-fix.log",
    )
    _run_process(
        [
            "uv",
            "run",
            "--project",
            "services/ai-service",
            "ruff",
            "format",
            *python_paths,
        ],
        cwd=snapshot,
        log_path=log_directory / "ruff-format-fix.log",
    )
    repaired = [
        relative
        for relative in python_paths
        if sha256_file(snapshot / relative) != before[relative]
    ]
    for relative in repaired:
        destination = root / relative
        if not destination.is_file() or sha256_file(destination) != before[relative]:
            raise PipelineError(
                "safe repair refused because the current file diverged from the "
                f"audited revision: {relative}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot / relative, destination)
    return repaired


def _non_audit_worktree_changes(root: Path) -> list[str]:
    status = git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    changes: list[str] = []
    for record in status.split("\0"):
        if not record:
            continue
        relative = record[3:].replace("\\", "/")
        if not relative.startswith("artifacts/task-audits/"):
            changes.append(relative)
    return changes


def _run_behavior_layer(
    *,
    snapshot: Path,
    verification_commands: Sequence[str],
    acceptance_files: Sequence[str],
    log_directory: Path,
) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    pytest_acceptance = [
        relative
        for relative in acceptance_files
        if relative.endswith(".py")
        and Path(relative).name.startswith(("test_", "tests_"))
    ]
    if pytest_acceptance:
        commands.append(
            _run_process(
                [
                    "uv",
                    "run",
                    "--project",
                    "services/ai-service",
                    "pytest",
                    *pytest_acceptance,
                    "-q",
                ],
                cwd=snapshot,
                log_path=log_directory / "acceptance.log",
            )
        )
    for index, command in enumerate(verification_commands, 1):
        commands.append(
            _run_process(
                command,
                cwd=snapshot,
                log_path=log_directory / f"verification-{index:02d}.log",
                shell=True,
            )
        )
    return {
        "name": "acceptance_and_regression",
        "passed": bool(commands) and all(item["passed"] for item in commands),
        "commands": commands,
    }


def run_task_audit(
    root: Path,
    task_id: str,
    *,
    target_ref: str = "HEAD",
    fix: bool = False,
    max_rounds: int = MAX_AUDIT_ROUNDS,
    change_refs: Sequence[str] = (),
) -> tuple[dict[str, Any], Path]:
    if not 1 <= max_rounds <= MAX_AUDIT_ROUNDS:
        raise PipelineError(f"max_rounds must be between 1 and {MAX_AUDIT_ROUNDS}")

    task_config, _, taskbook = load_pipeline(root)
    if task_id not in taskbook:
        raise PipelineError(f"unknown task: {task_id}")
    acceptance, baseline_commit = verify_acceptance_lock(root, task_config, task_id)
    target_commit = git(root, "rev-parse", f"{target_ref}^{{commit}}")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", baseline_commit, target_commit],
        cwd=root,
    )
    if ancestor.returncode != 0:
        raise PipelineError(
            f"target {target_commit} does not descend from {baseline_commit}"
        )
    if fix:
        unrelated_changes = _non_audit_worktree_changes(root)
        if unrelated_changes:
            raise PipelineError(
                "--fix requires a clean working tree outside prior audit evidence: "
                + ", ".join(unrelated_changes)
            )

    change_commits = tuple(
        git(root, "rev-parse", f"{ref}^{{commit}}") for ref in change_refs
    )
    if change_commits:
        if target_commit not in change_commits:
            raise PipelineError("historical change refs must include the target commit")
        for commit in change_commits:
            within_range = subprocess.run(
                ["git", "merge-base", "--is-ancestor", baseline_commit, commit],
                cwd=root,
            )
            before_target = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, target_commit],
                cwd=root,
            )
            if within_range.returncode != 0 or before_target.returncode != 0:
                raise PipelineError(
                    f"historical change commit is outside the task range: {commit}"
                )
        changed_paths, added_diff = _changes_from_commits(root, change_commits)
    else:
        changed_paths = _target_changed_paths(
            root, task_id, baseline_commit, target_commit
        )
        added_diff = _added_diff(root, baseline_commit, target_commit)
    if not changed_paths:
        raise PipelineError("target has no task changes after its acceptance baseline")

    run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{target_commit[:10]}"
    run_directory = root / "artifacts" / "task-audits" / task_id / run_id
    if run_directory.exists():
        raise PipelineError(f"audit run already exists: {run_directory}")
    run_directory.mkdir(parents=True)

    rounds: list[dict[str, Any]] = []
    repaired_files: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"agent-audit-{task_id.lower()}-") as raw:
        snapshot = Path(raw)
        _extract_snapshot(root, target_commit, snapshot)
        for round_number in range(1, max_rounds + 1):
            log_directory = run_directory / f"round-{round_number}"
            log_directory.mkdir()
            static_layer = _run_static_layer(
                root=root,
                snapshot=snapshot,
                task_id=task_id,
                baseline_commit=baseline_commit,
                target_commit=target_commit,
                changed_paths=changed_paths,
                acceptance_files=acceptance["files"],
                added_diff=added_diff,
                log_directory=log_directory,
            )
            behavior_layer: dict[str, Any] | None = None
            repairs: list[str] = []
            if static_layer["passed"]:
                behavior_layer = _run_behavior_layer(
                    snapshot=snapshot,
                    verification_commands=taskbook[task_id].verification_commands,
                    acceptance_files=sorted(acceptance["files"]),
                    log_directory=log_directory,
                )
            elif fix and round_number < max_rounds:
                repairs = _apply_safe_repairs(
                    root=root,
                    snapshot=snapshot,
                    changed_paths=changed_paths,
                    log_directory=log_directory,
                )
                repaired_files.extend(repairs)

            round_passed = static_layer["passed"] and bool(
                behavior_layer and behavior_layer["passed"]
            )
            rounds.append(
                {
                    "round": round_number,
                    "passed": round_passed,
                    "layer_1": static_layer,
                    "layer_2": behavior_layer,
                    "safe_repairs": repairs,
                }
            )
            if round_passed or not repairs:
                break

    audit_passed = bool(rounds[-1]["passed"])
    result = (
        "repaired_pending_commit"
        if audit_passed and repaired_files
        else ("passed" if audit_passed else "failed")
    )
    log_paths = list(run_directory.glob("round-*/*.log"))
    fingerprint, fingerprint_paths = implementation_fingerprint(
        root, task_id, baseline_commit, target_commit
    )
    if fingerprint_paths != changed_paths:
        raise PipelineError("audit path set does not match implementation fingerprint")
    manifest = {
        "schema_version": 1,
        "task_id": task_id,
        "task_title": taskbook[task_id].title,
        "recorded_at": utc_now(),
        "target_commit": target_commit,
        "baseline_commit": baseline_commit,
        "implementation_fingerprint": fingerprint,
        "acceptance_lock_sha256": sha256_file(
            acceptance_lock_path(root, task_config, task_id)
        ),
        "changed_files": changed_paths,
        "change_commits": list(change_commits),
        "max_rounds": max_rounds,
        "rounds_executed": len(rounds),
        "safe_repairs": sorted(set(repaired_files)),
        "safe_repair_hashes": {
            relative: sha256_file(root / relative)
            for relative in sorted(set(repaired_files))
        },
        "result": result,
        "layers": rounds,
        "evidence_files": evidence_file_entries(run_directory, log_paths),
    }
    write_json(run_directory / "manifest.json", manifest)
    return manifest, run_directory


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--target-ref", default="HEAD")
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--max-rounds", type=int, default=MAX_AUDIT_ROUNDS)
    parser.add_argument(
        "--change-ref",
        action="append",
        default=[],
        help="task-owned commit for a historical interleaved audit; repeat as needed",
    )
    args = parser.parse_args(argv)

    try:
        manifest, run_directory = run_task_audit(
            repository_root(),
            args.task.upper(),
            target_ref=args.target_ref,
            fix=args.fix,
            max_rounds=args.max_rounds,
            change_refs=args.change_ref,
        )
    except (PipelineError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"Audit evidence: {run_directory.relative_to(repository_root())}")
    if manifest["result"] == "repaired_pending_commit":
        return 3
    return 0 if manifest["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
