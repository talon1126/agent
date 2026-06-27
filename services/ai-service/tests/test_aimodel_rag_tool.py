from typing import Any
import importlib
import threading

from app.routers.AImodel.memory import NoopAiModelMemoryStore
from app.routers.AImodel.schemas import AiModelChatRequest, AiModelToolResult
from app.routers.AImodel.service import (
    SYSTEM_PROMPT,
    _query_trace_ids_from_tool_results,
    build_rag_tool,
    stream_chat_events,
)
from app.routers.AImodel.tools import (
    PersistentMcpRagKnowledgeClient,
    StdioMcpRagKnowledgeClient,
    close_rag_knowledge_client,
    get_rag_knowledge_client,
    search_shopping_guides,
)
import app.routers.AImodel.tools as aimodel_tools

aimodel_app_main = importlib.import_module("app.main")


class FakeRagKnowledgeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def query_knowledge_hub(
        self,
        *,
        query: str,
        collection: str,
        top_k: int,
        no_rerank: bool,
        include_image_base64: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "query": query,
                "collection": collection,
                "top_k": top_k,
                "no_rerank": no_rerank,
                "include_image_base64": include_image_base64,
            }
        )
        return self.payload


def test_search_shopping_guides_returns_public_rag_tool_result() -> None:
    client = FakeRagKnowledgeClient(
        {
            "ok": True,
            "trace_id": "query-trace-1",
            "content": "[1] 无线耳机选购时应关注佩戴舒适度。",
            "citations": [
                {
                    "document_id": "doc-1",
                    "chunk_id": "chunk-1",
                    "title": "无线耳机选购指南",
                    "section_path": ["佩戴体验"],
                    "score": 0.92,
                    "trace_id": "query-trace-1",
                }
            ],
            "images": [
                {
                    "image_id": "image-1",
                    "chunk_ids": ["chunk-1"],
                    "quality_status": "success",
                }
            ],
            "is_empty": False,
        }
    )

    result = search_shopping_guides(
        "无线耳机怎么选",
        rag_client=client,
        top_k=3,
        no_rerank=True,
    )

    assert result.tool == "search_shopping_guides"
    assert result.ok is True
    assert result.input == "无线耳机怎么选"
    assert result.data == {
        "trace_id": "query-trace-1",
        "content": "[1] 无线耳机选购时应关注佩戴舒适度。",
        "citations": [
            {
                "document_id": "doc-1",
                "chunk_id": "chunk-1",
                "title": "无线耳机选购指南",
                "section_path": ["佩戴体验"],
                "score": 0.92,
                "trace_id": "query-trace-1",
            }
        ],
        "images": [
            {
                "image_id": "image-1",
                "chunk_ids": ["chunk-1"],
                "quality_status": "success",
            }
        ],
        "is_empty": False,
    }
    assert client.calls == [
        {
            "query": "无线耳机怎么选",
            "collection": "shopping_guides",
            "top_k": 3,
            "no_rerank": True,
            "include_image_base64": False,
        }
    ]


def test_search_shopping_guides_returns_readable_business_error() -> None:
    client = FakeRagKnowledgeClient(
        {
            "ok": False,
            "error": {
                "code": "no_collections",
                "message": "no searchable collections are available",
            },
        }
    )

    result = search_shopping_guides("无线耳机怎么选", rag_client=client)

    assert result.tool == "search_shopping_guides"
    assert result.ok is False
    assert result.error == "no_collections: no searchable collections are available"
    assert result.data == {
        "error": {
            "code": "no_collections",
            "message": "no searchable collections are available",
        }
    }


def test_search_shopping_guides_rejects_blank_query_without_calling_rag() -> None:
    client = FakeRagKnowledgeClient({"ok": True})

    result = search_shopping_guides("   ", rag_client=client)

    assert result.ok is False
    assert result.error == "query_required"
    assert client.calls == []


