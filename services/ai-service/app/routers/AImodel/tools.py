import asyncio
import ipaddress
import json
import os
import threading
from collections.abc import Coroutine
from collections.abc import Callable
from collections.abc import Awaitable
from functools import lru_cache
from pathlib import Path
from typing import Any
from typing import Protocol
from urllib.parse import unquote, urlparse

import httpx

from app.routers.AImodel.schemas import AiModelToolResult


DEFAULT_RAG_COLLECTION = "shopping_guides"
RAG_TOOL_NAME = "rag_tool"
DEFAULT_RAG_TOP_K = 5
DEFAULT_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
DEFAULT_TAVILY_MAX_RESULTS = 5


class TavilySearchClient:
    """Small HTTP client for the controlled Tavily Search API integration.

    The AImodel agent must not browse arbitrary URLs or internal services. This
    client accepts only operator-provided configuration from environment
    variables, posts every query to one configured Tavily endpoint, and returns a
    reduced text-only payload for the agent.
    """

    def __init__(
        self,
        *,
        api_key: str,
        search_url: str = DEFAULT_TAVILY_SEARCH_URL,
        max_results: int = DEFAULT_TAVILY_MAX_RESULTS,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Create a Tavily client from validated configuration.

        Args:
            api_key: Tavily API credential read from ``TAVILY_API_KEY``.
            search_url: Tavily Search API endpoint. Tests may override this via
                configuration; user prompts never control it.
            max_results: Upper bound for returned Tavily result texts.
            http_client: Optional injectable HTTP client used by unit tests.

        Raises:
            ValueError: If required configuration is missing or points to an
                internal/local address.
        """

        normalized_key = api_key.strip()
        if not normalized_key:
            raise ValueError("missing_tavily_api_key")
        normalized_url = search_url.strip() or DEFAULT_TAVILY_SEARCH_URL
        if _is_disallowed_internal_url(normalized_url):
            raise ValueError("invalid_tavily_search_url")
        self.api_key = normalized_key
        self.search_url = normalized_url
        self.max_results = max(1, min(max_results, 10))
        self._http_client = http_client

    @classmethod
    def from_env(
        cls, *, http_client: httpx.Client | None = None
    ) -> "TavilySearchClient":
        """Build a client from AImodel web-search environment variables."""

        return cls(
            api_key=os.getenv("TAVILY_API_KEY", ""),
            search_url=os.getenv("TAVILY_SEARCH_URL", DEFAULT_TAVILY_SEARCH_URL),
            max_results=_int_from_env("TAVILY_MAX_RESULTS", DEFAULT_TAVILY_MAX_RESULTS),
            http_client=http_client,
        )

    def search(self, query: str) -> dict[str, Any]:
        """Search public web information and return text-only agent context.

        Args:
            query: User question or topic passed by the LangChain tool.

        Returns:
            A compact dictionary containing Tavily's generated answer, result
            text snippets, and the number of snippets retained. Page metadata is
            intentionally removed before the result reaches the Agent.
        """

        normalized_query = query.strip() if isinstance(query, str) else ""
        if not normalized_query:
            raise ValueError("query_required")

        payload = {
            "query": normalized_query,
            "search_depth": "basic",
            "include_answer": True,
            "max_results": self.max_results,
        }
        client, should_close = _client_or_default_for_url(
            self.search_url, self._http_client
        )
        try:
            response = client.post(
                self.search_url,
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            data = response.json()
        finally:
            if should_close:
                client.close()
        return _public_tavily_tool_data(data, max_results=self.max_results)


class RagKnowledgeClient(Protocol):
    """Minimal client interface required by the AImodel RAG tool adapter."""

    def query_knowledge_hub(
        self,
        *,
        query: str,
        collection: str | None,
        top_k: int,
        no_rerank: bool,
        include_image_base64: bool,
    ) -> dict[str, Any]:
        """Call the RAG knowledge hub and return its public JSON payload."""


class StdioMcpRagKnowledgeClient:
    """Call the standalone RAG MCP server over stdio.

    The AImodel service depends on this small business-facing adapter instead
    of importing RAG retrieval internals. H3 can reuse this client from a
    LangChain tool wrapper, while unit tests inject a fake client and avoid
    starting a subprocess.
    """

    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Configure the MCP subprocess command without starting it.

        Args:
            command: Executable used to start the MCP server. Defaults to the
                current Python interpreter.
            args: Command arguments. Defaults to the documented RAG MCP module
                entry point.
            cwd: Working directory containing the RAG project. Defaults to the
                sibling ``rag`` directory under ``services/ai-service``.
            env: Optional environment overlay passed to the subprocess.
        """

        rag_root = Path(__file__).resolve().parents[3] / "rag"
        project_dir = Path(cwd or os.getenv("RAG_PROJECT_DIR", str(rag_root))).resolve()
        env_command = os.getenv("RAG_MCP_COMMAND", "").strip()
        if command is not None:
            self._command = command
            self._args = args or _python_mcp_args()
        elif env_command:
            self._command = env_command
            self._args = args or _python_mcp_args()
        else:
            self._command = "uv"
            self._args = args or [
                "run",
                "--project",
                str(project_dir),
                "python",
                *_python_mcp_args(),
            ]
        self._cwd = project_dir
        self._env = env

    def query_knowledge_hub(
        self,
        *,
        query: str,
        collection: str | None,
        top_k: int,
        no_rerank: bool,
        include_image_base64: bool,
    ) -> dict[str, Any]:
        """Synchronously call the async MCP tool for LangChain tool usage."""

        return _run_async_blocking(
            self._query_knowledge_hub_async(
                query=query,
                collection=collection,
                top_k=top_k,
                no_rerank=no_rerank,
                include_image_base64=include_image_base64,
            )
        )

    async def _query_knowledge_hub_async(
        self,
        *,
        query: str,
        collection: str | None,
        top_k: int,
        no_rerank: bool,
        include_image_base64: bool,
    ) -> dict[str, Any]:
        """Open one MCP stdio session and call ``query_knowledge_hub``."""

        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        env = {
            **os.environ,
            "PYTHONPATH": str(self._cwd),
            **(self._env or {}),
        }
        server = StdioServerParameters(
            command=self._command,
            args=self._args,
            cwd=self._cwd,
            env=env,
        )
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "query_knowledge_hub",
                    {
                        "query": query,
                        "collection": collection,
                        "top_k": top_k,
                        "no_rerank": no_rerank,
                        "include_image_base64": include_image_base64,
                    },
                )
        return _mcp_result_to_payload(result)


