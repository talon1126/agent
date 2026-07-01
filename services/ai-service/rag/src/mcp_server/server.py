"""Create the official MCP server boundary for the RAG subsystem.

This module owns only the transport-facing server assembly. It uses the Python
MCP SDK's ``FastMCP`` class, validates the configured tool names, and registers
the concrete Phase E tool handlers exposed to AImodel and other MCP clients.

The server factory deliberately does not open PostgreSQL connections, construct
LLM providers, run Retrieval, or import the local query CLI. Those actions are
tool implementation responsibilities and must stay outside the server bootstrap
path so smoke tests and external hosts can inspect tool schemas cheaply.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any, Final

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from src.core.config import RagSettings, load_settings
from src.core.errors import McpError
from src.mcp_server.tools import (
    McpRuntimeHolder,
    MetadataTool,
    QueryKnowledgeHubTool,
)

RAG_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_APP_LOG_PATH: Final[Path] = RAG_ROOT / "src" / "logs" / "app.log"
SERVER_NAME: Final[str] = "aimodel-rag"
SERVER_INSTRUCTIONS: Final[str] = (
    "Expose the AImodel modular RAG knowledge tools. Tool implementations "
    "return grounded content with citations and never expose internal vectors, "
    "provider payloads, prompts, or retrieval metadata."
)
SUPPORTED_TOOLS: Final[set[str]] = {
    "query_knowledge_hub",
    "list_collections",
    "get_document_summary",
}
McpSettingsLoader = Callable[[], RagSettings]
StdioRunner = Callable[[FastMCP], Awaitable[None]]
McpToolHandler = Callable[..., Awaitable[dict[str, Any]]]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options for the standalone MCP server process.

    Args:
        argv: Optional argument list used by tests. ``None`` reads
            ``sys.argv`` through ``argparse``.

    Returns:
        Parsed arguments. The first release intentionally accepts only stdio so
        AImodel integration has one stable transport contract.

    Raises:
        SystemExit: Raised by ``argparse`` when an unsupported transport is
            requested.
    """

    parser = argparse.ArgumentParser(
        description="Run the AImodel RAG MCP server.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio",),
        default="stdio",
        help="MCP transport. The first release supports stdio only.",
    )
    return parser.parse_args(argv)


async def run_stdio_server(
    *,
    settings_loader: McpSettingsLoader = load_settings,
    env_paths: Iterable[str | Path] | None = None,
    log_path: str | Path = DEFAULT_APP_LOG_PATH,
    stdio_runner: StdioRunner | None = None,
) -> None:
    """Load runtime context and start the MCP server over stdio.

    Args:
        settings_loader: Callable that returns validated RAG settings after
            environment loading. Tests inject this to avoid requiring secrets.
        env_paths: Optional explicit ``.env`` candidates. ``None`` loads the
            repository and RAG-local candidates used by normal execution.
        log_path: Application log file path. stdout is never used for logs
            because stdio belongs to the MCP protocol.
        stdio_runner: Optional transport runner. Tests inject a non-blocking
            runner; production uses ``FastMCP.run_stdio_async``.

    Side Effects:
        Loads values from ``.env`` without overriding existing process
        variables, creates the app log file, and starts the stdio server.
    """

    _load_local_environment(env_paths=env_paths)
    logger = _configure_stdio_logging(log_path)
    settings = settings_loader()
    runtime_holder = McpRuntimeHolder(settings_loader=lambda: settings)
    try:
        try:
            runtime_holder.warmup()
        except Exception:
            logger.exception("RAG MCP Cross-Encoder warmup failed")
        server = create_mcp_server(settings=settings, runtime_holder=runtime_holder)
        logger.info("Starting RAG MCP server", extra={"transport": "stdio"})
        runner = stdio_runner or _run_fastmcp_stdio
        await runner(server)
    finally:
        runtime_holder.close()


def main(argv: list[str] | None = None) -> int:
    """Run the standalone MCP server entry point.

    Args:
        argv: Optional argument list used by tests. ``None`` delegates to
            ``argparse`` and reads process arguments.

    Returns:
        Process-style exit code. ``0`` means the stdio server exited normally;
        ``1`` means startup failed after argument parsing.

    Side Effects:
        Runs an asyncio event loop and writes startup errors to stderr plus the
        configured app log. stdout remains reserved for MCP protocol messages.
    """

    parse_args(argv)
    try:
        asyncio.run(run_stdio_server())
    except Exception as error:
        logger = _configure_stdio_logging(DEFAULT_APP_LOG_PATH)
        logger.exception("RAG MCP server failed to start")
        print(f"RAG MCP server failed: {error}", file=sys.stderr)
        return 1
    return 0


