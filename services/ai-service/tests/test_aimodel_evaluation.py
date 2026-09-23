from __future__ import annotations

import json

import pytest

from app.routers.AImodel.evaluation import (
    AiModelEvaluationError,
    collect_chat_evaluation,
    evaluate_chat_non_streaming,
)
from app.routers.AImodel.memory import NoopAiModelMemoryStore
from app.routers.AImodel.schemas import AiModelChatRequest, AiModelToolResult


def _event(name: str, data: dict[str, object]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _done(*, answer: str = "推荐减压魔方。") -> dict[str, object]:
    return {
        "response_version": "v1",
        "conversation_id": 1,
        "response_type": "answer",
        "payload": {
            "schema_version": "v1",
            "response_type": "answer",
            "answer": answer,
            "evidence": [],
            "products": [],
        },
        "answer": answer,
        "recommended_links": [],
    }


def test_non_streaming_evaluation_runs_production_stream_and_returns_one_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    def fake_runner(
        request: AiModelChatRequest,
        tool_results: list[AiModelToolResult],
    ) -> list[str]:
        assert request.message == "推荐一个解压玩具"
        assert tool_results == []
        return ["推荐", "减压魔方。"]

    result = evaluate_chat_non_streaming(
        AiModelChatRequest(user_id=1, message="推荐一个解压玩具"),
        mock_api_url="http://mock-api",
        streaming_agent_runner=fake_runner,
        memory_store=NoopAiModelMemoryStore(),
    )

    assert result.output == "推荐减压魔方。"
    assert result.context == []
    assert result.toolCalls == []
    assert result.metadata["response_type"] == "answer"
    assert result.metadata["payload"]["answer"] == result.output


def test_evaluation_exports_final_rag_context_citations_and_safe_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    def fake_runner(
        _request: AiModelChatRequest,
        tool_results: list[AiModelToolResult],
    ) -> list[str]:
        tool_results.append(
            AiModelToolResult(
                tool="rag_tool",
                ok=True,
                input="联系 13800138000 查询订单 ORDER-SECRET-42 的退货规则",
                data={
                    "trace_id": "query-eval-1",
                    "content": "[1] 退货前请保持商品和附件完整。",
                    "citations": [
                        {
                            "document_id": "doc-1",
                            "chunk_id": "chunk-1",
                            "title": "退货规则",
                            "score": 0.95,
                            "trace_id": "query-eval-1",
                            "private": "must-not-leak",
                        }
                    ],
                    "is_empty": False,
                },
            )
        )
        return ["退货前请保持商品和附件完整。"]

    result = evaluate_chat_non_streaming(
        AiModelChatRequest(user_id=1, message="退货规则"),
        mock_api_url="http://mock-api",
        streaming_agent_runner=fake_runner,
        memory_store=NoopAiModelMemoryStore(),
    )

    assert result.context == ["[1] 退货前请保持商品和附件完整。"]
    assert result.toolCalls[0]["name"] == "rag_lookup"
    assert result.toolCalls[0]["status"] == "succeeded"
    assert result.toolCalls[0]["arguments"]["query"] == (
        "联系 [REDACTED_PHONE] 查询[REDACTED_ORDER] 的退货规则"
    )
    evaluation = result.metadata["evaluation"]
    assert evaluation["contextRefs"][0]["sourceId"] == "query-eval-1"
    assert evaluation["retrievalEvidence"][0]["citations"] == [
        {
            "document_id": "doc-1",
            "chunk_id": "chunk-1",
            "title": "退货规则",
            "score": 0.95,
            "trace_id": "query-eval-1",
        }
    ]
    rendered = result.model_dump_json()
    assert "13800138000" not in rendered
    assert "ORDER-SECRET-42" not in rendered
    assert "must-not-leak" not in rendered


def test_evaluation_rejects_agent_error_without_echoing_payload() -> None:
    with pytest.raises(AiModelEvaluationError, match="^agent_error$") as failure:
        collect_chat_evaluation(
            [_event("error", {"content": "private model failure"})]
        )

    assert "private model failure" not in str(failure.value)


def test_evaluation_redacts_action_tokens_from_metadata() -> None:
    done = {
        "response_version": "v1",
        "conversation_id": 1,
        "response_type": "action_preview",
        "payload": {
            "schema_version": "v1",
            "response_type": "action_preview",
            "answer": "确认后加入购物车。",
            "evidence": [],
            "products": [],
            "action": {
                "action_type": "add_to_cart",
                "action_token": "private-action-token",
                "summary": "加入购物车",
            },
        },
        "answer": "确认后加入购物车。",
        "recommended_links": [],
    }

    result = collect_chat_evaluation([_event("done", done)])

    assert result.metadata["payload"]["action"]["action_token"] == "[REDACTED]"
    assert "private-action-token" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("events", "code"),
    [
        ([_event("status", {"content": "working"})], "missing_done"),
        ([_event("done", _done()), _event("done", _done())], "duplicate_done"),
        (["event: done\ndata: not-json\n\n"], "invalid_terminal_event"),
        ([_event("done", {"answer": "incomplete"})], "invalid_done"),
    ],
)
def test_evaluation_rejects_non_terminal_or_invalid_streams(
    events: list[str],
    code: str,
) -> None:
    with pytest.raises(AiModelEvaluationError, match=f"^{code}$"):
        collect_chat_evaluation(events)
