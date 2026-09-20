"""Frozen acceptance contract for task A3."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[3]
AI_SERVICE_ROOT = ROOT / "services" / "ai-service"
sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.main import app  # noqa: E402
from app.routers.AImodel import schemas as aimodel_schemas  # noqa: E402


JSON_SCHEMA_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_request.schema.json"
OPENAPI_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_request.openapi.json"
MAX_SEARCH_QUERY_LENGTH = 200
MAX_CANDIDATES = 20
MAX_LINKS = 8
MAX_LINK_LENGTH = 2048
MAX_REQUEST_BYTES = 16 * 1024


def _context_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "v1",
        "page_type": "search",
        "route": "/search",
        "search_query": "无线耳机",
        "current_item_id": "item_wireless_earbuds",
        "candidate_refs": [
            {"item_id": "item_wireless_earbuds"},
            {"item_id": 10002},
        ],
        "source_event": "search_results_viewed",
        "client_time": "2026-09-20T10:00:00+08:00",
    }
    payload.update(overrides)
    return payload


def _request_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "user_id": 1,
        "conversation_id": 2,
        "message": "帮我比较当前候选商品",
        "links": [],
        "request_version": "v2",
        "page_context": _context_payload(),
    }
    payload.update(overrides)
    return payload


def _contract_openapi_snapshot() -> dict[str, Any]:
    document = app.openapi()
    components = document["components"]["schemas"]
    names = (
        "AiModelCandidateRef",
        "AiModelChatRequest",
        "AiModelPageContext",
    )
    return {
        "request_body": document["paths"]["/AImodel/chat"]["post"]["requestBody"],
        "schemas": {name: components[name] for name in names},
    }


def test_legacy_request_defaults_to_v1_without_context() -> None:
    request_model = aimodel_schemas.AiModelChatRequest.model_validate(
        {
            "user_id": 1,
            "conversation_id": 2,
            "message": "你好",
            "links": [],
        }
    )

    assert request_model.request_version == "v1"
    assert request_model.page_context is None
    assert request_model.model_dump(mode="json") == {
        "user_id": 1,
        "conversation_id": 2,
        "message": "你好",
        "links": [],
        "request_version": "v1",
        "page_context": None,
    }


def test_v2_page_context_round_trips_as_an_independent_structure() -> None:
    request_model = aimodel_schemas.AiModelChatRequest.model_validate(
        _request_payload()
    )
    serialized = request_model.model_dump(mode="json")
    restored = aimodel_schemas.AiModelChatRequest.model_validate(serialized)

    assert restored == request_model
    assert isinstance(restored.page_context, aimodel_schemas.AiModelPageContext)
    assert all(
        isinstance(candidate, aimodel_schemas.AiModelCandidateRef)
        for candidate in restored.page_context.candidate_refs
    )
    assert restored.page_context.candidate_refs[0].item_id == ("item_wireless_earbuds")
    assert restored.page_context.candidate_refs[1].item_id == 10002


@pytest.mark.parametrize(
    "page_context",
    [
        pytest.param(
            _context_payload(search_query="搜" * (MAX_SEARCH_QUERY_LENGTH + 1)),
            id="search-query-too-long",
        ),
        pytest.param(
            _context_payload(
                candidate_refs=[
                    {"item_id": index + 1} for index in range(MAX_CANDIDATES + 1)
                ]
            ),
            id="too-many-candidates",
        ),
        pytest.param(
            _context_payload(page_type="admin_console"),
            id="invalid-page-type",
        ),
        pytest.param(
            _context_payload(price=0.01, inventory=999, rating=5, delivery="today"),
            id="forged-business-facts",
        ),
        pytest.param(
            _context_payload(candidate_refs=[{"item_id": 1, "price": 0.01}]),
            id="unknown-nested-field",
        ),
        pytest.param(
            _context_payload(current_item_id=0),
            id="non-positive-current-item",
        ),
        pytest.param(
            _context_payload(candidate_refs=[{"item_id": -1}]),
            id="non-positive-candidate-item",
        ),
    ],
)
def test_invalid_or_untrusted_page_context_is_rejected(
    page_context: dict[str, Any],
) -> None:
    response = TestClient(app).post(
        "/AImodel/chat",
        json=_request_payload(page_context=page_context),
    )

    assert response.status_code == 422


def test_v1_cannot_smuggle_v2_context_or_server_policy_version() -> None:
    with pytest.raises(ValidationError):
        aimodel_schemas.AiModelChatRequest.model_validate(
            _request_payload(request_version="v1")
        )

    response = TestClient(app).post(
        "/AImodel/chat",
        json={**_request_payload(), "server_policy_version": "unsafe-client-choice"},
    )

    assert response.status_code == 422


def test_link_and_total_request_limits_are_enforced() -> None:
    client = TestClient(app)
    too_many_links = ["https://example.com/item"] * (MAX_LINKS + 1)
    one_oversized_link = "https://example.com/" + "a" * MAX_LINK_LENGTH
    individually_valid_but_oversized = [
        "https://example.com/" + str(index) + "-" + "a" * 1970
        for index in range(MAX_LINKS)
    ]

    assert (
        client.post(
            "/AImodel/chat", json=_request_payload(links=too_many_links)
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/AImodel/chat", json=_request_payload(links=[one_oversized_link])
        ).status_code
        == 422
    )
    oversized_payload = _request_payload(
        message="问" * 7000,
        links=individually_valid_but_oversized,
    )
    assert (
        len(
            json.dumps(
                oversized_payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        )
        > MAX_REQUEST_BYTES
    )
    assert client.post("/AImodel/chat", json=oversized_payload).status_code == 422


def test_missing_partial_and_unknown_context_remain_valid_v2_requests() -> None:
    request_model = aimodel_schemas.AiModelChatRequest

    without_context = request_model.model_validate(_request_payload(page_context=None))
    partial_context = request_model.model_validate(
        _request_payload(page_context={"page_type": "unknown"})
    )

    assert without_context.page_context is None
    assert partial_context.page_context.page_type == "unknown"
    assert partial_context.page_context.schema_version == "v1"


def test_router_passes_validated_context_to_service_without_flattening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router_module = importlib.import_module("app.routers.AImodel.router")
    captured: list[Any] = []

    def fake_stream_chat_events(request: Any, *, mock_api_url: str):
        captured.append(request)
        yield 'event: done\ndata: {"answer": "ok"}\n\n'

    monkeypatch.setattr(router_module, "ensure_aimodel_configured", lambda: None)
    monkeypatch.setattr(router_module, "stream_chat_events", fake_stream_chat_events)

    response = TestClient(app).post("/AImodel/chat", json=_request_payload())

    assert response.status_code == 200
    assert len(captured) == 1
    assert isinstance(captured[0].page_context, aimodel_schemas.AiModelPageContext)
    assert captured[0].page_context.search_query == "无线耳机"


def test_openapi_and_json_schema_match_frozen_contract_snapshots() -> None:
    assert JSON_SCHEMA_PATH.is_file()
    assert OPENAPI_PATH.is_file()
    expected_json_schema = json.loads(JSON_SCHEMA_PATH.read_text(encoding="utf-8"))
    expected_openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    actual_json_schema = aimodel_schemas.AiModelChatRequest.model_json_schema()
    actual_openapi = _contract_openapi_snapshot()

    assert actual_json_schema == expected_json_schema
    assert actual_openapi == expected_openapi
    assert actual_json_schema["additionalProperties"] is False
    assert actual_json_schema["properties"]["request_version"]["default"] == "v1"
    assert actual_json_schema["properties"]["links"]["maxItems"] == MAX_LINKS
    assert actual_json_schema["x-max-serialized-bytes"] == MAX_REQUEST_BYTES
    for name in (
        "AiModelCandidateRef",
        "AiModelChatRequest",
        "AiModelPageContext",
    ):
        assert actual_openapi["schemas"][name]["additionalProperties"] is False
