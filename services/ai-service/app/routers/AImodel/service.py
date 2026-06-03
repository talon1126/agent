import json
import os
from collections.abc import Callable
from collections.abc import Iterable, Iterator
from typing import Any

import httpx
from fastapi import HTTPException

try:
    from langchain_core.messages import HumanMessage
except ModuleNotFoundError:
    # 中文注释：本地未安装 LangChain 时仍允许 FastAPI 和单元测试导入；生产镜像会安装真实 HumanMessage。
    class HumanMessage:  # type: ignore[no-redef]
        type = "human"

        def __init__(self, content: str) -> None:
            self.content = content

from app.routers.AImodel.schemas import (
    AiModelChatRequest,
    AiModelChatResponse,
    AiModelRecommendedLink,
    AiModelToolResult,
)
from app.routers.AImodel.tools import (
    fetch_product_detail_from_link,
    recommended_links_from_tool_results,
    search_products,
)

AgentRunner = Callable[[AiModelChatRequest, list[AiModelToolResult]], str]
StreamingAgentRunner = Callable[[AiModelChatRequest, list[AiModelToolResult]], Iterable[str]]

SYSTEM_PROMPT = """
你是 TalonMart 的 AImodel 购物助手。
你需要根据用户意图决定是否调用工具。
当用户提供商品链接时，必须使用商品详情工具获取真实商品信息后再对比或总结。
当用户询问推荐时，必须使用商品搜索工具获取真实商品后再推荐。
只能推荐工具返回的真实商品和链接，不能编造商品、价格、库存或链接。
如果工具没有找到合适商品，请明确说明未找到。
回答使用中文，简洁、实用，并优先给出可执行建议。
回答必须使用清晰 Markdown 格式：短段落说明结论，多个要点使用无序列表，每个列表项只表达一个建议。
""".strip()


def handle_chat(
    request: AiModelChatRequest,
    *,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
    agent_runner: AgentRunner | None = None,
) -> AiModelChatResponse:
    ensure_aimodel_configured()

    tool_results = [
        fetch_product_detail_from_link(
            link,
            mock_api_url=mock_api_url,
            http_client=http_client,
        )
        for link in request.links
    ]

    try:
        # 中文注释：测试可注入 fake agent_runner，生产默认使用 LangChain + 百炼 OpenAI 兼容接口。
        answer = (
            agent_runner(request, tool_results)
            if agent_runner
            else _run_langchain_agent(request, tool_results, mock_api_url, http_client)
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"AImodel generation failed: {error}") from error

    return AiModelChatResponse(
        conversation_id=request.conversation_id,
        answer=answer,
        recommended_links=[
            AiModelRecommendedLink(**link)
            for link in recommended_links_from_tool_results(tool_results)
        ],
    )


def ensure_aimodel_configured() -> None:
    if not os.getenv("DASHSCOPE_API_KEY", "").strip():
        raise HTTPException(
            status_code=503,
            detail="AImodel is not configured. Provide DASHSCOPE_API_KEY.",
        )


def stream_chat_events(
    request: AiModelChatRequest,
    *,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
    streaming_agent_runner: StreamingAgentRunner | None = None,
) -> Iterator[str]:
    ensure_aimodel_configured()
    tool_results: list[AiModelToolResult] = []
    answer_parts: list[str] = []

    yield _format_sse("status", {"content": "正在理解问题"})

    if request.links:
        yield _format_sse("status", {"content": "正在识别商品链接"})
        for link in request.links:
            # 中文注释：商品链接来自用户输入，先通过后端工具查真实商品，再交给 agent 汇总。
            tool_results.append(
                fetch_product_detail_from_link(
                    link,
                    mock_api_url=mock_api_url,
                    http_client=http_client,
                )
            )
        yield _format_sse("status", {"content": "已获取商品信息"})

    yield _format_sse("status", {"content": "正在生成回答"})

    try:
        chunks = (
            streaming_agent_runner(request, tool_results)
            if streaming_agent_runner
            else _run_langchain_agent_stream(request, tool_results, mock_api_url, http_client)
        )
        for chunk in chunks:
            if not chunk:
                continue
            answer_parts.append(chunk)
            yield _format_sse("delta", {"content": chunk})
    except HTTPException:
        raise
    except Exception as error:
        yield _format_sse("error", {"content": f"AImodel generation failed: {error}"})
        return

    answer = "".join(answer_parts).strip()
    yield _format_sse(
        "done",
        {
            "conversation_id": request.conversation_id,
            "answer": answer,
            "recommended_links": recommended_links_from_tool_results(tool_results),
        },
    )