McpPayloadCaller = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class PersistentMcpRagKnowledgeClient:
    """Reuse one RAG MCP stdio session for process-wide AImodel tool calls.

    LangChain tools call AImodel helpers from synchronous code, while the MCP
    Python SDK is asynchronous. This client owns a background event loop thread
    and lazily creates one MCP stdio session on that loop. Subsequent
    ``query_knowledge_hub`` calls schedule ``session.call_tool`` on the same
    loop, avoiding a fresh RAG subprocess for every user question.

    Tests can inject ``session_factory`` so unit coverage verifies lifecycle
    behavior without starting the real MCP server.
    """

    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        session_factory: Callable[[], McpPayloadCaller] | None = None,
        on_session_start: Callable[[], None] | None = None,
        on_session_close: Callable[[], None] | None = None,
    ) -> None:
        """Configure the persistent MCP client without opening a session.

        Args:
            command: Optional executable used to start the MCP server.
            args: Optional command arguments for the MCP server.
            cwd: RAG project working directory.
            env: Environment overlay passed to the MCP server process.
            session_factory: Optional test hook returning an async payload
                caller. Production leaves this as ``None`` to use stdio MCP.
            on_session_start: Optional lifecycle hook used by tests.
            on_session_close: Optional lifecycle hook used by tests.
        """

        delegate = StdioMcpRagKnowledgeClient(
            command=command,
            args=args,
            cwd=cwd,
            env=env,
        )
        self._command = delegate._command
        self._args = delegate._args
        self._cwd = delegate._cwd
        self._env = delegate._env
        self._session_factory = session_factory
        self._on_session_start = on_session_start
        self._on_session_close = on_session_close
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._caller: McpPayloadCaller | None = None
        self._cleanup: Callable[[], Awaitable[None]] | None = None
        self._lock = threading.RLock()

    def query_knowledge_hub(
        self,
        *,
        query: str,
        collection: str | None,
        top_k: int,
        no_rerank: bool,
        include_image_base64: bool,
    ) -> dict[str, Any]:
        """Synchronously call RAG through the persistent MCP session.

        Args:
            query: User question sent to the RAG knowledge hub.
            collection: Optional target collection. ``None`` lets RAG routing choose.
            top_k: Number of final contexts requested.
            no_rerank: Whether RAG should skip reranking.
            include_image_base64: Whether image bytes should be returned.

        Returns:
            Public RAG MCP payload returned by ``query_knowledge_hub``.
        """

        payload = {
            "query": query,
            "collection": collection,
            "top_k": top_k,
            "no_rerank": no_rerank,
            "include_image_base64": include_image_base64,
            "request_source": "aimodel",
        }
        loop, caller = self._ensure_session()
        future = asyncio.run_coroutine_threadsafe(caller(payload), loop)
        return future.result()

    def close(self) -> None:
        """Close the persistent MCP session and stop its background loop."""

        with self._lock:
            loop = self._loop
            cleanup = self._cleanup
            thread = self._thread
            self._caller = None
            self._cleanup = None
            self._loop = None
            self._thread = None

        if loop is not None and cleanup is not None:
            asyncio.run_coroutine_threadsafe(cleanup(), loop).result()
        if self._on_session_close is not None and loop is not None:
            self._on_session_close()
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)

    def _ensure_session(self) -> tuple[asyncio.AbstractEventLoop, McpPayloadCaller]:
        """Return the active loop and caller, creating them once per lifecycle."""

        with self._lock:
            if self._loop is not None and self._caller is not None:
                return self._loop, self._caller

            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run_loop() -> None:
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()
                loop.close()

            thread = threading.Thread(
                target=run_loop,
                name="aimodel-rag-mcp-client",
                daemon=True,
            )
            thread.start()
            ready.wait(timeout=5)
            if not ready.is_set():
                raise RuntimeError("RAG MCP event loop did not start")

            try:
                caller, cleanup = asyncio.run_coroutine_threadsafe(
                    self._create_session_caller(),
                    loop,
                ).result()
            except Exception:
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=5)
                raise
            self._loop = loop
            self._thread = thread
            self._caller = caller
            self._cleanup = cleanup
            if self._on_session_start is not None:
                self._on_session_start()
            return loop, caller

    async def _create_session_caller(
        self,
    ) -> tuple[McpPayloadCaller, Callable[[], Awaitable[None]]]:
        """Create either an injected fake caller or a real stdio MCP caller."""

        if self._session_factory is not None:

            async def noop_cleanup() -> None:
                return None

            return self._session_factory(), noop_cleanup

        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        env = {
            **os.environ,
            "PYTHONPATH": str(self._cwd),
            **(self._env or {}),
        }
        stack = AsyncExitStack()
        try:
            server = StdioServerParameters(
                command=self._command,
                args=self._args,
                cwd=self._cwd,
                env=env,
            )
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(server)
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
        except Exception:
            await stack.aclose()
            raise

        async def call_query_knowledge_hub(payload: dict[str, Any]) -> dict[str, Any]:
            result = await session.call_tool("query_knowledge_hub", payload)
            return _mcp_result_to_payload(result)

        return call_query_knowledge_hub, stack.aclose


