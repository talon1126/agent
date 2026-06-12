"""Unit tests for the RAG Model Context Protocol server boundary.

Phase E exposes the Retrieval pipeline as MCP tools. E1 only creates the
server entry point and registers stable tool names; later tasks replace the
placeholder handlers with real query and repository-backed behavior.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

import src.mcp_server.server as mcp_server_module
from src.core.config import load_settings
from src.core.errors import McpError
from src.core.response import KnowledgeHubResponse, ResponseImage
from src.core.types import Citation
from src.mcp_server.server import create_mcp_server, parse_args, run_stdio_server
from src.mcp_server.tools import MetadataTool, QueryKnowledgeHubTool

SETTINGS_PATH = "services/ai-service/rag/config/settings.example.yaml"
FORBIDDEN_MCP_OUTPUT_KEYS = {
    "debug",
    "metadata",
    "embedding",
    "vector",
    "dense",
    "sparse",
    "bm25",
    "provider",
    "prompt",
    "tool_result",
    "raw",
}


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
        request_source: str | None = None,
    ) -> FakeQueryExecution:
        """Capture normalized tool arguments and return the fixture response."""

        self.calls.append(
            {
                "query": query,
                "collection": collection,
                "top_k": top_k,
                "no_rerank": no_rerank,
                "trace_id": trace_id,
                "request_source": request_source,
            }
        )
        return FakeQueryExecution(response=self.response)


class FakeMetadataReader:
    """Serve deterministic collection and document metadata to MCP tool tests."""

    def __init__(
        self,
        *,
        collections: list[dict[str, Any]] | None = None,
        document_summary: dict[str, Any] | None = None,
    ) -> None:
        """Store fake metadata responses and record lookup arguments.

        Args:
            collections: Public collection overview rows returned by
                ``list_collections``.
            document_summary: Public document summary returned by
                ``get_document_summary``. ``None`` simulates a missing document.
        """

        self.collections = collections or []
        self.document_summary = document_summary
        self.summary_calls: list[dict[str, str | None]] = []

    def list_collections(self) -> list[dict[str, Any]]:
        """Return the configured collection overview rows."""

        return self.collections

    def get_document_summary(
        self,
        *,
        document_id: str | None = None,
        source_uri: str | None = None,
        collection: str | None = None,
    ) -> dict[str, Any] | None:
        """Record lookup arguments and return the configured summary."""

        self.summary_calls.append(
            {
                "document_id": document_id,
                "source_uri": source_uri,
                "collection": collection,
            }
        )
        return self.document_summary


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


def _metadata_tool(
    *,
    reader: FakeMetadataReader | None = None,
    pool: FakePool | None = None,
) -> tuple[MetadataTool, FakePool, FakeMetadataReader]:
    """Create metadata MCP tools with fake resources for unit tests."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    active_pool = pool or FakePool()
    active_reader = reader or FakeMetadataReader()
    tool = MetadataTool(
        settings_loader=lambda: settings,
        pool_factory=lambda _database_settings: active_pool,
        schema_initializer=lambda _pool: None,
        reader_factory=lambda _pool: active_reader,
    )
    return tool, active_pool, active_reader


def _assert_no_forbidden_output_keys(payload: Any) -> None:
    """Recursively assert that MCP tool output stays on the public contract.

    Args:
        payload: JSON-compatible value returned by a FastMCP tool call or by a
            direct tool adapter invocation.

    Raises:
        AssertionError: If a dictionary key exposes internal retrieval,
            provider, prompt, debug, vector, or raw storage data. Values are not
            scanned because public text can legitimately mention words such as
            "metadata" or "vector" when a source document contains them.
    """

    if isinstance(payload, dict):
        forbidden = FORBIDDEN_MCP_OUTPUT_KEYS.intersection(payload)
        assert not forbidden, f"Forbidden MCP output keys leaked: {sorted(forbidden)}"
        for value in payload.values():
            _assert_no_forbidden_output_keys(value)
    elif isinstance(payload, list | tuple):
        for item in payload:
            _assert_no_forbidden_output_keys(item)


