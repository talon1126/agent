import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_pipeline import (  # noqa: E402
    PipelineError,
    build_taskbook_lock,
    discover_acceptance_files,
    evaluate_quality_profile,
    is_generated_evidence_path,
    load_pipeline,
    runner_metadata,
    sha256_file,
    sha256_mapping,
    validate_changed_paths,
    verify_task_audit,
    verify_taskbook_lock,
)
from task_audit import (  # noqa: E402
    MAX_AUDIT_ROUNDS,
    _non_audit_worktree_changes,
    scan_added_secrets,
    syntax_findings,
)
from verify_phase_gate import build_stage_verification_commands  # noqa: E402


def _passing_quality_report(quality_config: dict, profile_id: str) -> dict:
    metrics = {}
    for metric_id in quality_config["milestones"][profile_id]["metric_ids"]:
        values = {}
        for check in quality_config["metrics"][metric_id]["checks"]:
            threshold = check["threshold"]
            if check["operator"] == "gt":
                value = threshold + 1
            elif check["operator"] == "lt":
                value = threshold - 1
            else:
                value = threshold
            values[check["field"]] = value
        metrics[metric_id] = values
    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "config_version": quality_config["config_version"],
        "config_sha256": sha256_mapping(quality_config),
        "metrics": metrics,
    }


def test_task_registry_matches_all_taskbook_sections_and_commands() -> None:
    task_config, quality_config, taskbook = load_pipeline(ROOT)

    assert len(taskbook) == 45
    assert set(taskbook) == set(task_config["tasks"])
    assert all(section.verification_commands for section in taskbook.values())
    assert set(task_config["phases"]) == set("ABCDEFGHI")
    assert {"M3", "M4", "M5", "M6-A", "I-DATA-READY", "M6-B"}.issubset(
        quality_config["milestones"]
    )


def test_taskbook_lock_matches_current_contracts() -> None:
    current = verify_taskbook_lock(ROOT)
    expected = build_taskbook_lock(ROOT)

    assert current["taskbook_sha256"] == expected["taskbook_sha256"]
    assert current["task_config_sha256"] == expected["task_config_sha256"]
    assert current["quality_gates_sha256"] == expected["quality_gates_sha256"]
    assert current["tasks"] == expected["tasks"]


def test_text_hash_is_stable_across_git_line_endings(tmp_path: Path) -> None:
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    lf.write_bytes(b"alpha\nbeta\n")
    crlf.write_bytes(b"alpha\r\nbeta\r\n")

    assert sha256_file(lf) == sha256_file(crlf)


def test_explicit_acceptance_paths_do_not_lock_implementation_tests(
    tmp_path: Path,
) -> None:
    acceptance_dir = tmp_path / "tests" / "acceptance" / "a1"
    acceptance_dir.mkdir(parents=True)
    acceptance_file = acceptance_dir / "test_contract.py"
    acceptance_file.write_text("def test_contract(): pass\n", encoding="utf-8")
    bytecode_dir = acceptance_dir / "__pycache__"
    bytecode_dir.mkdir()
    (bytecode_dir / "test_contract.cpython-312.pyc").write_bytes(b"generated")
    implementation_test = tmp_path / "services" / "ai-service" / "tests"
    implementation_test.mkdir(parents=True)
    (implementation_test / "test_aimodel_agent.py").write_text(
        "def test_agent(): pass\n", encoding="utf-8"
    )
    section = type(
        "TaskSectionStub",
        (),
        {
            "verification_commands": (
                "pytest services/ai-service/tests/test_aimodel_agent.py",
            )
        },
    )()

    files, missing = discover_acceptance_files(
        tmp_path,
        "A1",
        section,
        ("tests/acceptance/a1",),
    )

    assert files == [acceptance_file]
    assert missing == []


def test_scope_rules_block_frontend_and_pipeline_mutation() -> None:
    task_config, _, _ = load_pipeline(ROOT)

    assert not validate_changed_paths(
        task_config,
        "G1",
        ["services/ai-service/app/domains/personalization/consent.py"],
    )
    assert validate_changed_paths(
        task_config,
        "G1",
        ["services/ai-service/app/routers/AImodel/router.py"],
    )
    assert not validate_changed_paths(
        task_config,
        "G5",
        ["services/ai-service/app/routers/AImodel/router.py"],
    )
    for protected in (
        "AGENT_TASKBOOK.md",
        "config/agent_quality_gates.yaml",
        "scripts/task_verify.py",
        "apps/talonmart-web/src/App.vue",
    ):
        assert validate_changed_paths(task_config, "A1", [protected])


def test_generated_evidence_is_distinct_from_implementation_changes() -> None:
    assert is_generated_evidence_path("artifacts/task-audits/A1/run/manifest.json")
    assert is_generated_evidence_path("artifacts/task-evidence/A1/run/manifest.json")
    assert is_generated_evidence_path("artifacts/phase-evidence/M3/run/manifest.json")
    assert not is_generated_evidence_path("artifacts/recommendation/model.bin")