@lru_cache(maxsize=1)
def get_rag_knowledge_client() -> RagKnowledgeClient:
    """Return the process-wide RAG knowledge client used by AImodel tools."""

    return PersistentMcpRagKnowledgeClient()


def close_rag_knowledge_client() -> None:
    """Close and clear the process-wide persistent RAG MCP client."""

    if get_rag_knowledge_client.cache_info().currsize == 0:
        return
    client = get_rag_knowledge_client()
    if hasattr(client, "close"):
        client.close()
    get_rag_knowledge_client.cache_clear()


def _python_mcp_args() -> list[str]:
    """Return the documented Python module arguments for the RAG MCP server."""

    return ["-m", "src.mcp_server.server", "--transport", "stdio"]


def parse_item_id_from_link(link: str) -> str | None:
    parsed = urlparse(link)
    path_parts = [part for part in parsed.path.split("/") if part]
    if "items" not in path_parts:
        return None
    item_index = path_parts.index("items") + 1
    if item_index >= len(path_parts):
        return None
    # 中文注释：前端商品详情页约定为 /items/{item_id}，工具只信任该路径中的商品 ID。
    return unquote(path_parts[item_index]).strip() or None


def build_product_url(item_id: str) -> str:
    frontend_base_url = os.getenv("FRONTEND_BASE_URL", "").strip().rstrip("/")
    if not frontend_base_url:
        return f"/items/{item_id}"
    # 中文注释：生产环境由 FRONTEND_BASE_URL 控制完整商品链接，本地未配置时返回相对路径。
    return f"{frontend_base_url}/items/{item_id}"


