import json
import os
from pathlib import Path
from typing import Any


def find_default_fixture_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "fixtures"
        if candidate.exists():
            return candidate
    return Path.cwd() / "fixtures"


DEFAULT_FIXTURE_DIR = find_default_fixture_dir()
FIXTURE_DIR = Path(os.getenv("FIXTURE_DIR", DEFAULT_FIXTURE_DIR)).resolve()


def load_json(name: str) -> list[dict[str, Any]]:
    with (FIXTURE_DIR / "data" / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_by_id(filename: str, key: str, value: str) -> dict[str, Any] | None:
    for item in load_json(filename):
        if item.get(key) == value:
            return item
    return None
