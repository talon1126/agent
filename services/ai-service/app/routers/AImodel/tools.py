import asyncio
import json
import os
import threading
from collections.abc import Coroutine
from functools import lru_cache
from pathlib import Path
from typing import Any
from typing import Protocol
from urllib.parse import unquote, urlparse

import httpx

from app.routers.AImodel.schemas import AiModelToolResult


DEFAULT_RAG_COLLECTION = "shopping_guides"
DEFAULT_RAG_TOP_K = 5


class RagKnowledgeClient(Protocol):
    """Minimal client interface required by the AImodel RAG tool adapter."""

    def query_knowledge_hub(
        self,
        *,
        query: str,
        collection: str,
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
        collection: str,
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
        collection: str,
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


@lru_cache(maxsize=1)
def get_rag_knowledge_client() -> RagKnowledgeClient:
    """Return the process-wide RAG knowledge client used by AImodel tools."""

    return StdioMcpRagKnowledgeClient()


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


def search_shopping_guides(
    query: str,
    *,
    rag_client: RagKnowledgeClient | None = None,
    collection: str | None = None,
    top_k: int = DEFAULT_RAG_TOP_K,
    no_rerank: bool = False,
    include_image_base64: bool = False,
) -> AiModelToolResult:
    """Search the RAG shopping-guide knowledge base for Agent-ready context.

    Args:
        query: User question or intent that needs durable guide knowledge.
        rag_client: Optional test or production client. ``None`` uses the
            process-wide MCP stdio adapter.
        collection: Optional RAG collection override. The default is
            ``RAG_DEFAULT_COLLECTION`` or ``shopping_guides``.
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
            tool="search_shopping_guides",
            ok=False,
            input=query,
            error="query_required",
        )
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        return AiModelToolResult(
            tool="search_shopping_guides",
            ok=False,
            input=query,
            error="top_k_must_be_positive_integer",
        )

    active_collection = (
        collection.strip()
        if isinstance(collection, str) and collection.strip()
        else os.getenv("RAG_DEFAULT_COLLECTION", DEFAULT_RAG_COLLECTION).strip()
        or DEFAULT_RAG_COLLECTION
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
            tool="search_shopping_guides",
            ok=False,
            input=query,
            error=f"rag_query_failed: {error}",
        )

    if payload.get("ok") is False:
        return AiModelToolResult(
            tool="search_shopping_guides",
            ok=False,
            input=query,
            data={"error": payload.get("error", {})},
            error=_format_rag_error(payload.get("error")),
        )

    return AiModelToolResult(
        tool="search_shopping_guides",
        ok=True,
        input=query,
        data=_public_rag_tool_data(payload),
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


def _run_async_blocking(coroutine: Coroutine[Any, Any, dict[str, Any]]) -> dict[str, Any]:
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
        except BaseException as exc:  # pragma: no cover - preserves thread error boundary.
            error = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    if result is None:
        raise RuntimeError("RAG MCP call finished without a result")
    return result