def test_stdio_mcp_rag_client_defaults_to_rag_uv_project(tmp_path) -> None:
    client = StdioMcpRagKnowledgeClient(cwd=tmp_path)

    assert client._command == "uv"
    assert client._args[:3] == ["run", "--project", str(tmp_path.resolve())]
    assert client._args[-4:] == [
        "-m",
        "src.mcp_server.server",
        "--transport",
        "stdio",
    ]


def test_persistent_mcp_rag_client_reuses_session_until_close(tmp_path) -> None:
    """Verify the production RAG MCP client keeps one session across calls."""

    calls: list[dict[str, Any]] = []
    starts: list[str] = []
    closes: list[str] = []

    async def fake_call_tool(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {
            "ok": True,
            "trace_id": f"query-{len(calls)}",
            "content": "RAG context",
            "citations": [],
            "images": [],
            "is_empty": False,
        }

    client = PersistentMcpRagKnowledgeClient(
        cwd=tmp_path,
        session_factory=lambda: fake_call_tool,
        on_session_start=lambda: starts.append("start"),
        on_session_close=lambda: closes.append("close"),
    )

    first = client.query_knowledge_hub(
        query="无线耳机怎么选",
        collection="shopping_guides",
        top_k=5,
        no_rerank=False,
        include_image_base64=False,
    )
    second = client.query_knowledge_hub(
        query="办公室安静解压玩具",
        collection="shopping_guides",
        top_k=3,
        no_rerank=True,
        include_image_base64=False,
    )
    client.close()
    third = client.query_knowledge_hub(
        query="人体工学键盘怎么选",
        collection="shopping_guides",
        top_k=2,
        no_rerank=False,
        include_image_base64=False,
    )
    client.close()

    assert first["trace_id"] == "query-1"
    assert second["trace_id"] == "query-2"
    assert third["trace_id"] == "query-3"
    assert starts == ["start", "start"]
    assert closes == ["close", "close"]
    assert calls == [
        {
            "query": "无线耳机怎么选",
            "collection": "shopping_guides",
            "top_k": 5,
            "no_rerank": False,
            "include_image_base64": False,
            "request_source": "aimodel",
        },
        {
            "query": "办公室安静解压玩具",
            "collection": "shopping_guides",
            "top_k": 3,
            "no_rerank": True,
            "include_image_base64": False,
            "request_source": "aimodel",
        },
        {
            "query": "人体工学键盘怎么选",
            "collection": "shopping_guides",
            "top_k": 2,
            "no_rerank": False,
            "include_image_base64": False,
            "request_source": "aimodel",
        },
    ]


def test_fastapi_shutdown_closes_persistent_rag_client(monkeypatch) -> None:
    """Ensure ai-service shutdown releases the long-lived RAG MCP client."""

    calls: list[str] = []
    monkeypatch.setattr(
        aimodel_app_main,
        "close_rag_knowledge_client",
        lambda: calls.append("closed"),
    )

    aimodel_app_main.close_aimodel_rag_client()

    assert calls == ["closed"]


def test_close_rag_knowledge_client_does_not_create_unused_client(monkeypatch) -> None:
    """Closing an unused process-wide RAG client must not start MCP resources."""

    get_rag_knowledge_client.cache_clear()

    def fail_if_constructed(*args: Any, **kwargs: Any) -> PersistentMcpRagKnowledgeClient:
        raise AssertionError("shutdown should not create a new RAG MCP client")

    monkeypatch.setattr(aimodel_tools, "PersistentMcpRagKnowledgeClient", fail_if_constructed)

    close_rag_knowledge_client()

    assert get_rag_knowledge_client.cache_info().currsize == 0


def test_persistent_mcp_rag_client_cleans_loop_when_session_start_fails(tmp_path) -> None:
    """A failed MCP startup must not leave the background event loop alive."""

    before_count = sum(
        1
        for thread in threading.enumerate()
        if thread.name == "aimodel-rag-mcp-client"
    )

    def fail_to_create_session() -> Any:
        raise RuntimeError("mcp startup failed")

    client = PersistentMcpRagKnowledgeClient(
        cwd=tmp_path,
        session_factory=fail_to_create_session,
    )

    try:
        client.query_knowledge_hub(
            query="无线耳机怎么选",
            collection="shopping_guides",
            top_k=5,
            no_rerank=False,
            include_image_base64=False,
        )
    except RuntimeError as error:
        assert str(error) == "mcp startup failed"
    else:
        raise AssertionError("query_knowledge_hub should propagate startup failure")

    after_count = sum(
        1
        for thread in threading.enumerate()
        if thread.name == "aimodel-rag-mcp-client"
    )
    assert after_count == before_count


def test_build_rag_tool_invokes_search_shopping_guides_and_tracks_trace() -> None:
    tool_results: list[AiModelToolResult] = []
    client = FakeRagKnowledgeClient(
        {
            "ok": True,
            "trace_id": "query-trace-agent",
            "content": "[1] 降噪耳机应关注佩戴舒适度。",
            "citations": [],
            "images": [],
            "is_empty": False,
        }
    )

    rag_tool = build_rag_tool(tool_results, rag_client=client)
    payload = rag_tool.invoke({"query": "降噪耳机怎么选"})

    assert rag_tool.name == "search_shopping_guides"
    assert payload["tool"] == "search_shopping_guides"
    assert payload["ok"] is True
    assert payload["data"]["trace_id"] == "query-trace-agent"
    assert [result.tool for result in tool_results] == ["search_shopping_guides"]
    assert _query_trace_ids_from_tool_results(tool_results) == ["query-trace-agent"]


def test_query_trace_ids_from_tool_results_deduplicates_rag_traces() -> None:
    tool_results = [
        AiModelToolResult(
            tool="search_shopping_guides",
            ok=True,
            input="无线耳机",
            data={"trace_id": "query-a"},
        ),
        AiModelToolResult(
            tool="search_shopping_guides",
            ok=True,
            input="蓝牙耳机",
            data={"trace_id": "query-a"},
        ),
        AiModelToolResult(
            tool="search_products",
            ok=True,
            input="蓝牙耳机",
            data={"items": [], "trace_id": "product-api-trace"},
        ),
    ]

    assert _query_trace_ids_from_tool_results(tool_results) == ["query-a"]


def test_stream_chat_force_rag_prefetches_requested_collection(monkeypatch) -> None:
    """Evaluation mode should force one RAG call before agent generation."""

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    captured: dict[str, Any] = {}

    def fake_streaming_agent_runner(
        request: AiModelChatRequest,
        tool_results: list[AiModelToolResult],
    ) -> list[str]:
        captured["tool_results_before_agent"] = [result.model_dump() for result in tool_results]
        return ["这是最终回答。"]

    def fake_search_shopping_guides(**kwargs: Any) -> AiModelToolResult:
        captured["rag_call"] = kwargs
        return AiModelToolResult(
            tool="search_shopping_guides",
            ok=True,
            input=kwargs["query"],
            data={
                "trace_id": "query-forced",
                "content": "[1] 办公椅应关注腰靠。",
                "citations": [],
                "images": [],
                "is_empty": False,
            },
        )

    monkeypatch.setattr(
        "app.routers.AImodel.service.run_search_shopping_guides",
        fake_search_shopping_guides,
    )

    events = list(
        stream_chat_events(
            AiModelChatRequest(
                user_id=1,
                message="每天久坐办公，办公椅最应该看哪些地方？",
                links=[],
                force_rag=True,
                rag_collection="shopping_guides",
            ),
            mock_api_url="http://mock-api",
            streaming_agent_runner=fake_streaming_agent_runner,
            memory_store=NoopAiModelMemoryStore(),
        )
    )

    assert captured["rag_call"] == {
        "query": "每天久坐办公，办公椅最应该看哪些地方？",
        "collection": "shopping_guides",
        "top_k": 5,
        "no_rerank": False,
        "include_image_base64": False,
    }
    assert captured["tool_results_before_agent"][0]["data"]["trace_id"] == "query-forced"
    assert any("conversation_id" in event for event in events)


def test_system_prompt_separates_product_api_facts_from_rag_knowledge() -> None:
    assert "商品事实" in SYSTEM_PROMPT
    assert "价格" in SYSTEM_PROMPT
    assert "库存" in SYSTEM_PROMPT
    assert "商品链接" in SYSTEM_PROMPT
    assert "商品搜索工具" in SYSTEM_PROMPT
    assert "商品详情工具" in SYSTEM_PROMPT
    assert "RAG" in SYSTEM_PROMPT
    assert "选购指南" in SYSTEM_PROMPT
    assert "政策 FAQ" in SYSTEM_PROMPT
    assert "不能使用 RAG" in SYSTEM_PROMPT
    assert "不能编造引用" in SYSTEM_PROMPT


def test_system_prompt_covers_recommendation_comparison_guide_and_policy_faq_scenarios() -> None:
    assert "推荐场景" in SYSTEM_PROMPT
    assert "商品搜索工具" in SYSTEM_PROMPT
    assert "商品链接对比场景" in SYSTEM_PROMPT
    assert "商品详情工具" in SYSTEM_PROMPT
    assert "选购指南场景" in SYSTEM_PROMPT
    assert "必须使用 RAG 工具" in SYSTEM_PROMPT
    assert "政策 FAQ 场景" in SYSTEM_PROMPT


def test_stream_chat_hides_rag_tool_payload_and_internal_ids_from_frontend(
    monkeypatch,
) -> None:
    """Protect the frontend SSE contract from raw RAG tool and trace internals."""

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    leaked_rag_json = (
        '{"tool": "search_shopping_guides", "ok": true, "input": "无线耳机怎么选", '
        '"data": {"trace_id": "query-secret", "content": "[1] 关注佩戴舒适度", '
        '"citations": [{"chunk_id": "chunk-secret", "title": "无线耳机选购指南"}]}, '
        '"error": null}'
    )

    def fake_streaming_agent_runner(
        request: AiModelChatRequest,
        tool_results: list[AiModelToolResult],
    ) -> list[str]:
        tool_results.append(
            AiModelToolResult(
                tool="search_shopping_guides",
                ok=True,
                input=request.message,
                data={"trace_id": "query-secret"},
            )
        )
        return [
            "我先检索选购指南。\n",
            leaked_rag_json[:80],
            leaked_rag_json[80:],
            "可以参考选购指南的佩戴舒适度和续航建议。\n",
            "内部记录 chunk_id: chunk-secret, trace_id: query-secret。",
        ]

    events = list(
        stream_chat_events(
            AiModelChatRequest(user_id=1, message="无线耳机怎么选", links=[]),
            mock_api_url="http://mock-api",
            streaming_agent_runner=fake_streaming_agent_runner,
            memory_store=NoopAiModelMemoryStore(),
        )
    )
    response_text = "".join(events)

    assert "可以参考选购指南" in response_text
    assert "search_shopping_guides" not in response_text
    assert "chunk_id" not in response_text
    assert "chunk-secret" not in response_text
    assert "trace_id" not in response_text
    assert "query-secret" not in response_text
    assert "tool_results" not in response_text


def test_stream_chat_hides_internal_ids_split_across_stream_chunks(monkeypatch) -> None:
    """Ensure token-level streaming cannot split internal field names past filtering."""

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    def fake_streaming_agent_runner(
        request: AiModelChatRequest,
        tool_results: list[AiModelToolResult],
    ) -> list[str]:
        return [
            "可以参考选购指南。\n",
            "内部记录 chunk_",
            "id: chunk-secret, trace_",
            "id: query-secret。",
        ]

    events = list(
        stream_chat_events(
            AiModelChatRequest(user_id=1, message="无线耳机怎么选", links=[]),
            mock_api_url="http://mock-api",
            streaming_agent_runner=fake_streaming_agent_runner,
            memory_store=NoopAiModelMemoryStore(),
        )
    )
    response_text = "".join(events)

    assert "可以参考选购指南" in response_text
    assert "chunk_id" not in response_text
    assert "chunk-secret" not in response_text
    assert "trace_id" not in response_text
    assert "query-secret" not in response_text
