"""Unit tests for the RAG Model Context Protocol server boundary.

Phase E exposes the Retrieval pipeline as MCP tools. E1 only creates the
server entry point and registers stable tool names; later tasks replace the
placeholder handlers with real query and repository-backed behavior.
"""

from __future__ import annotations

import os

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from src.core.config import load_settings
from src.core.errors import McpError
from src.mcp_server.server import create_mcp_server, parse_args, run_stdio_server

SETTINGS_PATH = "services/ai-service/rag/config/settings.example.yaml"


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
    """Keep E1 placeholder tools explicit until E2/E3 implement behavior."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    server = create_mcp_server(settings=settings)

    with pytest.raises(ToolError, match="not implemented"):
        await server.call_tool(
            "query_knowledge_hub",
            {"query": "如何挑选高性价比无线耳机？"},
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
