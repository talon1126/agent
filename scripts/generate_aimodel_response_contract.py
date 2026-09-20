"""Generate deterministic A4 response JSON Schema and OpenAPI snapshots."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AI_SERVICE_ROOT = ROOT / "services" / "ai-service"
sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.main import app  # noqa: E402
from app.routers.AImodel.schemas import AiModelChatResponse  # noqa: E402


JSON_SCHEMA_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_response.schema.json"
OPENAPI_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_response.openapi.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    json_schema = AiModelChatResponse.model_json_schema()
    openapi_schema = app.openapi()["paths"]["/AImodel/chat"]["post"]["x-sse-events"][
        "done"
    ]["schema"]
    _write_json(JSON_SCHEMA_PATH, json_schema)
    _write_json(OPENAPI_PATH, openapi_schema)
    print(JSON_SCHEMA_PATH.relative_to(ROOT).as_posix())
    print(OPENAPI_PATH.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