def _assert_business_error(
    payload: dict[str, Any],
    *,
    code: str,
    message_contains: str,
) -> None:
    """Assert the stable ``ok=false`` MCP business-error envelope.

    Args:
        payload: Tool response dictionary.
        code: Expected machine-readable business error code.
        message_contains: Required readable message fragment.
    """

    assert payload["ok"] is False
    assert payload["error"]["code"] == code
    assert message_contains in payload["error"]["message"]


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
async def test_mcp_tool_schemas_match_documented_contract() -> None:
    """Lock the public MCP schema consumed by AImodel and external clients.

    The test verifies the official SDK schema representation rather than local
    function signatures. A failure here means a tool argument was renamed,
    removed, made required, or widened without updating the documented MCP
    contract first.
    """

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    server = create_mcp_server(settings=settings)
    tools = {tool.name: tool.model_dump() for tool in await server.list_tools()}

    assert set(tools) == {
        "query_knowledge_hub",
        "list_collections",
        "get_document_summary",
    }

    query_schema = tools["query_knowledge_hub"]["inputSchema"]
    assert query_schema["required"] == ["query"]
    assert set(query_schema["properties"]) == {
        "query",
        "collection",
        "top_k",
        "no_rerank",
        "include_image_base64",
        "request_source",
    }
    assert query_schema["properties"]["query"]["type"] == "string"
    assert query_schema["properties"]["top_k"]["default"] is None
    assert query_schema["properties"]["no_rerank"]["default"] is False
    assert query_schema["properties"]["include_image_base64"]["default"] is False
    assert query_schema["properties"]["request_source"]["default"] is None

    collection_schema = tools["list_collections"]["inputSchema"]
    assert collection_schema["properties"] == {}
    assert collection_schema["type"] == "object"

    summary_schema = tools["get_document_summary"]["inputSchema"]
    assert set(summary_schema["properties"]) == {
        "document_id",
        "source_uri",
        "collection",
    }
    assert "required" not in summary_schema
    for field_name in ("document_id", "source_uri", "collection"):
        assert summary_schema["properties"][field_name]["default"] is None

    for tool in tools.values():
        assert tool["outputSchema"]["type"] == "object"
        assert tool["outputSchema"]["additionalProperties"] is True
        assert tool["description"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_server_registers_collection_tools_as_real_handlers() -> None:
    """Allow E3 collection tools to replace placeholders through injection."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)

    async def list_collections() -> dict[str, Any]:
        """Return a minimal structured collection response."""

        return {"ok": True, "collections": []}

    async def get_document_summary(document_id: str) -> dict[str, Any]:
        """Return a minimal structured document summary response."""

        return {"ok": True, "document": {"document_id": document_id}}

    server = create_mcp_server(
        settings=settings,
        list_collections=list_collections,
        get_document_summary=get_document_summary,
    )

    list_result = await server.call_tool("list_collections", {})
    summary_result = await server.call_tool(
        "get_document_summary",
        {"document_id": "doc-shopping-guide"},
    )

    assert list_result[1] == {"ok": True, "collections": []}
    assert summary_result[1] == {
        "ok": True,
        "document": {"document_id": "doc-shopping-guide"},
    }


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
def test_default_env_paths_support_container_rag_root(monkeypatch) -> None:
    """Allow the MCP server to start when RAG is copied to /app/rag in Docker."""

    monkeypatch.setattr(mcp_server_module, "RAG_ROOT", Path("/app/rag"))

    paths = mcp_server_module._default_env_paths()

    assert Path("/app/rag/.env") in paths
    assert Path("/app/.env") in paths


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
            "request_source": "mcp",
        }
    ]
    assert pool.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_knowledge_hub_accepts_aimodel_request_source() -> None:
    """Keep AImodel-triggered RAG traces separate from direct MCP or CLI traces."""

    tool, _pool, runtime = _query_tool()

    await tool.query_knowledge_hub(
        "无线耳机怎么选",
        collection="shopping_guides",
        request_source="aimodel",
    )

    assert runtime.calls[0]["request_source"] == "aimodel"


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
    assert runtime.calls[0]["request_source"] == "mcp"


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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_success_outputs_do_not_leak_internal_fields() -> None:
    """Protect Agent-visible tool results from internal retrieval diagnostics."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    query_tool, _query_pool, _runtime = _query_tool()
    metadata_tool, _metadata_pool, _reader = _metadata_tool(
        reader=FakeMetadataReader(
            collections=[
                {
                    "collection": "shopping_guides",
                    "document_count": 1,
                    "chunk_count": 2,
                    "updated_at": "2026-06-08T10:00:00+00:00",
                }
            ],
            document_summary={
                "document_id": "doc-shopping-guide",
                "collection": "shopping_guides",
                "source_uri": "shopping_guides/relax-toys.md",
                "title": "Relax Toy Guide",
                "summary": "A concise buying guide for stress relief toys.",
                "lifecycle_status": "success",
                "chunk_count": 2,
                "sections": [{"path": ["Selection Criteria"], "chunk_count": 2}],
                "updated_at": "2026-06-08T10:00:00+00:00",
            },
        )
    )
    server = create_mcp_server(
        settings=settings,
        query_knowledge_hub=query_tool.query_knowledge_hub,
        list_collections=metadata_tool.list_collections,
        get_document_summary=metadata_tool.get_document_summary,
    )

    query_payload = (
        await server.call_tool("query_knowledge_hub", {"query": "无线耳机怎么选"})
    )[1]
    collections_payload = (await server.call_tool("list_collections", {}))[1]
    summary_payload = (
        await server.call_tool(
            "get_document_summary",
            {"document_id": "doc-shopping-guide"},
        )
    )[1]

    for payload in (query_payload, collections_payload, summary_payload):
        assert payload["ok"] is True
        _assert_no_forbidden_output_keys(payload)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_business_errors_use_stable_public_envelope() -> None:
    """Require recoverable MCP errors to stay JSON-readable for AImodel."""

    query_tool, _query_pool, _runtime = _query_tool()
    metadata_tool, _metadata_pool, _reader = _metadata_tool(
        reader=FakeMetadataReader(collections=[], document_summary=None)
    )

    blank_query = await query_tool.query_knowledge_hub(" ")
    empty_collections = await metadata_tool.list_collections()
    missing_summary_identity = await metadata_tool.get_document_summary()
    missing_document = await metadata_tool.get_document_summary(
        source_uri="shopping_guides/missing.md"
    )

    _assert_business_error(
        blank_query,
        code="invalid_request",
        message_contains="query must not be blank",
    )
    _assert_business_error(
        empty_collections,
        code="no_collections",
        message_contains="no searchable collections",
    )
    _assert_business_error(
        missing_summary_identity,
        code="invalid_request",
        message_contains="document_id or source_uri",
    )
    _assert_business_error(
        missing_document,
        code="document_not_found",
        message_contains="document summary was not found",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_collections_returns_overview_and_closes_pool() -> None:
    """Expose searchable collection counts without leaking database rows."""

    reader = FakeMetadataReader(
        collections=[
            {
                "collection": "shopping_guides",
                "document_count": 2,
                "chunk_count": 8,
                "updated_at": "2026-06-08T10:00:00+00:00",
            }
        ]
    )
    tool, pool, _reader = _metadata_tool(reader=reader)

    payload = await tool.list_collections()

    assert payload == {
        "ok": True,
        "collections": [
            {
                "collection": "shopping_guides",
                "document_count": 2,
                "chunk_count": 8,
                "updated_at": "2026-06-08T10:00:00+00:00",
            }
        ],
    }
    assert pool.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_collections_returns_readable_error_for_empty_catalog() -> None:
    """Return a readable business error when no searchable collection exists."""

    tool, pool, _reader = _metadata_tool(reader=FakeMetadataReader(collections=[]))

    payload = await tool.list_collections()

    assert payload == {
        "ok": False,
        "error": {
            "code": "no_collections",
            "message": "no searchable collections are available",
        },
    }
    assert pool.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_document_summary_returns_public_summary() -> None:
    """Expose document status and section outline by document identity."""

    reader = FakeMetadataReader(
        document_summary={
            "document_id": "doc-shopping-guide",
            "collection": "shopping_guides",
            "source_uri": "shopping_guides/relax-toys.md",
            "title": "Relax Toy Guide",
            "summary": "A concise buying guide for stress relief toys.",
            "lifecycle_status": "success",
            "chunk_count": 3,
            "sections": [
                {"path": ["Selection Criteria"], "chunk_count": 2},
                {"path": ["Safety"], "chunk_count": 1},
            ],
            "updated_at": "2026-06-08T10:00:00+00:00",
        }
    )
    tool, pool, active_reader = _metadata_tool(reader=reader)

    payload = await tool.get_document_summary(document_id="doc-shopping-guide")

    assert payload["ok"] is True
    assert payload["document"]["document_id"] == "doc-shopping-guide"
    assert payload["document"]["sections"] == [
        {"path": ["Selection Criteria"], "chunk_count": 2},
        {"path": ["Safety"], "chunk_count": 1},
    ]
    assert active_reader.summary_calls == [
        {
            "document_id": "doc-shopping-guide",
            "source_uri": None,
            "collection": None,
        }
    ]
    assert pool.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_document_summary_validates_lookup_identity_before_settings() -> None:
    """Reject ambiguous summary lookups before loading environment settings."""

    def failing_settings_loader():
        """Fail if validation incorrectly waits for settings first."""

        raise AssertionError("settings should not be loaded for invalid lookup")

    tool = MetadataTool(settings_loader=failing_settings_loader)

    missing_identity = await tool.get_document_summary()
    ambiguous_identity = await tool.get_document_summary(
        document_id="doc-shopping-guide",
        source_uri="shopping_guides/relax-toys.md",
    )

    assert missing_identity["ok"] is False
    assert missing_identity["error"]["code"] == "invalid_request"
    assert "document_id or source_uri" in missing_identity["error"]["message"]
    assert ambiguous_identity["ok"] is False
    assert ambiguous_identity["error"]["code"] == "invalid_request"
    assert "only one" in ambiguous_identity["error"]["message"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_document_summary_returns_readable_error_when_missing() -> None:
    """Return a stable not-found envelope instead of exposing SQL details."""

    tool, pool, _reader = _metadata_tool(reader=FakeMetadataReader(document_summary=None))

    payload = await tool.get_document_summary(source_uri="shopping_guides/missing.md")

    assert payload == {
        "ok": False,
        "error": {
            "code": "document_not_found",
            "message": "document summary was not found",
        },
    }
    assert pool.closed is True
