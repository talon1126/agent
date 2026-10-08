"""Generate deterministic A3 request JSON Schema and OpenAPI snapshots."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AI_SERVICE_ROOT = ROOT / "services" / "ai-service"
sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.main import app  # noqa: E402
from app.routers.AImodel.schemas import AiModelChatRequest  # noqa: E402


JSON_SCHEMA_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_request.schema.json"
OPENAPI_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_request.openapi.json"
OPENAPI_COMPONENTS = (
    "AiModelCandidateRef",
    "AiModelChatRequest",
    "AiModelPageContext",
)


def build_openapi_snapshot() -> dict[str, Any]:
    document = app.openapi()
    schemas = document["components"]["schemas"]
    return {
        "request_body": document["paths"]["/AImodel/chat"]["post"]["requestBody"],
        "schemas": {name: schemas[name] for name in OPENAPI_COMPONENTS},
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    _write_json(JSON_SCHEMA_PATH, AiModelChatRequest.model_json_schema())
    _write_json(OPENAPI_PATH, build_openapi_snapshot())
    print(JSON_SCHEMA_PATH.relative_to(ROOT).as_posix())
    print(OPENAPI_PATH.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
