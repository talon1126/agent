"""Validate that one task is allowed to start or continue."""

from __future__ import annotations

import argparse
import json
import sys

from agent_pipeline import (
    PipelineError,
    load_pipeline,
    repository_root,
    run_preflight,
    verify_taskbook_lock,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", help="task ID, for example G1")
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="check dependencies before acceptance tests are frozen",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate task registry, taskbook and lock without selecting a task",
    )
    args = parser.parse_args()
    root = repository_root()
    try:
        if args.check_config:
            task_config, quality_config, taskbook = load_pipeline(root)
            lock = verify_taskbook_lock(root)
            result = {
                "tasks": len(taskbook),
                "phases": len(task_config["phases"]),
                "milestones": len(quality_config["milestones"]),
                "taskbook_sha256": lock["taskbook_sha256"],
            }
        else:
            if not args.task:
                parser.error("--task is required unless --check-config is used")
            result = run_preflight(root, args.task.upper(), prepare=args.prepare)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
