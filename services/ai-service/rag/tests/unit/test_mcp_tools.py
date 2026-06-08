"""Unit tests for the RAG Model Context Protocol server boundary.

Phase E exposes the Retrieval pipeline as MCP tools. E1 only creates the
server entry point and registers stable tool names; later tasks replace the
placeholder handlers with real query and repository-backed behavior.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from src.core.config import load_settings
from src.core.errors import McpError
from src.core.response import KnowledgeHubResponse, ResponseImage
from src.core.types import Citation
from src.mcp_server.server import create_mcp_server, parse_args, run_stdio_server
from src.mcp_server.tools import QueryKnowledgeHubTool

SETTINGS_PATH = "services/ai-service/rag/config/settings.example.yaml"


@dataclass
class FakeQueryExecution:
    """Minimal execution result containing only MCP-visible response data."""

    response: KnowledgeHubResponse


class FakePool:
    """Record pool lifecycle calls made by the MCP query tool."""

    def __init__(self) -> None:
        """Initialize unopened fake pool state."""

        self.is_open = False
        self.closed = False

    def open(self) -> None:
        """Record that the tool opened the database boundary."""

        self.is_open = True

    def close(self) -> None:
        """Record that the tool closed the database boundary."""

        self.closed = True


class FailingOpenPool(FakePool):
    """Simulate a pool that allocates state and then fails during ``open``."""

    def open(self) -> None:
        """Fail after marking the pool as opened to test cleanup guarantees."""

        self.is_open = True
        raise RuntimeError("database connection failed")


class FakeRuntime:
    """Return a prebuilt response while recording query execution inputs."""

    def __init__(self, response: KnowledgeHubResponse) -> None:
        """Store the public response returned from ``execute``."""

        self.response = response
        self.calls: list[dict[str, Any]] = []

    def execute(
        self,
        query: str,
        *,
        collection: str,
        top_k: int,
        no_rerank: bool,
        trace_id: str,
    ) -> FakeQueryExecution:
        """Capture normalized tool arguments and return the fixture response."""

        self.calls.append(
            {
                "query": query,
                "collection": collection,
                "top_k": top_k,
                "no_rerank": no_rerank,
                "trace_id": trace_id,
            }
        )
        return FakeQueryExecution(response=self.response)


def _knowledge_response(
    *,
    images: tuple[ResponseImage, ...] = (),
    is_empty: bool = False,
) -> KnowledgeHubResponse:
    """Build one public RAG response fixture for MCP tool tests."""

    citations = ()
    content = ""
    if not is_empty:
        citations = (
            Citation(
                document_id="doc-wireless-earbuds",
                chunk_id="chunk-wireless-earbuds",
                title="Wireless Earbuds Guide",
                section_path=("Core Checks",),
                source_uri="shopping_guides/wireless-earbuds.md",
                score=0.82,
                trace_id="trace-mcp-test",
            ),
        )
        content = "[1] Check connection stability, battery life, and comfort."
    return KnowledgeHubResponse(
        content=content,
        citations=citations,
        images=images,
        trace_id="trace-mcp-test",
        is_empty=is_empty,
    )


def _query_tool(
    *,
    response: KnowledgeHubResponse | None = None,
    pool: FakePool | None = None,
    runtime: FakeRuntime | None = None,
) -> tuple[QueryKnowledgeHubTool, FakePool, FakeRuntime]:
    """Create a query tool with fake resources for unit tests."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    active_pool = pool or FakePool()
    active_runtime = runtime or FakeRuntime(response or _knowledge_response())
    tool = QueryKnowledgeHubTool(
        settings_loader=lambda: settings,
        pool_factory=lambda _database_settings: active_pool,
        schema_initializer=lambda _pool: None,
        runtime_builder=lambda _settings, _pool, _no_rerank: active_runtime,
        trace_id_factory=lambda: "trace-mcp-test",
    )
    return tool, active_pool, active_runtime


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_mcp_server_registers_configured_tools() -> None:
    """Require the server factory to expose all configured MCP tool names.

    The test intentionally uses the official ``FastMCP.list_tools()`` API so a
    future SDK compatibility break fails at the external boundary rather than
    in AImodel integration.
    """

    settings = load_settings(SETTINGS_PATH, validate_environment=False)

    server = create_mcp_server(settings=settings)
    tools = await server.list_tools()

    assert isinstance(server, FastMCP)
    assert server.name == "aimodel-rag"
    assert {
        tool.name for tool in tools
    } == set(settings.mcp.tools)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_server_placeholder_tools_fail_with_stable_error() -> None:
    """Keep E3 placeholder tools explicit until collection tools are implemented."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    server = create_mcp_server(settings=settings)

    with pytest.raises(ToolError, match="not implemented"):
        await server.call_tool(
            "list_collections",
            {},
        )


@pytest.mark.unit
def test_create_mcp_server_rejects_unregistered_tool_names() -> None:
    """Fail fast when settings request a tool that E1 cannot register safely."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    broken_settings = settings.model_copy(
        update={"mcp": settings.mcp.model_copy(update={"tools": ["unknown_tool"]})}
    )

    with pytest.raises(McpError, match="Unsupported MCP tool"):
        create_mcp_server(settings=broken_settings)


