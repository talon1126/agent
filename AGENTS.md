# TalonMart Agent development rules

These instructions apply to the entire repository. They govern work selected from
`AGENT_TASKBOOK.md`; an explicit user request to maintain the pipeline itself is
handled separately and must not be hidden inside a taskbook task.

## Sources of truth

- `AGENT_TASKBOOK.md` defines product intent, deliverables, completion criteria,
  verification commands, and task boundaries.
- `config/agent_tasks.yaml` defines executable dependencies and file scopes.
- `config/agent_quality_gates.yaml` is the only machine-readable source for
  release metric semantics, lineage, versions, and numeric thresholds.
- `config/taskbook.lock.json` binds the three sources above. Never work around a
  stale lock; stop and use the approved change procedure.
- Do not add task status fields to the taskbook. Append ` ✔️` only to a completed
  task's existing heading. Completion is still derived from evidence under
  `artifacts/task-audits/`, `artifacts/task-evidence/`, and
  `artifacts/phase-evidence/`; the heading marker is only a human-readable index
  and is excluded from taskbook semantic hashes.

## Completion markers

- A task heading in `AGENT_TASKBOOK.md` is the only location for its ` ✔️`
  marker. Never maintain a duplicate completion list in this file.
- Add the marker only after frozen acceptance, implementation, two-layer audit,
  local verification, and independent verification have passed and their files
  are ready for the task's single final commit.

Stage A independent closure passed at commit `592710f4` with evidence run
`20260920T132512Z-592710f4de-9e89e0`.

Stage B independent closure passed at commit `3452f178` with evidence run
`20260921T040728Z-3452f178a3-a66de2`.

## Required task flow

Work on exactly one task ID at a time.

1. Run `python scripts/task_preflight.py --task <TASK_ID> --prepare`.
2. Add or review the task's acceptance tests and fixtures. Do not implement
   production behavior in this step.
3. Run `python scripts/freeze_acceptance.py --task <TASK_ID>`. Keep the
   acceptance inputs and generated lock uncommitted for the task's single final
   commit.
4. Run `python scripts/task_preflight.py --task <TASK_ID>` before implementation.
5. Implement only the selected task and only within its permitted paths.
6. Copy only the selected task's acceptance, lock, and implementation files to
   an ordinary temporary clone rooted at the lock's `baseline_commit`, create a
   disposable verification commit there, and run
   `python scripts/task_audit.py --task <TASK_ID> --fix`. The audit has two
   layers: static/security checks, then frozen acceptance and regression checks.
   The implementing agent may make at most two audit/fix rounds.
7. In that clean clone run `python scripts/task_verify.py --task <TASK_ID>`.
   F5, G5, H5, and I5 also require `--quality-report <path>`.
8. A reviewer or CI runner independent from the implementing agent reruns the
   same verification against the identical implementation fingerprint. Local
   reviewers set `AGENT_INDEPENDENT_REVIEW=true` and a stable
   `AGENT_VERIFIER_ID`; CI supplies the verifier ID. Copy the resulting audit and
   verification evidence back without copying the disposable commit history.
9. Append ` ✔️` to the task heading in `AGENT_TASKBOOK.md`, then create exactly
   one permanent task commit containing acceptance inputs, acceptance lock,
   implementation, audit evidence, local and independent verification evidence,
   and the heading marker. No intermediate task commits may remain in repository
   history.
10. At a stage boundary the independent reviewer runs
   `python scripts/verify_phase_gate.py --milestone <ID>`. The gate extracts the
   current Git commit into a clean snapshot and reruns every required task's
   frozen acceptance and verification commands. `I-DATA-READY` and metric-bearing
   milestones additionally require their machine-readable quality report.

## Non-negotiable constraints

- Never modify frozen acceptance files during implementation. If a requirement
  is wrong or incomplete, stop implementation, process an explicit spec/test
  change, and refreeze before resuming.
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
- Never set the independent-review environment variables for your own
  implementation run. They are an explicit reviewer attestation, not a bypass.

## Change control

An approved taskbook, gate, or pipeline change is a separate change set. Update
the relevant source, run `python scripts/freeze_taskbook.py --update`, review the
lock diff, and invalidate/refreeze affected acceptance locks. Existing evidence
with old hashes is intentionally rejected. Adding or removing only an existing
task heading's ` ✔️` marker is completion metadata, not a semantic taskbook
change, and must not require a taskbook refreeze.

## Completion rule

A task is complete only when dependencies, frozen acceptance, path scope, both
audit layers, taskbook verification commands, applicable quality gates,
evidence generation, and independent rerun all pass. Preserve failure logs; do
not represent partial or mocked results as completion.
