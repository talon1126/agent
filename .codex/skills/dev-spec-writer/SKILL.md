---
name: dev-spec-writer
description: Create or revise Chinese DEV_SPEC.md development specification documents that cover a complete project lifecycle. Use when the user asks to generate, design, improve, audit, or update a DEV_SPEC.md or full development plan with project overview, highlights, technical choices, TDD test strategy, architecture/module design, directory tree, module responsibility tables, phased roadmap, task tracking, and AI-executable implementation stages.
---

# DEV_SPEC Writer

Use this skill to create or maintain a Chinese `DEV_SPEC.md` that can guide an entire software project from design through staged AI-assisted implementation.

## Core Workflow

1. Identify the target project, output path, language, and whether the user wants a new document or edits to an existing `DEV_SPEC.md`.
2. Read any existing project docs, code structure, plans, screenshots, or user notes before writing.
3. Ask concise clarification questions before making assumptions when project scope, architecture, data model, phase boundaries, or component behavior is unclear.
4. Produce or update the DEV_SPEC in Chinese by default unless the user requests another language.
5. Keep the document useful for three audiences: developers implementing the project, users supervising AI progress, and reviewers/interviewers quickly understanding design highlights.
6. After edits, scan the full document for consistency: terminology, phase numbers, task counts, file paths, module names, diagrams, trace/data flows, and progress tables.

## Required DEV_SPEC Structure

Use these sections unless the user provides a different structure:

1. 项目概述
2. 核心特点
3. 技术选型
4. 测试方案（TDD）
5. 系统架构与模块设计
6. 项目排期

For a full skeleton, read `references/dev_spec_template.md`.

## Writing Rules

- Write with clear Chinese headings and concise explanations.
- Use `**bold**` to highlight design亮点, not every technical term.
- In 核心特点, emphasize what makes the project understandable and impressive; avoid implementation minutiae unless needed.
- In 技术选型, explain pipeline/layer responsibilities, component boundaries, fallback behavior, and configuration-driven choices.
- In 测试方案, require TDD: each feature task should define matching tests before implementation.
- In 系统架构与模块设计, include an overall architecture diagram, directory tree, and module responsibility table.
- In 项目排期, split the project into phases and tasks that a user can assign to AI one by one.
- Make tasks concrete enough to implement: each task should include goal, modified files, classes/functions, acceptance criteria, and test method when detailed implementation planning is requested.
- Keep phase and task numbering stable after edits; if tasks are merged, deleted, or inserted, update all downstream references and totals.

## Clarification Policy

Ask before proceeding when any of these are unclear:

- project purpose or target users
- output path
- backend/frontend/language/framework choices
- persistence layer or deployment target
- data model or API contracts
- whether a feature is in scope for the first version
- ordering of pipeline stages
- phase/task granularity
- whether to generate only a document or also implement code

If only minor wording is unclear but a safe local edit is obvious, make the edit and mention the assumption.

## Project Schedule Requirements

The 项目排期 section should support AI-assisted development management:

- 阶段总览表: phase, goal, status.
- 交付里程碑: after each phase, record current project position, available features, validation commands, next phase entry point.
- 进度跟踪表: each phase contains tasks with status, completion date, notes.
- 总体进度表: phase, total tasks, completed, progress.
- 阶段实施明细: for each task, include goal, modified files, classes/functions, acceptance criteria, and test method when the user wants an executable plan.

Task design rules:

- Keep each task independently reviewable.
- Avoid tasks that are too thin to verify or too broad to finish safely.
- Every implementation task should include tests or a clear verification method.
- Put scripts and integration gates after the core modules they depend on.
- When users reorder pipeline stages, update task tables, implementation details, diagrams, data flows, trace stages, and tests.

## Consistency Audit Checklist

Before finalizing, search the document for changed terms and stale references:

- old phase names or numbers
- old task IDs after insertion/deletion/merge
- removed file names
- renamed classes/functions
- stale pipeline stage order
- stale trace stage lists
- stale test descriptions
- stale total task counts
- mismatched table rows and implementation detail headings
- directory tree comments alignment if the document uses aligned `#` comments

Report what changed and any remaining assumptions.