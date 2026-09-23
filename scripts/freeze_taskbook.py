"""Freeze the taskbook and executable gate definitions into one lock file."""

from __future__ import annotations

import argparse
import sys

from agent_pipeline import (
    PipelineError,
    build_taskbook_lock,
    load_structured,
    repository_root,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--update",
        action="store_true",
        help="replace an existing lock after an approved taskbook/config change",
    )
    args = parser.parse_args()
    root = repository_root()
    try:
        config = load_structured(root / "config/agent_tasks.yaml")
        lock_path = root / str(
            config.get("taskbook_lock_path", "config/taskbook.lock.json")
        )
        if lock_path.exists() and not args.update:
            raise PipelineError(
                f"lock already exists: {lock_path}; use --update for an approved change"
            )
        lock = build_taskbook_lock(root)
        write_json(lock_path, lock)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Frozen {len(lock['tasks'])} tasks at {lock_path.relative_to(root)}")
    print(f"taskbook_sha256={lock['taskbook_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
