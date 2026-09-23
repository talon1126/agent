from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.routers.AImodel.schemas import (
    MAX_AIMODEL_CANDIDATES,
    MAX_AIMODEL_LINKS,
    MAX_AIMODEL_REQUEST_BYTES,
    AiModelCandidateRef,
    AiModelChatRequest,
    AiModelPageContext,
)
from app.routers.AImodel.service import _build_user_prompt, handle_chat


ROOT = Path(__file__).resolve().parents[3]
JSON_SCHEMA_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_request.schema.json"
OPENAPI_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_request.openapi.json"


def _v2_request(**context_overrides: object) -> AiModelChatRequest:
    context = {
        "page_type": "search",
        "route": "/search",
        "search_query": "无线耳机",
        "current_item_id": "item_wireless_earbuds",
        "candidate_refs": [{"item_id": "item_wireless_earbuds"}],
        "source_event": "search_results_viewed",
        "client_time": "2026-09-20T10:00:00+08:00",
    }
    context.update(context_overrides)
    return AiModelChatRequest.model_validate(
        {
            "user_id": 1,
            "message": "帮我看看当前页面",
            "request_version": "v2",
            "page_context": context,
        }
    )


def test_legacy_request_stays_valid_and_defaults_to_v1() -> None:
    request = AiModelChatRequest(user_id=1, message="你好", links=[])

    assert request.request_version == "v1"
    assert request.page_context is None
    assert request.links == []


def test_candidate_and_context_models_forbid_untrusted_fields() -> None:
    with pytest.raises(ValidationError, match="price"):
        AiModelCandidateRef.model_validate({"item_id": 1, "price": 0.01})
    with pytest.raises(ValidationError, match="inventory"):
        AiModelPageContext.model_validate(
            {"page_type": "product", "current_item_id": 1, "inventory": 99}
        )


@pytest.mark.parametrize("item_id", [0, -1, "0", "-1", True])
def test_product_references_reject_non_positive_or_boolean_ids(item_id: object) -> None:
    with pytest.raises(ValidationError):
        AiModelCandidateRef(item_id=item_id)


def test_context_limits_are_reflected_in_runtime_validation() -> None:
    with pytest.raises(ValidationError):
        _v2_request(
            candidate_refs=[
                {"item_id": index + 1} for index in range(MAX_AIMODEL_CANDIDATES + 1)
            ]
        )
    with pytest.raises(ValidationError):
        AiModelChatRequest(
            user_id=1,
            message="hello",
            links=["https://example.com/item"] * (MAX_AIMODEL_LINKS + 1),
        )


def test_v1_rejects_page_context_but_v2_allows_missing_context() -> None:
    with pytest.raises(ValidationError, match="requires request_version v2"):
        AiModelChatRequest(
            user_id=1,
            message="hello",
            request_version="v1",
            page_context=AiModelPageContext(page_type="unknown"),
        )

    request = AiModelChatRequest(
        user_id=1,
        message="hello",
        request_version="v2",
    )
    assert request.page_context is None


def test_structured_context_reaches_service_without_prompt_json(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    request = _v2_request()
    observed: list[AiModelPageContext | None] = []

    def fake_agent_runner(request: AiModelChatRequest, tool_results: list) -> str:
        assert tool_results == []
        observed.append(request.page_context)
        return "普通聊天回答"

    response = handle_chat(
        request,
        mock_api_url="http://mock-api",
        agent_runner=fake_agent_runner,
    )

    assert response.answer == "普通聊天回答"
    assert observed == [request.page_context]
    prompt = _build_user_prompt(request)
    assert "page_context" not in prompt
    assert "candidate_refs" not in prompt
    assert json.dumps(request.page_context.model_dump(mode="json")) not in prompt


def test_old_http_request_reaches_configuration_gate_not_validation() -> None:
    response = TestClient(app).post(
        "/AImodel/chat",
        json={"user_id": 1, "message": "你好", "links": []},
    )

    assert response.status_code == 503


def test_raw_http_body_limit_cannot_be_bypassed_with_json_whitespace() -> None:
    compact_payload = json.dumps(
        {"user_id": 1, "message": "hello", "links": []},
        separators=(",", ":"),
    )
    padded_payload = " " * MAX_AIMODEL_REQUEST_BYTES + compact_payload

    response = TestClient(app).post(
        "/AImodel/chat",
        content=padded_payload,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == (
        f"AImodel request exceeds {MAX_AIMODEL_REQUEST_BYTES} bytes."
    )


def test_contract_snapshots_are_current_and_bounded() -> None:
    schema = json.loads(JSON_SCHEMA_PATH.read_text(encoding="utf-8"))
    openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

    assert schema == AiModelChatRequest.model_json_schema()
    assert schema["x-max-serialized-bytes"] == MAX_AIMODEL_REQUEST_BYTES
    assert schema["additionalProperties"] is False
    assert openapi["request_body"]["required"] is True
    assert set(openapi["schemas"]) == {
        "AiModelCandidateRef",
        "AiModelChatRequest",
        "AiModelPageContext",
    }