def _run_langchain_agent(
    request: AiModelChatRequest,
    tool_results: list[AiModelToolResult],
    mock_api_url: str,
    http_client: httpx.Client | None,
) -> str:
    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_openai import ChatOpenAI

    @tool
    def get_product_detail_from_link(link: str) -> dict[str, Any]:
        """根据前端商品详情链接查询真实商品详情。"""
        result = fetch_product_detail_from_link(
            link,
            mock_api_url=mock_api_url,
            http_client=http_client,
        )
        tool_results.append(result)
        return result.model_dump()

    @tool
    def search_product_catalog(query: str) -> dict[str, Any]:
        """根据用户搜索词查询真实商品库并返回可推荐商品链接。"""
        result = search_products(
            query,
            mock_api_url=mock_api_url,
            http_client=http_client,
        )
        tool_results.append(result)
        return result.model_dump()

    model = ChatOpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        model=os.getenv("AIMODEL_MODEL", "deepseek-v4-flash"),
        temperature=0,
        max_retries=2,
    )
    agent = create_agent(
        model=model,
        tools=[get_product_detail_from_link, search_product_catalog],
        system_prompt=SYSTEM_PROMPT,
    )
    result = agent.invoke({"messages": _build_langchain_messages(request)})
    return _extract_answer(result) or str(result)


def _run_langchain_agent_stream(
    request: AiModelChatRequest,
    tool_results: list[AiModelToolResult],
    mock_api_url: str,
    http_client: httpx.Client | None,
) -> Iterator[str]:
    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_openai import ChatOpenAI

    @tool
    def get_product_detail_from_link(link: str) -> dict[str, Any]:
        """根据前端商品详情链接查询真实商品详情。"""
        result = fetch_product_detail_from_link(
            link,
            mock_api_url=mock_api_url,
            http_client=http_client,
        )
        tool_results.append(result)
        return result.model_dump()

    @tool
    def search_product_catalog(query: str) -> dict[str, Any]:
        """根据用户搜索词查询真实商品库并返回可推荐商品链接。"""
        result = search_products(
            query,
            mock_api_url=mock_api_url,
            http_client=http_client,
        )
        tool_results.append(result)
        return result.model_dump()

    model = ChatOpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        model=os.getenv("AIMODEL_MODEL", "deepseek-v4-flash"),
        temperature=0,
        max_retries=2,
        streaming=True,
    )
    agent = create_agent(
        model=model,
        tools=[get_product_detail_from_link, search_product_catalog],
        system_prompt=SYSTEM_PROMPT,
    )
    # 中文注释：这里只向前端流式输出可见回答文本，不暴露模型内部隐藏推理链路。
    for update in agent.stream({"messages": _build_langchain_messages(request)}, stream_mode="messages"):
        chunk = _extract_stream_token(update)
        if chunk:
            yield chunk


def _build_user_prompt(request: AiModelChatRequest) -> str:
    links = "\n".join(f"- {link}" for link in request.links) if request.links else "无"
    # 中文注释：把显式链接放进用户上下文，便于 agent 判断是否必须调用商品详情工具。
    return f"用户问题：{request.message}\n用户提供的商品链接：\n{links}"


def _build_langchain_messages(request: AiModelChatRequest) -> list[HumanMessage]:
    # 中文注释：LangChain 输入显式使用 HumanMessage，避免手写 role dict 在不同模型适配器中行为不一致。
    return [HumanMessage(content=_build_user_prompt(request))]


def _extract_answer(result: Any) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        if getattr(message, "type", "") == "human":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _extract_stream_token(update: Any) -> str:
    if not isinstance(update, tuple) or not update:
        return ""

    message = update[0]
    if getattr(message, "tool_call_chunks", None):
        return ""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _format_sse(event: str, data: dict[str, Any]) -> str:
    # 中文注释：SSE 用同一个 /chat 接口返回，前端按 event 类型更新状态、增量内容和最终推荐链接。
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