def create_mcp_server(
    *,
    settings: RagSettings,
    query_knowledge_hub: McpToolHandler | None = None,
    list_collections: McpToolHandler | None = None,
    get_document_summary: McpToolHandler | None = None,
    runtime_holder: McpRuntimeHolder | None = None,
) -> FastMCP:
    """Create a configured MCP server and register enabled RAG tool names.

    Args:
        settings: Validated RAG settings. The ``mcp.tools`` list is treated as
            the single source of truth for which tool names should be exposed.
        query_knowledge_hub: Optional E2 query tool handler. Tests and AImodel
            integration can inject a preconfigured handler; ``None`` creates
            the default ``QueryKnowledgeHubTool`` without opening resources.
        list_collections: Optional E3 collection catalog handler. ``None``
            creates the default ``MetadataTool`` handler lazily.
        get_document_summary: Optional E3 document summary handler. ``None``
            creates the default ``MetadataTool`` handler lazily.
        runtime_holder: Optional process-level runtime owner used by the default
            query tool to reuse DB pools, query runtimes, and rerank providers.

    Returns:
        A ``FastMCP`` server with E1 placeholder handlers registered for every
        configured tool when MCP is enabled. If ``settings.mcp.enabled`` is
        false, the server is still created but exposes no tools.

    Raises:
        McpError: If settings request an unknown tool name. Failing during
            server creation keeps typoed tool names from reaching AImodel or
            other MCP clients as silently missing capabilities.
    """

    tool_names = tuple(settings.mcp.tools if settings.mcp.enabled else ())
    _validate_tool_names(tool_names)
    server = FastMCP(name=SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    query_tool = query_knowledge_hub or QueryKnowledgeHubTool(
        runtime_holder=runtime_holder,
    ).query_knowledge_hub
    metadata_tool = MetadataTool()
    list_tool = list_collections or metadata_tool.list_collections
    summary_tool = get_document_summary or metadata_tool.get_document_summary
    for tool_name in tool_names:
        _register_tool(
            server,
            tool_name,
            query_knowledge_hub=query_tool,
            list_collections=list_tool,
            get_document_summary=summary_tool,
        )
    return server


def _validate_tool_names(tool_names: Iterable[str]) -> None:
    """Ensure the configured MCP tools are known to the server boundary.

    Args:
        tool_names: Tool names read from settings.

    Raises:
        McpError: If any configured name is not implemented by the current
            Phase E server boundary.
    """

    unknown_tools = sorted(set(tool_names) - SUPPORTED_TOOLS)
    if unknown_tools:
        raise McpError(
            "Unsupported MCP tool configured",
            context={"tools": unknown_tools},
        )


def _register_tool(
    server: FastMCP,
    tool_name: str,
    *,
    query_knowledge_hub: McpToolHandler,
    list_collections: McpToolHandler,
    get_document_summary: McpToolHandler,
) -> None:
    """Attach the current Phase E handler for one configured tool.

    Args:
        server: ``FastMCP`` instance receiving the tool registration.
        tool_name: Stable external tool identifier.
        query_knowledge_hub: E2 knowledge query handler.
        list_collections: E3 collection catalog handler.
        get_document_summary: E3 document summary handler.

    Notes:
        Phase E registers concrete tool handlers while keeping server assembly
        free of database, Retrieval, and provider side effects.
    """

    if tool_name == "query_knowledge_hub":
        server.add_tool(
            query_knowledge_hub,
            name=tool_name,
            description="Query the configured RAG knowledge hub.",
        )
        return
    if tool_name == "list_collections":
        server.add_tool(
            list_collections,
            name=tool_name,
            description="List searchable RAG collections.",
        )
        return
    if tool_name == "get_document_summary":
        server.add_tool(
            get_document_summary,
            name=tool_name,
            description="Return a document summary and structural outline.",
        )
        return
    raise McpError("Unsupported MCP tool configured", context={"tool": tool_name})


async def _run_fastmcp_stdio(server: FastMCP) -> None:
    """Start the official FastMCP stdio transport.

    Args:
        server: Fully configured MCP server.
    """

    await server.run_stdio_async()


def _load_local_environment(
    *,
    env_paths: Iterable[str | Path] | None = None,
) -> None:
    """Load local ``.env`` files without overriding process variables.

    Args:
        env_paths: Explicit candidate files. When omitted, the search covers
            the current working directory, RAG module root, and repository root
            so both direct RAG execution and AImodel-launched child processes
            can share local environment configuration.

    Side Effects:
        Adds variables from existing ``.env`` files to ``os.environ`` only when
        they are not already set.
    """

    candidates = tuple(env_paths) if env_paths is not None else _default_env_paths()
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            load_dotenv(path, override=False)


def _default_env_paths() -> tuple[Path, ...]:
    """Return ordered local ``.env`` candidates for stdio child processes."""

    repository_root = RAG_ROOT.parents[2] if len(RAG_ROOT.parents) > 2 else RAG_ROOT.parent
    return (
        Path.cwd() / ".env",
        RAG_ROOT / ".env",
        repository_root / ".env",
    )


def _configure_stdio_logging(log_path: str | Path) -> logging.Logger:
    """Configure MCP application logs without touching stdout.

    Args:
        log_path: Destination file for ordinary MCP server logs.

    Returns:
        Dedicated logger used by this module.

    Side Effects:
        Creates the log directory and replaces previous handlers on the MCP
        logger to avoid duplicate records in repeated tests or restarts.
    """

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("aimodel_rag.mcp_server")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    )
    logger.addHandler(file_handler)
    return logger


if __name__ == "__main__":
    raise SystemExit(main())
