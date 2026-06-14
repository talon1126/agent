---
name: auto-coder
description: Autonomous spec-driven development agent. Use when the user says "auto code", "自动开发", "自动写代码", "auto dev", "一键开发", "autopilot", or asks for a fully automated DEV_SPEC.md to code workflow that reads the spec, finds the next task, implements it, tests it with up to three repair rounds, updates progress, and pauses only for commit confirmation.
---

# Auto Coder

Use this skill for autonomous, spec-driven implementation from the relevant
project `DEV_SPEC.md`.

## Trigger Intent

Run this workflow when the user wants one-shot automation from specification to code, including phrases such as:

- `auto code`
- `自动开发`
- `自动写代码`
- `auto dev`
- `一键开发`
- `autopilot`

## Non-Negotiable Rules

- Treat the selected domain `DEV_SPEC.md` as the single source of truth.
- Use uv as the only project environment and command runner. Do not activate
  `.venv` manually or invoke the system Python for project commands.
- Before implementation or verification, run
  `uv sync --project services/ai-service/rag --extra dev --frozen`.
- Before editing code, update or create a short task plan.
- When the user reports an optimization, fix, refactor, architecture correction,
  or workflow rule, first update `DEV_SPEC.md` so the reusable decision is not
  trapped in conversation context. Then re-sync references before continuing
  implementation.
- Match existing code style and architecture patterns.
- Use values from `config/settings.yaml`; do not hardcode project configuration.
- Write tests with implementation. Put tests under the path required by the spec, usually `tests/unit/` or `tests/integration/`.
- Mock external dependencies in unit tests.
- Do not ask for confirmation during the normal flow unless prerequisites are missing, the spec conflicts with the codebase, or the task is blocked.
- Pause only at the final commit confirmation step.

## Spec Domain Selection

Auto-coder supports two independent specification domains. Always choose the
domain before syncing references or selecting a task:

- `talonMart`: use for TalonMart, e-commerce frontend/backend, warehouse,
  procurement, delivery, operations workflows, n8n workflow files, Feishu
  adapter work, mock-api work, and AImodel integration work outside the RAG
  subsystem.
- `rag`: use for the standalone RAG subsystem under `services/ai-service/rag`,
  including ingestion, retrieval, evaluation, dashboard, MCP server internals,
  RAG storage, and RAG trace behavior.

If the user request mentions both domains, split the work by ownership:

- Caller-side integration from AImodel or TalonMart into RAG follows
  `talonMart`.
- Internal RAG behavior, APIs, tools, traces, and evaluation follows `rag`.

If the ownership is ambiguous, stop and ask which DEV_SPEC should control the
task before editing files.

## Project Optimization and Fix Mode

Use this mode when the user asks to correct an implemented task, refine a prior
architecture decision, repair dirty data behavior, or improve the auto-coder
workflow itself.

- Split the request into reviewable fix/refactor tasks when it crosses module or
  phase boundaries.
- Ask for clarification before changing schema fields, data contracts, task
  ownership, or model/Prompt behavior if the expected contract is ambiguous.
- For each task, update `DEV_SPEC.md` and any relevant skill/workflow rules,
  run `sync_spec.py --force`, write a failing test first, implement the smallest
  change, run verification, review the diff, then commit before starting the
  next task.
- Use `fix(scope): ...` for behavior corrections and `refactor(scope): ...` for
  structural changes that intentionally preserve behavior. Include the affected
  task ID or optimization label in the commit subject and body.
- Never batch unrelated fix and refactor changes into one commit just because
  they came from the same user message.

## Core Workflow

### 1. Sync Spec

Run:

```powershell
uv sync --project services/ai-service/rag --extra dev --frozen
uv run --project services/ai-service/rag python .codex\skills\auto-coder\scripts\sync_spec.py --domain all
```

Then read the schedule from the selected domain:

```text
.codex/skills/auto-coder/references/<domain>/06-schedule.md
```

### 2. Select Task

Choose the next task in this order:

1. First task marked `[~]`.
2. Otherwise first task marked `[ ]`.

