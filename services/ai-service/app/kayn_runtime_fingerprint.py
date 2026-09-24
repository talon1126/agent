"""Identify the Agent source loaded by a Kayn connector process."""

from __future__ import annotations

import hashlib
from pathlib import Path


_ROOT_FILES = (
    "services/ai-service/app/kayn_target.py",
    "services/ai-service/app/kayn_runtime_fingerprint.py",
    "scripts/run_kayn_abcd_evaluation.py",
    "config/kayn_evaluation_compose.override.yaml",
)
_AIMODEL_ROOT = Path("services/ai-service/app/routers/AImodel")
_SOURCE_SUFFIXES = {".py", ".yaml", ".yml"}


def evaluated_files(repo: Path) -> tuple[str, ...]:
    paths = set(_ROOT_FILES)
    paths.update(
        path.relative_to(repo).as_posix()
        for path in (repo / _AIMODEL_ROOT).rglob("*")
        if path.is_file() and path.suffix in _SOURCE_SUFFIXES
    )
    return tuple(sorted(paths))


def implementation_fingerprint(repo: Path, paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative_path in paths:
        content = (repo / relative_path).read_bytes()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(content).to_bytes(8, byteorder="big"))
        digest.update(content)
    return digest.hexdigest()
