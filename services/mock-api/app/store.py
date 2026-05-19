import json
import os
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(os.getenv("FIXTURE_DIR", "../../fixtures")).resolve()


def load_json(name: str) -> list[dict[str, Any]]:
    with (FIXTURE_DIR / "data" / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_by_id(filename: str, key: str, value: str) -> dict[str, Any] | None:
    for item in load_json(filename):
        if item.get(key) == value:
            return item
    return None
