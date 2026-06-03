import os
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import HTTPException

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

SYSTEM_PROMPT = """
你是 TalonMart 的 AImodel 购物助手。
你需要根据用户意图决定是否调用工具。
当用户提供商品链接时，必须使用商品详情工具获取真实商品信息后再对比或总结。
当用户询问推荐时，必须使用商品搜索工具获取真实商品后再推荐。
只能推荐工具返回的真实商品和链接，不能编造商品、价格、库存或链接。
如果工具没有找到合适商品，请明确说明未找到。
回答使用中文，简洁、实用，并优先给出可执行建议。
""".strip()


def handle_chat(
    request: AiModelChatRequest,
    *,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
    agent_runner: AgentRunner | None = None,
) -> AiModelChatResponse:
    if not os.getenv("DASHSCOPE_API_KEY", "").strip():
        raise HTTPException(
            status_code=503,
            detail="AImodel is not configured. Provide DASHSCOPE_API_KEY.",
        )

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
        tool_results=tool_results,
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
    result = agent.invoke({"messages": [{"role": "user", "content": _build_user_prompt(request)}]})
    return _extract_answer(result)


def _build_user_prompt(request: AiModelChatRequest) -> str:
    links = "\n".join(f"- {link}" for link in request.links) if request.links else "无"
    # 中文注释：把显式链接放进用户上下文，便于 agent 判断是否必须调用商品详情工具。
    return f"用户问题：{request.message}\n用户提供的商品链接：\n{links}"


def _extract_answer(result: Any) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    if messages:
        content = getattr(messages[-1], "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return str(result)
