from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.main import app
from app.routers.AImodel.memory import (
    POSTGRES_AIMODEL_MEMORY_SCHEMA_SQL,
    NoopAiModelMemoryStore,
)
from app.routers.AImodel.schemas import (
    AiModelActionPreview,
    AiModelActionPreviewPayload,
    AiModelActionResultPayload,
    AiModelAnswerPayload,
    AiModelChatRequest,
    AiModelChatResponse,
    AiModelClarificationOption,
    AiModelClarificationPayload,
    AiModelComparisonColumn,
    AiModelComparisonPayload,
    AiModelComparisonRow,
    AiModelEvidenceReference,
    AiModelFallbackPayload,
    AiModelProductListPayload,
    AiModelProductRef,
    AiModelRecommendationPayload,
    AiModelRecommendationReason,
    AiModelResponsePayloadAdapter,
)
from app.routers.AImodel.service import stream_chat_events


ROOT = Path(__file__).resolve().parents[3]
JSON_SCHEMA_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_response.schema.json"
OPENAPI_PATH = ROOT / "fixtures" / "contracts" / "aimodel_chat_response.openapi.json"


def _product(item_id: str, name: str) -> AiModelProductRef:
    return AiModelProductRef(
        item_id=item_id,
        item_name=name,
        url=f"https://shop.example.com/items/{item_id}",
    )


def _payloads() -> list[Any]:
    product_a = _product("sku-a", "商品 A")
    product_b = _product("sku-b", "商品 B")
    evidence = AiModelEvidenceReference(
        evidence_id="fact-a",
        source_type="product_fact",
        source_id="snapshot:sku-a",
    )
    return [
        AiModelAnswerPayload(answer="普通回答", products=[product_a]),
        AiModelClarificationPayload(
            answer="请选择预算",
            options=[
                AiModelClarificationOption(
                    option_id="budget-500",
                    label="500 元以内",
                    value="budget<=500",
                )
            ],
        ),
        AiModelProductListPayload(
            answer="候选商品",
            products=[product_a, product_b],
        ),
        AiModelComparisonPayload(
            answer="对比结果",
            products=[product_a, product_b],
            columns=[AiModelComparisonColumn(key="price", label="价格")],
            rows=[
                AiModelComparisonRow(item_id="sku-a", cells={"price": "99 元"}),
                AiModelComparisonRow(item_id="sku-b", cells={"price": "129 元"}),
            ],
        ),
        AiModelRecommendationPayload(
            answer="推荐商品 A",
            candidates=[product_a, product_b],
            recommendations=[
                AiModelRecommendationReason(
                    item_id="sku-a",
                    reason="满足预算",
                    evidence_ids=["fact-a"],
                )
            ],
            evidence=[evidence],
        ),
        AiModelActionPreviewPayload(
            answer="等待确认",
            products=[product_a],
            action=AiModelActionPreview(
                action_type="add_to_cart",
                action_token="token-a",
                summary="加入购物车",
                target_item_id="sku-a",
            ),
        ),
        AiModelActionResultPayload(
            answer="操作完成",
            products=[product_a],
            action_token="token-a",
            status="success",
            result_summary="加购成功",
        ),
        AiModelFallbackPayload(
            answer="暂时无法处理",
            reason_code="insufficient_evidence",
        ),
    ]


@pytest.mark.parametrize("payload", _payloads(), ids=lambda item: item.response_type)
def test_payload_types_round_trip_through_discriminated_contract(payload: Any) -> None:
    dumped = payload.model_dump(mode="json")
    restored = AiModelResponsePayloadAdapter.validate_python(dumped)
    response = AiModelChatResponse.from_payload(restored, conversation_id=7)

    assert restored == payload
    assert response.response_type == payload.response_type
    assert response.answer == payload.answer
    assert response.conversation_id == 7


