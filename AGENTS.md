# TalonMart Agent development rules

These instructions apply to the entire repository. They govern work selected from
`AGENT_TASKBOOK.md`; an explicit user request to maintain the pipeline itself is
handled separately and must not be hidden inside a taskbook task.

## Sources of truth

- `AGENT_TASKBOOK.md` defines product intent, deliverables, completion criteria,
  verification commands, and task boundaries.
- `config/agent_tasks.yaml` defines executable dependencies and file scopes.
- `config/agent_quality_gates.yaml` is the only machine-readable source for
  numeric release thresholds.
- `config/taskbook.lock.json` binds the three sources above. Never work around a
  stale lock; stop and use the approved change procedure.
- Do not add task status fields to the taskbook. Completion is derived from
  evidence under `artifacts/task-audits/`, `artifacts/task-evidence/`, and
  `artifacts/phase-evidence/`.

## Required task flow

Work on exactly one task ID at a time.

1. Run `python scripts/task_preflight.py --task <TASK_ID> --prepare`.
2. Add or review the task's acceptance tests and fixtures. Do not implement
   production behavior in this step.
3. Commit the acceptance inputs, run
   `python scripts/freeze_acceptance.py --task <TASK_ID>`, then commit the
   generated `config/acceptance-locks/<TASK_ID>.json`.
4. Run `python scripts/task_preflight.py --task <TASK_ID>` before implementation.
5. Implement only the selected task and only within its permitted paths.
6. Run `python scripts/task_audit.py --task <TASK_ID> --fix`. The audit has two
   layers: static/security checks, then frozen acceptance and regression checks.
   Safe Ruff fixes may be applied automatically. Review and commit those edits,
   then rerun the audit. The implementing agent may make at most two audit/fix
   rounds; unresolved behavioral findings must be reported instead of bypassed.
7. Run `python scripts/task_verify.py --task <TASK_ID>`. F5, G5, H5, and I5 also
   require `--quality-report <path>`.
8. A reviewer or CI runner independent from the implementing agent reruns the
   same verification. An agent's written claim is not completion evidence.
9. At a stage boundary run `python scripts/verify_phase_gate.py --milestone <ID>`.
   `I-DATA-READY` additionally requires its machine-readable quality report.

## Non-negotiable constraints

- Never modify frozen acceptance files during implementation. If a requirement
  is wrong or incomplete, stop implementation and process an explicit spec/test
  change, then refreeze and recommit the acceptance lock.
- Never change `AGENT_TASKBOOK.md`, pipeline scripts, pipeline configuration,
  acceptance locks, or existing evidence to make a task pass.
- Never delete, skip, weaken, mark xfail, or narrow a failing mandatory test.
- Never run more than two audit/fix rounds for one implementation attempt. Safe
  automatic repair is limited to formatter/linter fixes; do not blindly rewrite
  domain behavior to silence a failing acceptance or regression test.
- Never lower a quality threshold, change a metric denominator, or remove failed
  samples during a feature task.
- Never edit `apps/talonmart-web`; frontend work is outside this taskbook.
- G, H, and I code must follow the ownership boundaries in taskbook section 2.4.
- Do not implement later tasks opportunistically. Create no compatibility or
  migration behavior unless the selected task requires it.
- Do not use simulated exposure or labels to claim M6-B readiness.
- Do not auto-merge, auto-release, or deploy to production from these scripts.

## Change control

An approved taskbook, gate, or pipeline change is a separate change set. Update
the relevant source, run `python scripts/freeze_taskbook.py --update`, review the
lock diff, and invalidate/refreeze affected acceptance locks. Existing evidence
with old hashes is intentionally rejected.

## Completion rule

A task is complete only when dependencies, frozen acceptance, path scope, both
audit layers, taskbook verification commands, applicable quality gates,
evidence generation, and independent rerun all pass. Preserve failure logs; do
not represent partial or mocked results as completion.
