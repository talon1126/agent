from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from app.routers.AImodel.schemas import AiModelToolResult


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
    import os

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