def test_response_rejects_contradictory_legacy_projection() -> None:
    response = AiModelChatResponse.from_payload(
        AiModelAnswerPayload(answer="可信回答")
    ).model_dump(mode="json")
    response["answer"] = "冲突回答"

    with pytest.raises(ValidationError, match="derived from payload"):
        AiModelChatResponse.model_validate(response)


def test_comparison_matrix_and_recommendation_references_are_closed() -> None:
    products = [_product("sku-a", "商品 A"), _product("sku-b", "商品 B")]

    with pytest.raises(ValidationError, match="cells must match columns"):
        AiModelComparisonPayload(
            answer="错误矩阵",
            products=products,
            columns=[AiModelComparisonColumn(key="price", label="价格")],
            rows=[
                AiModelComparisonRow(item_id="sku-a", cells={"weight": "1 kg"}),
                AiModelComparisonRow(item_id="sku-b", cells={"price": "99 元"}),
            ],
        )

    with pytest.raises(ValidationError, match="must be candidates"):
        AiModelRecommendationPayload(
            answer="错误推荐",
            candidates=[products[0]],
            recommendations=[
                AiModelRecommendationReason(item_id="sku-b", reason="不存在")
            ],
        )


def test_legacy_response_factory_derives_links_from_validated_products() -> None:
    response = AiModelChatResponse.from_legacy(
        answer="旧版回答",
        recommended_links=[
            {
                "item_id": "sku-a",
                "item_name": "商品 A",
                "url": "https://shop.example.com/items/sku-a",
            }
        ],
        conversation_id=9,
    )

    assert response.response_type == "answer"
    assert response.payload.products[0].item_id == "sku-a"
    assert response.recommended_links[0].item_id == "sku-a"


def test_noop_memory_persists_validated_response_and_falls_back_for_legacy() -> None:
    store = NoopAiModelMemoryStore()
    conversation_id = store.ensure_conversation(None, user_id=1, first_message="你好")
    structured = AiModelChatResponse.from_payload(
        AiModelFallbackPayload(answer="证据不足", reason_code="no_evidence"),
        conversation_id=conversation_id,
    ).model_dump(mode="json")
    store.append_assistant_message(
        conversation_id,
        user_id=1,
        content="证据不足",
        recommended_links=[],
        structured_response=structured,
    )
    store.append_assistant_message(
        conversation_id,
        user_id=1,
        content="旧版回答",
        recommended_links=[],
    )

    messages = store.list_messages(conversation_id, user_id=1)
    assert messages[0].structured_response == structured
    fallback = AiModelChatResponse.model_validate(messages[1].structured_response)
    assert fallback.response_type == "answer"
    assert fallback.answer == "旧版回答"


def test_database_upgrade_adds_json_without_destructive_migration() -> None:
    migration = " ".join(
        " ".join(statement.split())
        for statement in POSTGRES_AIMODEL_MEMORY_SCHEMA_SQL
        if "ADD COLUMN IF NOT EXISTS structured_response" in statement
    )

    assert "ALTER TABLE message" in migration
    assert "structured_response JSONB" in migration
    assert "TRUNCATE" not in migration.upper()


def test_stream_done_is_a_complete_valid_response(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    events = list(
        stream_chat_events(
            AiModelChatRequest(user_id=1, message="你好"),
            mock_api_url="http://mock-api",
            streaming_agent_runner=lambda _request, _results: ["完整回答"],
            memory_store=NoopAiModelMemoryStore(),
        )
    )
    done = json.loads(events[-1].split("data: ", 1)[1])

    assert AiModelChatResponse.model_validate(done).answer == "完整回答"


def test_response_contract_snapshots_equal_runtime_schema() -> None:
    json_schema = json.loads(JSON_SCHEMA_PATH.read_text(encoding="utf-8"))
    openapi_schema = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

    assert json_schema == AiModelChatResponse.model_json_schema()
    assert openapi_schema == json_schema
    assert (
        app.openapi()["paths"]["/AImodel/chat"]["post"]["x-sse-events"]["done"][
            "schema"
        ]
        == json_schema
    )