def _client_or_default_for_url(
    base_url: str,
    http_client: httpx.Client | None,
) -> tuple[httpx.Client, bool]:
    """Return an injected client or a short-lived HTTP client for Tavily."""

    if http_client:
        return http_client, False
    return httpx.Client(timeout=8, follow_redirects=False), True


def _client_or_default(
    mock_api_url: str,
    http_client: httpx.Client | None,
) -> tuple[httpx.Client, bool]:
    if http_client:
        return http_client, False
    return httpx.Client(base_url=mock_api_url, timeout=8), True


def fetch_product_detail_from_link(
    link: str,
    *,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
) -> AiModelToolResult:
    item_id = parse_item_id_from_link(link)
    if not item_id:
        return AiModelToolResult(
            tool="get_product_detail_from_link",
            ok=False,
            input=link,
            error="product_link_item_id_not_found",
        )

    client, should_close = _client_or_default(mock_api_url, http_client)
    try:
        # 中文注释：商品详情只通过 mock-api 的可信接口读取，避免模型直接编造商品属性。
        response = client.get(f"/ip/{item_id}")
        if response.status_code != 200:
            return AiModelToolResult(
                tool="get_product_detail_from_link",
                ok=False,
                input=link,
                item_id=item_id,
                data=_safe_response_json(response),
                error=f"mock_api_status_{response.status_code}",
            )
        return AiModelToolResult(
            tool="get_product_detail_from_link",
            ok=True,
            input=link,
            item_id=item_id,
            data=response.json(),
        )
    except httpx.HTTPError as error:
        return AiModelToolResult(
            tool="get_product_detail_from_link",
            ok=False,
            input=link,
            item_id=item_id,
            error=str(error),
        )
    finally:
        if should_close:
            client.close()


def search_products(
    query: str,
    *,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
) -> AiModelToolResult:
    client, should_close = _client_or_default(mock_api_url, http_client)
    try:
        # 中文注释：推荐场景必须先搜索后端真实商品库，推荐链接从真实 item_id 生成。
        response = client.get("/search", params={"q": query})
        if response.status_code != 200:
            return AiModelToolResult(
                tool="search_products",
                ok=False,
                input=query,
                data=_safe_response_json(response),
                error=f"mock_api_status_{response.status_code}",
            )
        data = response.json()
        items = [_item_with_url(item) for item in data.get("items", [])]
        return AiModelToolResult(
            tool="search_products",
            ok=True,
            input=query,
            data={**data, "items": items},
        )
    except httpx.HTTPError as error:
        return AiModelToolResult(
            tool="search_products",
            ok=False,
            input=query,
            error=str(error),
        )
    finally:
        if should_close:
            client.close()


