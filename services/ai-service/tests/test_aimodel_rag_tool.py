from typing import Any

from app.routers.AImodel.schemas import AiModelToolResult
from app.routers.AImodel.service import (
    SYSTEM_PROMPT,
    _query_trace_ids_from_tool_results,
    build_rag_tool,
)
from app.routers.AImodel.tools import StdioMcpRagKnowledgeClient, search_shopping_guides


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