@pytest.mark.parametrize("profile_id", ["M3", "M4", "M5", "M6-B", "I-DATA-READY"])
def test_quality_profiles_are_machine_evaluable(profile_id: str) -> None:
    _, quality_config, _ = load_pipeline(ROOT)
    report = _passing_quality_report(quality_config, profile_id)

    results = evaluate_quality_profile(quality_config, profile_id, report)

    assert results
    assert all(result["passed"] for result in results)


def test_quality_profile_rejects_a_missing_metric() -> None:
    _, quality_config, _ = load_pipeline(ROOT)
    report = _passing_quality_report(quality_config, "M3")
    report["metrics"].pop("M3-01")

    with pytest.raises(PipelineError, match="missing metric M3-01"):
        evaluate_quality_profile(quality_config, "M3", report)


def test_quality_profile_rejects_unbound_or_stale_config() -> None:
    _, quality_config, _ = load_pipeline(ROOT)
    report = _passing_quality_report(quality_config, "M3")
    report.pop("config_sha256")

    with pytest.raises(PipelineError, match="bind the current"):
        evaluate_quality_profile(quality_config, "M3", report)

    report = _passing_quality_report(quality_config, "M3")
    report["config_version"] = "stale-version"
    with pytest.raises(PipelineError, match="bind the current"):
        evaluate_quality_profile(quality_config, "M3", report)


def test_quality_profile_rejects_boolean_values_for_numeric_gates() -> None:
    _, quality_config, _ = load_pipeline(ROOT)
    report = _passing_quality_report(quality_config, "M3")
    report["metrics"]["M3-02"]["hard_constraint_violation_rate"] = False

    with pytest.raises(PipelineError, match="must be numeric"):
        evaluate_quality_profile(quality_config, "M3", report)


def test_agents_instructions_bind_ai_work_to_the_pipeline() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for token in (
        "AGENT_TASKBOOK.md",
        "config/agent_tasks.yaml",
        "freeze_acceptance.py",
        "task_preflight.py",
        "task_verify.py",
        "task_audit.py",
        "verify_phase_gate.py",
        "one task ID at a time",
        "Never edit `apps/talonmart-web`",
        "Do not add task status fields",
        "AGENT_INDEPENDENT_REVIEW",
    ):
        assert token in text


def test_task_audit_has_two_round_limit_and_detects_added_secrets() -> None:
    assert MAX_AUDIT_ROUNDS == 2
    assert scan_added_secrets("+token = 'ghp_123456789012345678901234567890'") == [
        "github_token"
    ]
    assert scan_added_secrets(" context\n-token = 'ghp_old'\n+token = 'test-key'") == []


def test_task_audit_parses_changed_python_and_json(tmp_path: Path) -> None:
    (tmp_path / "valid.py").write_text("answer = 42\n", encoding="utf-8")
    (tmp_path / "valid.json").write_text('{"ok": true}\n', encoding="utf-8")

    assert syntax_findings(tmp_path, ["valid.py", "valid.json"]) == []

    (tmp_path / "invalid.py").write_text("def broken(:\n", encoding="utf-8")
    findings = syntax_findings(tmp_path, ["invalid.py"])

    assert len(findings) == 1
    assert findings[0].startswith("invalid.py:")


def test_task_audit_ignores_only_its_generated_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    def fake_git(root: Path, *args: str, **kwargs) -> str:
        assert root == tmp_path
        assert "--porcelain=v1" in args
        return (
            "?? artifacts/task-audits/A1/run/manifest.json\0"
            " M services/ai-service/app/main.py\0"
        )

    monkeypatch.setattr("task_audit.git", fake_git)

    assert _non_audit_worktree_changes(tmp_path) == ["services/ai-service/app/main.py"]


def test_m3_metric_semantics_have_one_machine_readable_source() -> None:
    _, quality_config, _ = load_pipeline(ROOT)

    for metric_id in (f"M3-{index:02d}" for index in range(1, 11)):
        metric = quality_config["metrics"][metric_id]
        assert metric["target"]
        assert metric["denominator"]
        assert metric["window"]
        assert metric["applicable_milestones"] == ["M3"]


def test_independent_runner_requires_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("AGENT_INDEPENDENT_REVIEW", "true")
    monkeypatch.delenv("AGENT_VERIFIER_ID", raising=False)

    with pytest.raises(PipelineError, match="AGENT_VERIFIER_ID"):
        runner_metadata(require_independent=True)

    monkeypatch.setenv("AGENT_VERIFIER_ID", "reviewer-1")
    assert runner_metadata(require_independent=True) == {
        "ci": False,
        "independent": True,
        "verifier_id": "reviewer-1",
    }


def test_stage_gate_reruns_acceptance_and_task_verification() -> None:
    task_config, _, taskbook = load_pipeline(ROOT)
    commands = build_stage_verification_commands(
        ROOT,
        task_config,
        taskbook,
        ["A1", "A2"],
    )

    assert {item["kind"] for item in commands} == {
        "frozen_acceptance",
        "task_verification",
    }
    assert {item["task_id"] for item in commands} == {"A1", "A2"}


@pytest.mark.parametrize("task_id", ["A1", "A2", "A3", "A4", "A5"])
def test_completed_stage_a_tasks_have_passing_audits(task_id: str) -> None:
    manifest = verify_task_audit(ROOT, task_id)

    assert manifest["result"] == "passed"