def rag_tool(
    query: str,
    *,
    rag_client: RagKnowledgeClient | None = None,
    collection: str | None = None,
    top_k: int = DEFAULT_RAG_TOP_K,
    no_rerank: bool = False,
    include_image_base64: bool = False,
) -> AiModelToolResult:
    """Search TalonMart internal knowledge base for Agent-ready context.

    Args:
        query: User question or intent that needs durable internal knowledge.
        rag_client: Optional test or production client. ``None`` uses the
            process-wide MCP stdio adapter.
        collection: Optional RAG collection override. ``None`` lets the RAG
            intent router choose the most suitable collection.
        top_k: Final number of knowledge snippets requested from RAG.
        no_rerank: Whether to skip reranker inside the RAG query pipeline.
        include_image_base64: Explicitly request image bytes. AImodel keeps this
            false by default to avoid large tool payloads.

    Returns:
        ``AiModelToolResult`` containing only public RAG fields: formatted
        context, citations, images, ``is_empty``, and ``trace_id``.
    """

    normalized_query = query.strip() if isinstance(query, str) else ""
    if not normalized_query:
        return AiModelToolResult(
            tool=RAG_TOOL_NAME,
            ok=False,
            input=query,
            error="query_required",
        )
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        return AiModelToolResult(
            tool=RAG_TOOL_NAME,
            ok=False,
            input=query,
            error="top_k_must_be_positive_integer",
        )

    active_collection = (
        collection.strip() if isinstance(collection, str) and collection.strip() else None
    )
    client = rag_client or get_rag_knowledge_client()
    try:
        payload = client.query_knowledge_hub(
            query=normalized_query,
            collection=active_collection,
            top_k=top_k,
            no_rerank=no_rerank,
            include_image_base64=include_image_base64,
        )
    except Exception as error:
        return AiModelToolResult(
            tool=RAG_TOOL_NAME,
            ok=False,
            input=query,
            error=f"rag_query_failed: {error}",
        )

    if payload.get("ok") is False:
        return AiModelToolResult(
            tool=RAG_TOOL_NAME,
            ok=False,
            input=query,
            data={"error": payload.get("error", {})},
            error=_format_rag_error(payload.get("error")),
        )

    return AiModelToolResult(
        tool=RAG_TOOL_NAME,
        ok=True,
        input=query,
        data=_public_rag_tool_data(payload),
    )


def search_shopping_guides(
    query: str,
    *,
    rag_client: RagKnowledgeClient | None = None,
    collection: str | None = None,
    top_k: int = DEFAULT_RAG_TOP_K,
    no_rerank: bool = False,
    include_image_base64: bool = False,
) -> AiModelToolResult:
    """Backward-compatible wrapper for callers not yet renamed to rag_tool."""

    return rag_tool(
        query,
        rag_client=rag_client,
        collection=collection,
        top_k=top_k,
        no_rerank=no_rerank,
        include_image_base64=include_image_base64,
    )


def search_web_with_tavily(
    query: str,
    *,
    http_client: httpx.Client | None = None,
) -> AiModelToolResult:
    """Search public web information through the controlled Tavily API.

    Args:
        query: User question or topic that requires public web information.
        http_client: Optional injectable client for unit tests.

    Returns:
        ``AiModelToolResult`` with a text-only Tavily payload. When credentials
        or the feature flag are unavailable, the function returns a business
        result instead of raising so product and RAG tools continue to work.
    """

    normalized_query = query.strip() if isinstance(query, str) else ""
    if not normalized_query:
        return AiModelToolResult(
            tool="search_web_with_tavily",
            ok=False,
            input=query,
            error="query_required",
        )
    if not _web_search_enabled():
        return AiModelToolResult(
            tool="search_web_with_tavily",
            ok=False,
            input=query,
            data={"reason": "web_search_disabled"},
            error="web_search_unavailable",
        )
    if not os.getenv("TAVILY_API_KEY", "").strip():
        return AiModelToolResult(
            tool="search_web_with_tavily",
            ok=False,
            input=query,
            data={"reason": "missing_tavily_api_key"},
            error="web_search_unavailable",
        )

    try:
        data = TavilySearchClient.from_env(http_client=http_client).search(
            normalized_query
        )
    except (ValueError, httpx.HTTPError) as error:
        return AiModelToolResult(
            tool="search_web_with_tavily",
            ok=False,
            input=query,
            error=f"tavily_query_failed: {error}",
        )

    return AiModelToolResult(
        tool="search_web_with_tavily",
        ok=True,
        input=query,
        data=data,
    )


def recommended_links_from_tool_results(
    tool_results: list[AiModelToolResult],
) -> list[dict[str, str]]:
    links: dict[str, dict[str, str]] = {}
    for result in tool_results:
        if not result.ok:
            continue
        for item in _items_from_tool_result(result):
            item_id = str(item.get("item_id") or "").strip()
            item_name = str(item.get("item_name") or item_id).strip()
            if item_id:
                links[item_id] = {
                    "item_id": item_id,
                    "item_name": item_name,
                    "url": str(item.get("url") or build_product_url(item_id)),
                }
    return list(links.values())


