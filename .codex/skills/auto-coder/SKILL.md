---
name: auto-coder
description: Autonomous spec-driven development agent. Use when the user says "auto code", "自动开发", "自动写代码", "auto dev", "一键开发", "autopilot", or asks for a fully automated DEV_SPEC.md to code workflow that reads the spec, finds the next task, implements it, tests it with up to three repair rounds, updates progress, and pauses only for commit confirmation.
---

# Auto Coder

Use this skill for autonomous, spec-driven implementation from `DEV_SPEC.md`.

## Trigger Intent

Run this workflow when the user wants one-shot automation from specification to code, including phrases such as:

- `auto code`
- `自动开发`
- `自动写代码`
- `auto dev`
- `一键开发`
- `autopilot`

## Non-Negotiable Rules

- Treat `DEV_SPEC.md` as the single source of truth.
- Use uv as the only project environment and command runner. Do not activate
  `.venv` manually or invoke the system Python for project commands.
- Before implementation or verification, run
  `uv sync --project services/ai-service/rag --extra dev --frozen`.
- Before editing code, update or create a short task plan.
- Match existing code style and architecture patterns.
- Use values from `config/settings.yaml`; do not hardcode project configuration.
- Write tests with implementation. Put tests under the path required by the spec, usually `tests/unit/` or `tests/integration/`.
- Mock external dependencies in unit tests.
- Do not ask for confirmation during the normal flow unless prerequisites are missing, the spec conflicts with the codebase, or the task is blocked.
- Pause only at the final commit confirmation step.

## Core Workflow

### 1. Sync Spec

Run:

```powershell
uv sync --project services/ai-service/rag --extra dev --frozen
uv run --project services/ai-service/rag python .codex\skills\auto-coder\scripts\sync_spec.py
```

Then read:

```text
.codex/skills/auto-coder/references/06-schedule.md
```

### 2. Select Task

Choose the next task in this order:

1. First task marked `[~]`.
2. Otherwise first task marked `[ ]`.

Before implementation, quickly check that prerequisite objects, files, and prior-phase modules referenced by the task exist. If prerequisites do not match the spec, stop and report the blocker.

### 3. Read Relevant References

Load only the references needed for the selected task:

- Architecture and module placement: `references/05-architecture.md`
- Technology choices and implementation patterns: `references/03-tech-stack.md`
- Testing expectations: `references/04-testing.md`
- Feature behavior: `references/02-features.md`
- Task status and implementation details: `references/06-schedule.md`
- Development, feedback-sync, and commit rules: `references/07-development-rules.md`

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
`uv run --project services/ai-service/rag`.

Standard command forms:

```powershell
uv run --project services/ai-service/rag pytest <test-path> -v
uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests
```

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
uv run --project services/ai-service/rag python .codex\skills\auto-coder\scripts\sync_spec.py --force
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

Before committing, read `references/07-development-rules.md` and use the commit structure defined there. The commit subject must include the task ID. The body must include `Changes`, `Testing`, `Design Principles`, `Task`, `Spec`, and a fresh `Tests` result.

When the user corrects architecture, naming, workflow order, testing, documentation, or commit behavior, update `DEV_SPEC.md` before continuing. Re-sync references after the edit. Do not leave reusable decisions only in conversation context.

If the user answers `next`, commit the already reviewed task first, then restart
from step 1 and implement exactly one next task. After that task's review,
always stop for confirmation again.
