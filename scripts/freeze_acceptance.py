"""Freeze task acceptance tests and fixtures before implementation starts."""

from __future__ import annotations

import argparse
import sys

from agent_pipeline import (
    PipelineError,
    acceptance_lock_path,
    build_acceptance_lock,
    load_pipeline,
    repository_root,
    run_preflight,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, help="task ID, for example G1")
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help=(
            "acceptance file or directory; may be repeated; when supplied, only "
            "these paths are frozen"
        ),
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="replace an existing acceptance lock after approved test changes",
    )
    args = parser.parse_args()
    task_id = args.task.upper()
    root = repository_root()
    try:
        run_preflight(root, task_id, prepare=True)
        task_config, _, _ = load_pipeline(root)
        lock_path = acceptance_lock_path(root, task_config, task_id)
        if lock_path.exists() and not args.update:
            raise PipelineError(
                f"acceptance lock already exists: {lock_path}; use --update explicitly"
            )
        lock = build_acceptance_lock(root, task_id, args.path)
        write_json(lock_path, lock)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Frozen {len(lock['files'])} acceptance files for {task_id}")
    print(f"Keep {lock_path.relative_to(root)} for the task's single final commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
