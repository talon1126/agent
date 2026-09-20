"""Shared primitives for the taskbook-driven Agent development pipeline."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
TASK_HEADING = re.compile(r"^###\s+([A-I][1-5])：(.+?)\s*$", re.MULTILINE)
VERIFY_BLOCK = re.compile(
    r"\*\*验证命令\*\*\s*```(?:powershell|bash|shell)?\s*\n(.*?)```",
    re.DOTALL,
)


class PipelineError(RuntimeError):
    """A deterministic pipeline validation failure."""


@dataclass(frozen=True)
class TaskSection:
    task_id: str
    title: str
    body: str
    verification_commands: tuple[str, ...]

    @property
    def section_sha256(self) -> str:
        return sha256_bytes(self.body.encode("utf-8"))


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" not in raw:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            canonical = text.replace("\r\n", "\n").replace("\r", "\n")
            raw = canonical.encode("utf-8")
    return sha256_bytes(raw)


def load_structured(path: Path) -> dict[str, Any]:
    """Load JSON-compatible YAML without requiring a repository-level dependency."""

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PipelineError(f"required configuration is missing: {path}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PipelineError(
                f"{path} is not JSON-compatible YAML and PyYAML is not installed"
            ) from exc
        data = yaml.safe_load(raw)

    if not isinstance(data, dict):
        raise PipelineError(f"{path} must contain a mapping at its root")
    return data


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def parse_taskbook(path: Path) -> dict[str, TaskSection]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PipelineError(f"taskbook is missing: {path}") from exc

    headings = list(TASK_HEADING.finditer(text))
    tasks: dict[str, TaskSection] = {}
    for index, heading in enumerate(headings):
        start = heading.start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[start:end].rstrip() + "\n"
        command_block = VERIFY_BLOCK.search(body)
        if command_block is None:
            raise PipelineError(f"{heading.group(1)} has no verification command block")
        commands = tuple(
            line.strip()
            for line in command_block.group(1).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        if not commands:
            raise PipelineError(
                f"{heading.group(1)} has an empty verification command block"
            )
        task_id = heading.group(1)
        if task_id in tasks:
            raise PipelineError(f"duplicate task heading: {task_id}")
        tasks[task_id] = TaskSection(
            task_id=task_id,
            title=heading.group(2).strip(),
            body=body,
            verification_commands=commands,
        )
    return tasks


def load_pipeline(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, TaskSection]]:
    task_config_path = root / "config/agent_tasks.yaml"
    quality_config_path = root / "config/agent_quality_gates.yaml"
    task_config = load_structured(task_config_path)
    quality_config = load_structured(quality_config_path)
    taskbook_path = root / str(task_config.get("taskbook_path", "AGENT_TASKBOOK.md"))
    taskbook = parse_taskbook(taskbook_path)
    validate_pipeline_config(task_config, quality_config, taskbook)
    return task_config, quality_config, taskbook


def validate_pipeline_config(
    task_config: Mapping[str, Any],
    quality_config: Mapping[str, Any],
    taskbook: Mapping[str, TaskSection],
) -> None:
    if task_config.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError("config/agent_tasks.yaml has an unsupported schema_version")
    if quality_config.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(
            "config/agent_quality_gates.yaml has an unsupported schema_version"
        )

    configured_tasks = task_config.get("tasks")
    phases = task_config.get("phases")
    milestones = quality_config.get("milestones")
    metrics = quality_config.get("metrics")
    if not isinstance(configured_tasks, dict) or not configured_tasks:
        raise PipelineError("agent_tasks.yaml must define tasks")
    if not isinstance(phases, dict) or not phases:
        raise PipelineError("agent_tasks.yaml must define phases")
    if not isinstance(milestones, dict) or not isinstance(metrics, dict):
        raise PipelineError(
            "agent_quality_gates.yaml must define milestones and metrics"
        )

    configured_ids = set(configured_tasks)
    taskbook_ids = set(taskbook)
    if configured_ids != taskbook_ids:
        missing = sorted(taskbook_ids - configured_ids)
        extra = sorted(configured_ids - taskbook_ids)
        raise PipelineError(f"task registry mismatch; missing={missing}, extra={extra}")

    for task_id, item in configured_tasks.items():
        if not isinstance(item, dict):
            raise PipelineError(f"task {task_id} must be a mapping")
        phase = item.get("phase")
        if phase not in phases:
            raise PipelineError(f"task {task_id} references unknown phase {phase!r}")
        if not task_id.startswith(str(phase)):
            raise PipelineError(f"task {task_id} does not belong to phase {phase}")
        dependencies = item.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise PipelineError(f"task {task_id} dependencies must be a list")
        for dependency in dependencies:
            if dependency not in configured_tasks:
                raise PipelineError(
                    f"task {task_id} has unknown dependency {dependency}"
                )
            if dependency == task_id:
                raise PipelineError(f"task {task_id} cannot depend on itself")
        profile = item.get("quality_profile")
        if profile is not None and profile not in milestones:
            raise PipelineError(
                f"task {task_id} references unknown quality profile {profile}"
            )
        for milestone in item.get("required_milestones", []):
            if milestone not in milestones:
                raise PipelineError(
                    f"task {task_id} references unknown milestone {milestone}"
                )

    _validate_acyclic(configured_tasks)

    known_task_ids = set(configured_tasks)
    for milestone_id, milestone in milestones.items():
        if not isinstance(milestone, dict):
            raise PipelineError(f"milestone {milestone_id} must be a mapping")
        unknown_tasks = set(milestone.get("required_tasks", [])) - known_task_ids
        if unknown_tasks:
            raise PipelineError(
                f"milestone {milestone_id} has unknown tasks {sorted(unknown_tasks)}"
            )
        unknown_metrics = set(milestone.get("metric_ids", [])) - set(metrics)
        if unknown_metrics:
            raise PipelineError(
                f"milestone {milestone_id} has unknown metrics {sorted(unknown_metrics)}"
            )
        for prerequisite in milestone.get("required_milestones", []):
            if prerequisite not in milestones:
                raise PipelineError(
                    f"milestone {milestone_id} references unknown milestone {prerequisite}"
                )
    _validate_milestone_acyclic(milestones)


def _validate_acyclic(tasks: Mapping[str, Any]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise PipelineError(f"task dependency cycle detected at {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in tasks[task_id].get("dependencies", []):
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in tasks:
        visit(task_id)


def _validate_milestone_acyclic(milestones: Mapping[str, Any]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(milestone_id: str) -> None:
        if milestone_id in visiting:
            raise PipelineError(
                f"milestone dependency cycle detected at {milestone_id}"
            )
        if milestone_id in visited:
            return
        visiting.add(milestone_id)
        for dependency in milestones[milestone_id].get("required_milestones", []):
            visit(dependency)
        visiting.remove(milestone_id)
        visited.add(milestone_id)

    for milestone_id in milestones:
        visit(milestone_id)


def build_taskbook_lock(root: Path) -> dict[str, Any]:
    task_config, _, taskbook = load_pipeline(root)
    task_config_path = root / "config/agent_tasks.yaml"
    quality_config_path = root / "config/agent_quality_gates.yaml"
    taskbook_path = root / str(task_config.get("taskbook_path", "AGENT_TASKBOOK.md"))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "taskbook_path": taskbook_path.relative_to(root).as_posix(),
        "taskbook_sha256": sha256_file(taskbook_path),
        "task_config_sha256": sha256_file(task_config_path),
        "quality_gates_sha256": sha256_file(quality_config_path),
        "tasks": {
            task_id: {
                "title": section.title,
                "section_sha256": section.section_sha256,
                "verification_commands": list(section.verification_commands),
            }
            for task_id, section in sorted(taskbook.items())
        },
    }


def verify_taskbook_lock(root: Path) -> dict[str, Any]:
    task_config = load_structured(root / "config/agent_tasks.yaml")
    lock_path = root / str(
        task_config.get("taskbook_lock_path", "config/taskbook.lock.json")
    )
    lock = load_structured(lock_path)
    expected = build_taskbook_lock(root)
    for field in (
        "schema_version",
        "taskbook_path",
        "taskbook_sha256",
        "task_config_sha256",
        "quality_gates_sha256",
        "tasks",
    ):
        if lock.get(field) != expected.get(field):
            raise PipelineError(
                f"taskbook lock is stale at field {field}; run freeze_taskbook.py --update"
            )
    return lock


def git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PipelineError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def ensure_clean_worktree(root: Path) -> None:
    if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise PipelineError(
            "acceptance can only be frozen from a clean worktree; commit the tests first"
        )


def acceptance_lock_path(
    root: Path, task_config: Mapping[str, Any], task_id: str
) -> Path:
    lock_root = str(
        task_config.get("acceptance", {}).get("lock_root", "config/acceptance-locks")
    )
    return root / lock_root / f"{task_id}.json"


def discover_acceptance_files(
    root: Path,
    task_id: str,
    section: TaskSection,
    explicit_paths: Sequence[str] = (),
) -> tuple[list[Path], list[str]]:
    dedicated_path = f"tests/acceptance/{task_id.lower()}"
    if explicit_paths:
        candidates = set(explicit_paths)
    elif (root / dedicated_path).exists():
        candidates = {dedicated_path}
    else:
        candidates = set()
        for command in section.verification_commands:
            for token in re.findall(r"[A-Za-z0-9_.\-/]+", command):
                normalized = token.rstrip(".,:;")
                if normalized.startswith("tests/") or "/tests/" in normalized:
                    candidates.add(normalized)
                elif normalized.startswith("fixtures/evals/"):
                    candidates.add(normalized)

    files: set[Path] = set()
    missing: list[str] = []
    for candidate in sorted(candidates):
        path = root / candidate
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(item for item in path.rglob("*") if item.is_file())
        else:
            missing.append(candidate)
    return sorted(files), missing


def build_acceptance_lock(
    root: Path,
    task_id: str,
    explicit_paths: Sequence[str] = (),
) -> dict[str, Any]:
    task_config, _, taskbook = load_pipeline(root)
    verify_taskbook_lock(root)
    if task_id not in taskbook:
        raise PipelineError(f"unknown task: {task_id}")
    files, missing = discover_acceptance_files(
        root, task_id, taskbook[task_id], explicit_paths
    )
    if missing:
        raise PipelineError(
            "verification inputs do not exist yet: " + ", ".join(missing)
        )
    if not files:
        raise PipelineError(
            f"no acceptance files found for {task_id}; add tests before freezing acceptance"
        )
    taskbook_lock = verify_taskbook_lock(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "generated_at": utc_now(),
        "taskbook_sha256": taskbook_lock["taskbook_sha256"],
        "task_config_sha256": taskbook_lock["task_config_sha256"],
        "files": {
            path.relative_to(root).as_posix(): sha256_file(path) for path in files
        },
    }


def verify_acceptance_lock(
    root: Path, task_config: Mapping[str, Any], task_id: str
) -> tuple[dict[str, Any], str]:
    lock_path = acceptance_lock_path(root, task_config, task_id)
    lock = load_structured(lock_path)
    taskbook_lock = verify_taskbook_lock(root)
    if lock.get("task_id") != task_id:
        raise PipelineError(f"acceptance lock belongs to another task: {lock_path}")
    for field in ("taskbook_sha256", "task_config_sha256"):
        if lock.get(field) != taskbook_lock.get(field):
            raise PipelineError(f"acceptance lock for {task_id} is stale at {field}")
    files = lock.get("files")
    if not isinstance(files, dict) or not files:
        raise PipelineError(f"acceptance lock for {task_id} has no files")
    for relative, expected_hash in files.items():
        path = root / relative
        if not path.is_file():
            raise PipelineError(f"frozen acceptance file is missing: {relative}")
        if sha256_file(path) != expected_hash:
            raise PipelineError(f"frozen acceptance file changed: {relative}")

    relative_lock = lock_path.relative_to(root).as_posix()
    tracked = git(root, "ls-files", "--error-unmatch", "--", relative_lock, check=False)
    if not tracked:
        raise PipelineError(
            f"acceptance lock must be committed before implementation: {relative_lock}"
        )
    if git(root, "diff", "--name-only", "--", relative_lock) or git(
        root, "diff", "--cached", "--name-only", "--", relative_lock
    ):
        raise PipelineError(f"acceptance lock has uncommitted changes: {relative_lock}")
    baseline_commit = git(root, "log", "-1", "--format=%H", "--", relative_lock)
    if not baseline_commit:
        raise PipelineError(f"cannot resolve baseline commit for {relative_lock}")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", baseline_commit, "HEAD"], cwd=root
    )
    if ancestor.returncode != 0:
        raise PipelineError(
            "current HEAD does not descend from the acceptance baseline"
        )
    return lock, baseline_commit


def changed_files(root: Path, baseline_commit: str) -> list[str]:
    committed = git(
        root,
        "diff",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
        f"{baseline_commit}..HEAD",
    ).splitlines()
    working = git(
        root,
        "diff",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
        "HEAD",
    ).splitlines()
    staged = git(
        root,
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
    ).splitlines()
    untracked = git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted(
        {
            item.strip().replace("\\", "/")
            for item in (*committed, *working, *staged, *untracked)
            if item.strip()
        }
    )


def expand_pattern(pattern: str, task_id: str) -> str:
    return pattern.format(
        task_id=task_id,
        task_id_lower=task_id.lower(),
        phase=task_id[0],
        phase_lower=task_id[0].lower(),
    ).replace("\\", "/")


def path_matches(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    normalized_pattern = pattern.replace("\\", "/").lstrip("./")
    if normalized_pattern.endswith("/**"):
        prefix = normalized_pattern[:-3].rstrip("/")
        return normalized == prefix or normalized.startswith(prefix + "/")
    return fnmatch.fnmatchcase(normalized, normalized_pattern)


def validate_changed_paths(
    task_config: Mapping[str, Any], task_id: str, paths: Iterable[str]
) -> list[str]:
    task = task_config["tasks"][task_id]
    phase = task_config["phases"][task["phase"]]
    defaults = task_config.get("defaults", {})
    allowed = [
        expand_pattern(pattern, task_id)
        for pattern in (
            list(defaults.get("allowed_paths", []))
            + list(phase.get("allowed_paths", []))
            + list(task.get("allowed_paths", []))
        )
    ]
    forbidden = [
        expand_pattern(pattern, task_id)
        for pattern in (
            list(task_config.get("protected_paths", []))
            + list(defaults.get("forbidden_paths", []))
            + list(phase.get("forbidden_paths", []))
            + list(task.get("forbidden_paths", []))
        )
    ]
    violations: list[str] = []
    for path in paths:
        if any(path_matches(path, pattern) for pattern in forbidden):
            violations.append(f"protected path changed: {path}")
        elif not any(path_matches(path, pattern) for pattern in allowed):
            violations.append(f"path is outside task scope: {path}")
    return violations


def is_generated_evidence_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized.startswith("artifacts/task-evidence/") or normalized.startswith(
        "artifacts/phase-evidence/"
    )


def latest_manifest(root: Path, category: str, item_id: str) -> Path | None:
    directory = root / "artifacts" / category / item_id
    if not directory.is_dir():
        return None
    manifests = sorted(directory.glob("*/manifest.json"), reverse=True)
    return manifests[0] if manifests else None


def validate_evidence_manifest(
    root: Path, manifest_path: Path, expected_id: str, id_field: str
) -> dict[str, Any]:
    manifest = load_structured(manifest_path)
    lock = verify_taskbook_lock(root)
    if manifest.get(id_field) != expected_id:
        raise PipelineError(f"evidence identity mismatch: {manifest_path}")
    if manifest.get("verification_result") != "passed":
        raise PipelineError(f"latest evidence did not pass: {manifest_path}")
    for field in (
        "taskbook_sha256",
        "task_config_sha256",
        "quality_gates_sha256",
    ):
        if manifest.get(field) != lock.get(field):
            raise PipelineError(f"stale evidence {manifest_path}: {field}")
    if id_field == "task_id":
        task_config = load_structured(root / "config/agent_tasks.yaml")
        current_acceptance_path = acceptance_lock_path(root, task_config, expected_id)
        expected_acceptance_hash = manifest.get("acceptance_lock_sha256")
        if not expected_acceptance_hash:
            raise PipelineError(
                f"task evidence has no acceptance lock hash: {manifest_path}"
            )
        if (
            not current_acceptance_path.is_file()
            or sha256_file(current_acceptance_path) != expected_acceptance_hash
        ):
            raise PipelineError(
                f"acceptance lock changed after task verification: {expected_id}"
            )
        verify_acceptance_lock(root, task_config, expected_id)
    elif id_field == "milestone_id":
        for task_id, run_id in manifest.get("task_evidence", {}).items():
            task_manifest = (
                root
                / "artifacts/task-evidence"
                / task_id
                / str(run_id)
                / "manifest.json"
            )
            if not task_manifest.is_file():
                raise PipelineError(
                    f"milestone evidence references missing task run: {task_id}/{run_id}"
                )
            validate_evidence_manifest(root, task_manifest, task_id, "task_id")
        for milestone_id, run_id in manifest.get("milestone_evidence", {}).items():
            milestone_manifest = (
                root
                / "artifacts/phase-evidence"
                / milestone_id
                / str(run_id)
                / "manifest.json"
            )
            if not milestone_manifest.is_file():
                raise PipelineError(
                    "milestone evidence references missing prerequisite run: "
                    f"{milestone_id}/{run_id}"
                )
            validate_evidence_manifest(
                root, milestone_manifest, milestone_id, "milestone_id"
            )
    for artifact in manifest.get("evidence_files", []):
        relative = artifact.get("path")
        expected_hash = artifact.get("sha256")
        if not relative or not expected_hash:
            raise PipelineError(f"malformed evidence file entry: {manifest_path}")
        evidence_file = manifest_path.parent / relative
        if not evidence_file.is_file() or sha256_file(evidence_file) != expected_hash:
            raise PipelineError(f"evidence file is missing or changed: {evidence_file}")
    return manifest


def verify_task_dependency(root: Path, task_id: str) -> dict[str, Any]:
    manifest_path = latest_manifest(root, "task-evidence", task_id)
    if manifest_path is None:
        raise PipelineError(f"dependency {task_id} has no evidence")
    return validate_evidence_manifest(root, manifest_path, task_id, "task_id")


def verify_milestone_dependency(root: Path, milestone_id: str) -> dict[str, Any]:
    manifest_path = latest_manifest(root, "phase-evidence", milestone_id)
    if manifest_path is None:
        raise PipelineError(f"milestone {milestone_id} has no evidence")
    return validate_evidence_manifest(root, manifest_path, milestone_id, "milestone_id")


def run_preflight(root: Path, task_id: str, prepare: bool = False) -> dict[str, Any]:
    task_config, _, taskbook = load_pipeline(root)
    lock = verify_taskbook_lock(root)
    if task_id not in taskbook:
        raise PipelineError(f"unknown task: {task_id}")
    task = task_config["tasks"][task_id]
    dependencies = {}
    for dependency in task.get("dependencies", []):
        dependencies[dependency] = str(
            latest_manifest(root, "task-evidence", dependency) or ""
        )
        verify_task_dependency(root, dependency)
    milestones = {}
    for milestone in task.get("required_milestones", []):
        milestones[milestone] = str(
            latest_manifest(root, "phase-evidence", milestone) or ""
        )
        verify_milestone_dependency(root, milestone)

    result: dict[str, Any] = {
        "task_id": task_id,
        "title": taskbook[task_id].title,
        "phase": task["phase"],
        "taskbook_sha256": lock["taskbook_sha256"],
        "dependencies": dependencies,
        "required_milestones": milestones,
        "mode": "prepare" if prepare else "execute",
    }
    if not prepare:
        acceptance, baseline_commit = verify_acceptance_lock(root, task_config, task_id)
        current_acceptance_path = acceptance_lock_path(root, task_config, task_id)
        result["acceptance_files"] = sorted(acceptance["files"])
        result["acceptance_lock_sha256"] = sha256_file(current_acceptance_path)
        result["baseline_commit"] = baseline_commit
    return result


def evaluate_quality_profile(
    quality_config: Mapping[str, Any], profile_id: str, report: Mapping[str, Any]
) -> list[dict[str, Any]]:
    milestone = quality_config["milestones"][profile_id]
    report_metrics = report.get("metrics")
    if (
        report.get("schema_version") != SCHEMA_VERSION
        or report.get("profile_id") != profile_id
        or not isinstance(report_metrics, dict)
    ):
        raise PipelineError("quality report has an invalid schema")
    results: list[dict[str, Any]] = []
    for metric_id in milestone.get("metric_ids", []):
        metric = quality_config["metrics"][metric_id]
        actual_fields = report_metrics.get(metric_id)
        if not isinstance(actual_fields, dict):
            raise PipelineError(f"quality report is missing metric {metric_id}")
        for check in metric.get("checks", []):
            field = check["field"]
            if field not in actual_fields:
                raise PipelineError(f"quality report is missing {metric_id}.{field}")
            actual = actual_fields[field]
            threshold = check["threshold"]
            operator = check["operator"]
            passed = compare(actual, operator, threshold)
            results.append(
                {
                    "metric_id": metric_id,
                    "field": field,
                    "operator": operator,
                    "threshold": threshold,
                    "actual": actual,
                    "passed": passed,
                }
            )
    return results


def compare(actual: Any, operator: str, threshold: Any) -> bool:
    if isinstance(threshold, (int, float)) and (
        isinstance(actual, bool) or not isinstance(actual, (int, float))
    ):
        raise PipelineError(
            f"quality value must be numeric, got {type(actual).__name__}"
        )
    operations = {
        "eq": lambda: actual == threshold,
        "gte": lambda: actual >= threshold,
        "lte": lambda: actual <= threshold,
        "gt": lambda: actual > threshold,
        "lt": lambda: actual < threshold,
    }
    if operator not in operations:
        raise PipelineError(f"unsupported quality operator: {operator}")
    try:
        return bool(operations[operator]())
    except TypeError as exc:
        raise PipelineError(
            f"cannot compare quality value {actual!r} {operator} {threshold!r}"
        ) from exc


def evidence_file_entries(
    directory: Path, paths: Iterable[Path]
) -> list[dict[str, str]]:
    return [
        {
            "path": path.relative_to(directory).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(paths)
        if path.is_file()
    ]


def new_run_id(root: Path) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    commit = git(root, "rev-parse", "--short=10", "HEAD")
    nonce = os.urandom(3).hex()
    return f"{timestamp}-{commit}-{nonce}"