Before implementation, quickly check that prerequisite objects, files, and prior-phase modules referenced by the task exist. If prerequisites do not match the spec, stop and report the blocker.

### 3. Read Relevant References

Load only the references needed for the selected task:

- Architecture and module placement: `references/<domain>/05-architecture.md`
- Technology choices and implementation patterns: `references/<domain>/03-tech-stack.md`
- Testing expectations: `references/<domain>/04-testing.md`
- Feature behavior: `references/<domain>/02-features.md`
- Task status and implementation details: `references/<domain>/06-schedule.md`
- Development, feedback-sync, and commit rules: `references/<domain>/07-development-rules.md`

Extract from the spec:

- input/output behavior
- design principles
- files to modify
- classes/functions to implement
- acceptance criteria
- test method

### 4. Implement

Follow a TDD loop:

1. Write or update the required tests first.
2. Run the relevant test command and confirm it fails for the expected reason.
3. Implement the minimal production code that satisfies the task.
4. Preserve existing user changes and avoid unrelated refactors.

Before running tests, self-check:

- all planned files exist
- test imports resolve
- changed code follows the architecture in `05-architecture.md`
- config comes from `config/settings.yaml` or settings objects

### 5. Test and Auto-Repair

Run the task-specific pytest command from `06-schedule.md` through
the command runner required by the selected domain.

Standard RAG command forms:

```powershell
uv run --project services/ai-service/rag pytest <test-path> -v
uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests
```

For `talonMart` tasks, use the verification command specified in
`references/talonMart/06-schedule.md`. Prefer `uv run --project <service>` for
Python services and the existing package script for frontend work.

Repair loop:

1. Run relevant tests.
2. If tests fail, analyze the error and apply a focused fix.
3. Repeat up to 3 total repair rounds.

If the third repair round still fails, stop and report:

- selected task ID and name
- failing command
- error summary
- files changed
- suspected root cause

### 6. Persist Progress

When tests pass:

1. Update global `DEV_SPEC.md`: mark the task `[ ]` or `[~]` as `[✔]`.
2. Update completion date and progress totals if the spec tracks them.
3. Re-sync references:

```powershell
uv run --project services/ai-service/rag python .codex\skills\auto-coder\scripts\sync_spec.py --domain all --force
```

4. Review all staged, unstaged, and untracked changes belonging to the current
   task. Focus on correctness, contracts, error handling, configuration,
   testing, documentation, and accidental scope expansion.
5. If review finds an actionable issue, fix it with TDD, rerun verification,
   and repeat the review until no actionable findings remain.
6. Preserve a review log for the final task summary. For every finding, report
   its impact, root cause, affected files, concrete fix, failing evidence, and
   passing verification. Do not omit findings merely because they were fixed.
   When no findings occurred, explicitly state that the review found no
   actionable issues.
7. Show a concise summary and ask for one of:

```text
✅ [TASK_ID] Task name — completed
Files: ...
Tests: n/n passed
Review:
- Finding: ...
  Fix: ...
  Verification: ...
Suggested commit: feat(scope): [TASK_ID] summary

"commit" -> git add + commit
"skip"   -> end without commit
"next"   -> commit + start next task
```

Only after the user answers should you commit or continue. A completed review
must always be the final action before this pause; do not start another task in
the same cycle.

## Commit Behavior

Use atomic commits. Stage only files related to the completed task and synchronized references. Do not include unrelated dirty files.

Before committing, read `references/<domain>/07-development-rules.md` and use
the commit structure defined there. The commit subject must include the task ID.
The body must include `Changes`, `Testing`, `Design Principles`, `Task`, `Spec`,
and a fresh `Tests` result.

When the user corrects architecture, naming, workflow order, testing, documentation, or commit behavior, update `DEV_SPEC.md` before continuing. Re-sync references after the edit. Do not leave reusable decisions only in conversation context.

If the user answers `next`, commit the already reviewed task first, then restart
from step 1 and implement exactly one next task. After that task's review,
always stop for confirmation again.