def _items_from_tool_result(result: AiModelToolResult) -> list[dict[str, Any]]:
    if result.tool == "get_product_detail_from_link":
        item = result.data.get("item")
        return [item] if isinstance(item, dict) else []
    if result.tool == "search_products":
        items = result.data.get("items", [])
        return [item for item in items if isinstance(item, dict)]
    return []


def _item_with_url(item: dict[str, Any]) -> dict[str, Any]:
    item_id = str(item.get("item_id") or "")
    return {**item, "url": build_product_url(item_id)} if item_id else item


def _safe_response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"text": response.text}
    return payload if isinstance(payload, dict) else {"payload": payload}


def _public_rag_tool_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only RAG fields that are safe and useful for the AImodel Agent."""

    content = payload.get("content")
    return {
        "trace_id": payload.get("trace_id"),
        "content": content if isinstance(content, str) else "",
        "citations": _list_of_dicts(payload.get("citations")),
        "images": _list_of_dicts(payload.get("images")),
        "is_empty": bool(payload.get("is_empty", not bool(content))),
    }


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    """Return only dictionary items from a possibly mixed JSON list."""

    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _format_rag_error(error: Any) -> str:
    """Convert the public RAG business error envelope into readable text."""

    if not isinstance(error, dict):
        return "rag_query_failed"
    code = str(error.get("code") or "rag_query_failed")
    message = str(error.get("message") or "").strip()
    return f"{code}: {message}" if message else code


def _mcp_result_to_payload(result: Any) -> dict[str, Any]:
    """Extract structured JSON from different MCP SDK result shapes."""

    structured_content = getattr(result, "structuredContent", None)
    if isinstance(structured_content, dict):
        return structured_content
    if isinstance(result, tuple) and len(result) >= 2 and isinstance(result[1], dict):
        return result[1]

    content = getattr(result, "content", None)
    if isinstance(content, list):
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    return decoded
    raise ValueError("RAG MCP response did not include structured JSON content")


def _run_async_blocking(
    coroutine: Coroutine[Any, Any, dict[str, Any]],
) -> dict[str, Any]:
    """Run one async MCP call from sync FastAPI/LangChain tool code.

    A normal sync route can use ``asyncio.run`` directly. If a caller already
    owns an event loop, the coroutine runs in a short helper thread so this
    synchronous tool API remains usable.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    result: dict[str, Any] | None = None
    error: BaseException | None = None

    def runner() -> None:
        nonlocal error, result
        try:
            result = asyncio.run(coroutine)
        except (
            BaseException
        ) as exc:  # pragma: no cover - preserves thread error boundary.
            error = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    if result is None:
        raise RuntimeError("RAG MCP call finished without a result")
    return result


def _public_tavily_tool_data(payload: Any, *, max_results: int) -> dict[str, Any]:
    """Reduce Tavily's response to text fields that are safe for the Agent."""

    data = payload if isinstance(payload, dict) else {}
    answer = data.get("answer")
    contents: list[str] = []
    raw_results = data.get("results")
    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if content and content not in contents:
                contents.append(content)
            if len(contents) >= max_results:
                break
    return {
        "answer": answer.strip() if isinstance(answer, str) else "",
        "contents": contents,
        "result_count": len(contents),
    }


def _web_search_enabled() -> bool:
    """Return whether the AImodel web search tool should be available."""

    value = os.getenv("AIMODEL_WEB_SEARCH_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _int_from_env(name: str, default: int) -> int:
    """Parse a positive integer environment setting with a safe fallback."""

    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _is_disallowed_internal_url(url: str) -> bool:
    """Reject non-Tavily, local, and internal web-search API endpoints."""

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return True
    if host in {"localhost", "0.0.0.0"}:
        return True
    try:
        ip_address = ipaddress.ip_address(host)
    except ValueError:
        ip_address = None
    if ip_address is not None and (
        ip_address.is_private
        or ip_address.is_loopback
        or ip_address.is_link_local
        or ip_address.is_reserved
        or ip_address.is_multicast
    ):
        return True
    if host.endswith(".local") or host.endswith(".internal"):
        return True
    return not (
        host == "api.tavily.com"
        or host.endswith(".tavily.com")
        or host == "api.tavily.test"
        or host.endswith(".tavily.test")
    )