@pytest.mark.unit
def test_parse_args_accepts_only_stdio_transport() -> None:
    """Keep the first MCP transport decision explicit and testable."""

    args = parse_args(["--transport", "stdio"])

    assert args.transport == "stdio"

    with pytest.raises(SystemExit):
        parse_args(["--transport", "streamable-http"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_stdio_server_loads_env_configures_file_logging_and_runs(
    tmp_path,
    monkeypatch,
) -> None:
    """Verify stdio startup side effects without blocking on a real transport."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    calls: list[str] = []
    env_path = tmp_path / ".env"
    log_path = tmp_path / "src" / "logs" / "app.log"
    env_path.write_text("RAG_MCP_TEST_VALUE=loaded-from-env\n", encoding="utf-8")
    monkeypatch.delenv("RAG_MCP_TEST_VALUE", raising=False)

    async def fake_runner(server: FastMCP) -> None:
        """Record that the assembled server would be started over stdio."""

        calls.append(server.name)

    await run_stdio_server(
        settings_loader=lambda: settings,
        env_paths=[env_path],
        log_path=log_path,
        stdio_runner=fake_runner,
    )

    assert calls == ["aimodel-rag"]
    assert log_path.exists()
    assert log_path.read_text(encoding="utf-8")
    assert os.environ["RAG_MCP_TEST_VALUE"] == "loaded-from-env"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_knowledge_hub_returns_public_response_and_closes_pool() -> None:
    """Expose Retrieval output as a stable MCP tool result without debug data."""

    tool, pool, runtime = _query_tool()

    payload = await tool.query_knowledge_hub(
        "如何挑选高性价比无线耳机？",
        collection="shopping_guides",
        top_k=3,
        no_rerank=True,
    )

    assert payload["ok"] is True
    assert payload["content"] == "[1] Check connection stability, battery life, and comfort."
    assert payload["trace_id"] == "trace-mcp-test"
    assert payload["is_empty"] is False
    assert payload["citations"][0]["chunk_id"] == "chunk-wireless-earbuds"
    assert "debug" not in payload
    assert "metadata" not in str(payload)
    assert runtime.calls == [
        {
            "query": "如何挑选高性价比无线耳机？",
            "collection": "shopping_guides",
            "top_k": 3,
            "no_rerank": True,
            "trace_id": "trace-mcp-test",
        }
    ]
    assert pool.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_knowledge_hub_uses_defaults_and_preserves_empty_success() -> None:
    """Keep empty retrieval as ok=true instead of a transport failure."""

    tool, _pool, runtime = _query_tool(response=_knowledge_response(is_empty=True))

    payload = await tool.query_knowledge_hub("怎么选无线耳机")

    assert payload == {
        "ok": True,
        "content": "",
        "citations": [],
        "images": [],
        "trace_id": "trace-mcp-test",
        "is_empty": True,
    }
    assert runtime.calls[0]["collection"] == "shopping_guides"
    assert runtime.calls[0]["top_k"] == 5
    assert runtime.calls[0]["no_rerank"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_knowledge_hub_returns_structured_business_error() -> None:
    """Return recoverable validation failures as ok=false JSON content."""

    tool, pool, runtime = _query_tool()

    payload = await tool.query_knowledge_hub("   ")

    assert payload == {
        "ok": False,
        "error": {
            "code": "invalid_request",
            "message": "query must not be blank",
        },
    }
    assert runtime.calls == []
    assert pool.closed is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_knowledge_hub_closes_pool_when_open_fails() -> None:
    """Release the database boundary even if pool opening fails midway."""

    failing_pool = FailingOpenPool()
    tool, pool, _runtime = _query_tool(pool=failing_pool)

    with pytest.raises(RuntimeError, match="database connection failed"):
        await tool.query_knowledge_hub("无线耳机怎么选")

    assert pool.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_knowledge_hub_validates_blank_query_before_loading_settings() -> None:
    """Return request errors without requiring database or provider environment."""

    def failing_settings_loader():
        """Fail if validation incorrectly waits for settings first."""

        raise AssertionError("settings should not be loaded for blank query")

    tool = QueryKnowledgeHubTool(settings_loader=failing_settings_loader)

    payload = await tool.query_knowledge_hub(" ")

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_request"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_knowledge_hub_returns_image_metadata_without_base64_by_default(
    tmp_path,
) -> None:
    """Avoid large stdio payloads unless image base64 is explicitly requested."""

    image_path = tmp_path / "earbuds.png"
    image_path.write_bytes(b"image-bytes")
    response = _knowledge_response(
        images=(
            ResponseImage(
                image_id="image-earbuds",
                file_path=str(image_path),
                mime_type="image/png",
                page=1,
                width=640,
                height=480,
                caption="无线耳机佩戴示意图。",
                quality_status="ok",
                chunk_ids=("chunk-wireless-earbuds",),
            ),
        )
    )
    tool, _pool, _runtime = _query_tool(response=response)

    payload = await tool.query_knowledge_hub("无线耳机怎么选")

    assert payload["images"][0]["file_path"] == str(image_path)
    assert "base64_content" not in payload["images"][0]

    payload_with_base64 = await tool.query_knowledge_hub(
        "无线耳机怎么选",
        include_image_base64=True,
    )

    assert payload_with_base64["images"][0]["base64_content"] == "aW1hZ2UtYnl0ZXM="


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_mcp_server_can_register_real_query_tool() -> None:
    """Let E2 replace only the query placeholder while E3 tools stay reserved."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    tool, _pool, _runtime = _query_tool()
    server = create_mcp_server(
        settings=settings,
        query_knowledge_hub=tool.query_knowledge_hub,
    )

    result = await server.call_tool(
        "query_knowledge_hub",
        {"query": "无线耳机怎么选"},
    )

    payload = result[1]
    assert payload["ok"] is True
    assert payload["trace_id"] == "trace-mcp-test"
