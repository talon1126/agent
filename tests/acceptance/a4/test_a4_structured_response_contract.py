"""Frozen acceptance contract for task A4."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[3]
AI_SERVICE_ROOT = ROOT / "services" / "ai-service"
sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.main import app  # noqa: E402
from app.routers.AImodel import memory as aimodel_memory  # noqa: E402
from app.routers.AImodel import schemas as aimodel_schemas  # noqa: E402
from app.routers.AImodel import service as aimodel_service  # noqa: E402


JSON_SCHEMA_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_response.schema.json"
OPENAPI_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_response.openapi.json"

PRODUCT_A = {
    "item_id": "sku-a",
    "item_name": "商品 A",
    "url": "https://shop.example.com/items/sku-a",
}
PRODUCT_B = {
    "item_id": "sku-b",
    "item_name": "商品 B",
    "url": "https://shop.example.com/items/sku-b",
}
EVIDENCE_A = {
    "evidence_id": "evidence-a",
    "source_type": "product_fact",
    "source_id": "snapshot:sku-a:price",
    "title": "商品 A 事实快照",
}


def _valid_payloads() -> dict[str, dict[str, Any]]:
    return {
        "answer": {
            "response_type": "answer",
            "answer": "这是普通回答。",
            "products": [deepcopy(PRODUCT_A)],
        },
        "clarification": {
            "response_type": "clarification",
            "answer": "你的预算范围是多少？",
            "options": [
                {
                    "option_id": "budget-low",
                    "label": "500 元以内",
                    "value": "budget<=500",
                }
            ],
        },
        "product_list": {
            "response_type": "product_list",
            "answer": "找到两个候选商品。",
            "products": [deepcopy(PRODUCT_A), deepcopy(PRODUCT_B)],
        },
        "comparison": {
            "response_type": "comparison",
            "answer": "商品 A 价格更低。",
            "products": [deepcopy(PRODUCT_A), deepcopy(PRODUCT_B)],
            "columns": [{"key": "price", "label": "价格"}],
            "rows": [
                {"item_id": "sku-a", "cells": {"price": "99 元"}},
                {"item_id": "sku-b", "cells": {"price": "129 元"}},
            ],
        },
        "recommendation": {
            "response_type": "recommendation",
            "answer": "更推荐商品 A。",
            "candidates": [deepcopy(PRODUCT_A), deepcopy(PRODUCT_B)],
            "recommendations": [
                {
                    "item_id": "sku-a",
                    "reason": "满足预算并且有事实依据。",
                    "evidence_ids": ["evidence-a"],
                }
            ],
            "evidence": [deepcopy(EVIDENCE_A)],
        },
        "action_preview": {
            "response_type": "action_preview",
            "answer": "确认后可将商品 A 加入购物车。",
            "products": [deepcopy(PRODUCT_A)],
            "action": {
                "action_type": "add_to_cart",
                "action_token": "action-token-a",
                "summary": "将商品 A 加入购物车",
                "target_item_id": "sku-a",
            },
        },
        "action_result": {
            "response_type": "action_result",
            "answer": "商品 A 已加入购物车。",
            "products": [deepcopy(PRODUCT_A)],
            "action_token": "action-token-a",
            "status": "success",
            "result_summary": "加购成功",
        },
        "fallback": {
            "response_type": "fallback",
            "answer": "暂时无法完成这次请求。",
            "reason_code": "insufficient_evidence",
        },
    }


def _invalid_payloads() -> dict[str, dict[str, Any]]:
    payloads = _valid_payloads()
    payloads["answer"]["answer"] = ""
    payloads["clarification"]["options"] = []
    payloads["product_list"]["products"] = [PRODUCT_A, PRODUCT_A]
    payloads["comparison"]["rows"][0]["cells"] = {"weight": "1 kg"}
    payloads["recommendation"]["recommendations"][0]["item_id"] = "sku-missing"
    payloads["action_preview"]["action"]["action_token"] = ""
    payloads["action_result"]["action_token"] = ""
    payloads["fallback"]["reason_code"] = ""
    return payloads


def _payload_adapter() -> Any:
    return aimodel_schemas.AiModelResponsePayloadAdapter


def _parse_sse_event(raw_event: str) -> tuple[str, dict[str, Any]]:
    lines = raw_event.strip().splitlines()
    event = next(
        line.removeprefix("event: ") for line in lines if line.startswith("event: ")
    )
    data = json.loads(
        next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
    )
    return event, data


@pytest.mark.parametrize("response_type", sorted(_valid_payloads()))
def test_all_response_types_accept_valid_payloads(response_type: str) -> None:
    payload = _payload_adapter().validate_python(_valid_payloads()[response_type])
    response = aimodel_schemas.AiModelChatResponse.from_payload(
        payload,
        conversation_id=42,
    )

    assert payload.response_type == response_type
    assert response.response_version == "v1"
    assert response.response_type == response_type
    assert response.answer == payload.answer
    assert response.payload == payload
    assert response.conversation_id == 42


@pytest.mark.parametrize("response_type", sorted(_invalid_payloads()))
def test_each_response_type_rejects_an_invalid_payload(response_type: str) -> None:
    with pytest.raises(ValidationError):
        _payload_adapter().validate_python(_invalid_payloads()[response_type])


def test_top_level_response_rejects_payload_type_and_legacy_field_mismatches() -> None:
    payload = _valid_payloads()["clarification"]
    valid = aimodel_schemas.AiModelChatResponse.from_payload(
        _payload_adapter().validate_python(payload),
        conversation_id=1,
    ).model_dump(mode="json")

    wrong_type = deepcopy(valid)
    wrong_type["response_type"] = "answer"
    with pytest.raises(ValidationError):
        aimodel_schemas.AiModelChatResponse.model_validate(wrong_type)

    wrong_answer = deepcopy(valid)
    wrong_answer["answer"] = "与 payload 矛盾的文本"
    with pytest.raises(ValidationError):
        aimodel_schemas.AiModelChatResponse.model_validate(wrong_answer)

    wrong_links = deepcopy(valid)
    wrong_links["recommended_links"] = [PRODUCT_A]
    with pytest.raises(ValidationError):
        aimodel_schemas.AiModelChatResponse.model_validate(wrong_links)


def test_cross_type_product_comparison_recommendation_and_evidence_invariants() -> None:
    duplicate_products = _valid_payloads()["comparison"]
    duplicate_products["products"] = [PRODUCT_A, PRODUCT_A]
    with pytest.raises(ValidationError):
        _payload_adapter().validate_python(duplicate_products)

    missing_column = _valid_payloads()["comparison"]
    missing_column["rows"][0]["cells"] = {}
    with pytest.raises(ValidationError):
        _payload_adapter().validate_python(missing_column)

    unknown_recommendation = _valid_payloads()["recommendation"]
    unknown_recommendation["recommendations"][0]["item_id"] = "not-a-candidate"
    with pytest.raises(ValidationError):
        _payload_adapter().validate_python(unknown_recommendation)

    empty_pointer = _valid_payloads()["recommendation"]
    empty_pointer["evidence"][0]["source_id"] = ""
    with pytest.raises(ValidationError):
        _payload_adapter().validate_python(empty_pointer)


def test_legacy_answer_and_recommended_links_are_derived_from_payload() -> None:
    payload = _payload_adapter().validate_python(_valid_payloads()["recommendation"])
    response = aimodel_schemas.AiModelChatResponse.from_payload(payload)

    assert response.answer == "更推荐商品 A。"
    assert [link.item_id for link in response.recommended_links] == ["sku-a"]
    assert response.recommended_links[0].url == PRODUCT_A["url"]


def test_sse_done_contains_only_a_valid_versioned_response(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    events = [
        _parse_sse_event(event)
        for event in aimodel_service.stream_chat_events(
            aimodel_schemas.AiModelChatRequest(user_id=1, message="你好"),
            mock_api_url="http://mock-api",
            streaming_agent_runner=lambda _request, _results: ["普通回答"],
            memory_store=aimodel_memory.NoopAiModelMemoryStore(),
        )
    ]
    event_name, done = events[-1]

    assert event_name == "done"
    validated = aimodel_schemas.AiModelChatResponse.model_validate(done)
    assert validated.response_version == "v1"
    assert validated.response_type == "answer"
    assert validated.answer == "普通回答"


def test_sse_serialization_failure_emits_controlled_error_without_done(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    def fail_serialization(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TypeError("controlled serialization failure")

    monkeypatch.setattr(
        aimodel_schemas.AiModelChatResponse,
        "model_dump",
        fail_serialization,
    )
    events = [
        _parse_sse_event(event)
        for event in aimodel_service.stream_chat_events(
            aimodel_schemas.AiModelChatRequest(user_id=1, message="你好"),
            mock_api_url="http://mock-api",
            streaming_agent_runner=lambda _request, _results: ["普通回答"],
            memory_store=aimodel_memory.NoopAiModelMemoryStore(),
        )
    ]

    assert events[-1] == (
        "error",
        {"content": "AImodel response validation failed."},
    )
    assert all(event != "done" for event, _data in events)


def test_old_messages_receive_structured_text_fallback() -> None:
    store = aimodel_memory.NoopAiModelMemoryStore()
    conversation_id = store.ensure_conversation(
        None,
        user_id=1,
        first_message="旧会话",
    )
    store.append_assistant_message(
        conversation_id,
        user_id=1,
        content="旧版文本回答",
        recommended_links=[PRODUCT_A],
    )

    message = store.list_messages(conversation_id, user_id=1)[0]
    response = aimodel_schemas.AiModelChatResponse.model_validate(
        message.structured_response
    )

    assert response.response_type == "answer"
    assert response.answer == "旧版文本回答"
    assert [link.item_id for link in response.recommended_links] == ["sku-a"]


def test_structured_message_migration_is_idempotent_and_non_destructive() -> None:
    statements = [
        " ".join(statement.split())
        for statement in aimodel_memory.POSTGRES_AIMODEL_MEMORY_SCHEMA_SQL
        if "structured_response" in statement
    ]

    assert statements
    assert any(
        "ADD COLUMN IF NOT EXISTS structured_response JSONB" in statement
        for statement in statements
    )
    assert all("TRUNCATE" not in statement.upper() for statement in statements)
    assert all("DROP TABLE" not in statement.upper() for statement in statements)


def test_response_json_schema_and_openapi_snapshots_match_runtime_contract() -> None:
    assert JSON_SCHEMA_PATH.is_file()
    assert OPENAPI_PATH.is_file()
    json_schema = json.loads(JSON_SCHEMA_PATH.read_text(encoding="utf-8"))
    openapi_snapshot = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    operation = app.openapi()["paths"]["/AImodel/chat"]["post"]

    assert json_schema == aimodel_schemas.AiModelChatResponse.model_json_schema()
    assert openapi_snapshot == operation["x-sse-events"]["done"]["schema"]
    assert openapi_snapshot == json_schema
    assert (
        json_schema["properties"]["payload"]["discriminator"]["propertyName"]
        == "response_type"
    )
    assert set(json_schema["required"]) >= {
        "answer",
        "payload",
        "recommended_links",
        "response_type",
    }
