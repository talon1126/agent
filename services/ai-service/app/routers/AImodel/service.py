import json
import os
import re
from collections.abc import Callable
from collections.abc import Iterable, Iterator
from typing import Any

import httpx
from fastapi import HTTPException

try:
    from langchain_core.messages import AIMessage, HumanMessage
except ModuleNotFoundError:
    # 中文注释：本地未安装 LangChain 时仍允许 FastAPI 和单元测试导入；生产镜像会安装真实 HumanMessage。
    class HumanMessage:  # type: ignore[no-redef]
        type = "human"

        def __init__(self, content: str) -> None:
            self.content = content

    class AIMessage:  # type: ignore[no-redef]
        type = "ai"

        def __init__(self, content: str) -> None:
            self.content = content


from app.routers.AImodel.intent_router import (
    AImodelIntentRoute,
    load_default_aimodel_intent_router,
)
from app.routers.AImodel.agent_trace import (
    AgentTraceContext,
    LangChainAgentTraceMiddleware,
    record_allowed_tools,
    record_intent_route,
)
from app.routers.AImodel.memory import (
    AiModelMemoryMessage,
    AiModelMemoryStore,
    AiModelUserMemory,
    extract_user_memories_from_text,
    get_aimodel_memory_store,
)
from app.routers.AImodel.schemas import (
    AiModelChatRequest,
    AiModelChatResponse,
    AiModelRecommendedLink,
    AiModelToolResult,
)
from app.routers.AImodel.tools import (
    RagKnowledgeClient,
    fetch_product_detail_from_link,
    recommended_links_from_tool_results,
    rag_tool as run_rag_tool,
    search_web_with_tavily as run_search_web_with_tavily,
    search_products,
)

AgentRunner = Callable[[AiModelChatRequest, list[AiModelToolResult]], str]
StreamingAgentRunner = Callable[
    [AiModelChatRequest, list[AiModelToolResult]], Iterable[str]
]

SYSTEM_PROMPT = """
你是 TalonMart 的 AImodel 购物助手。
你需要根据用户意图决定是否调用工具，并严格区分信息来源。
商品推荐场景：当用户想要商品推荐、购买建议、可购买商品或“有什么值得买”时，必须先使用商品搜索工具获取真实商品后再推荐。
商品链接对比场景：当用户提供一个或多个商品链接并要求对比、总结或判断时，必须使用商品详情工具获取真实商品信息后再回答。
内部知识场景：当用户询问怎么选、判断标准、避坑点、参数含义、品类知识、平台政策、FAQ、客服话术、售后、退换货、保修、配送、履约或账号安全时，必须使用 RAG 工具检索内部知识库。
公开信息场景：当用户询问外部市场趋势、品牌新品背景、公开排行榜或商品库与内部知识库之外的信息时，才使用联网搜索工具。
商品事实必须来自商品搜索工具或商品详情工具，包括价格、库存、优惠、规格、可购买商品和商品链接。
RAG 工具返回的是可直接用于回答的内部知识上下文，不是最终答案；你需要基于这些上下文组织自然回答。
不能使用 RAG 内容生成实时商品事实，不能用 RAG 编造价格、库存、优惠、可购买商品或商品链接。
联网搜索工具不能替代商品搜索工具、商品详情工具或 RAG 工具，不能覆盖平台内部政策、售后规则或客服口径。
当 RAG 与联网搜索冲突时，平台内部规则、政策、FAQ 和客服口径以 RAG 为准；外部市场信息以联网搜索为准。
如果 RAG 返回空结果或证据不足，应明确说明当前内部知识库没有足够依据，不要用常识补写平台规则。
RAG 返回引用时可以在回答中展示引用标题或章节，但不能编造引用，也不能展示内部 chunk id、trace id、query_trace_id 或原始工具 JSON。
不要在最终回答中声明“根据内部知识库”“我查了内部知识库”“来自 RAG”“我调用了工具”等来源过程；直接给出答案。
如果工具没有找到合适商品，请明确说明未找到。
回答使用中文，简洁、实用，并优先给出可执行建议。
回答必须使用清晰 Markdown 格式：短段落说明结论，多个要点使用无序列表，每个列表项只表达一个建议。
不要把工具调用过程、工具名称、工具参数、工具返回 JSON、原始字段名或 Python/JSON 对象展示给用户。
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
        raise HTTPException(
            status_code=502, detail=f"AImodel generation failed: {error}"
        ) from error

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
    memory_store: AiModelMemoryStore | None = None,
) -> Iterator[str]:
    ensure_aimodel_configured()
    memory_store = memory_store or get_aimodel_memory_store()
    tool_results: list[AiModelToolResult] = []
    answer_parts: list[str] = []

    yield _format_sse("status", {"content": "正在理解问题"})

    try:
        # 中文注释：会话 ID 由 ai-service 的 conversation.id 管理，缺失时创建新会话并在 done 中返回给前端。
        conversation_id = memory_store.ensure_conversation(
            request.conversation_id,
            user_id=request.user_id,
            first_message=request.message,
        )
        history = memory_store.load_recent_messages(conversation_id, limit=5)
        user_memories = memory_store.load_user_memories(request.user_id, limit=10)
        memory_store.append_user_message(
            conversation_id,
            user_id=request.user_id,
            content=request.message,
            links=request.links,
        )
        agent_trace_context = AgentTraceContext.start(
            user_query=request.message,
            conversation_id=conversation_id,
        )
    except Exception as error:
        yield _format_sse("error", {"content": f"AImodel memory failed: {error}"})
        return

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
        tool_json_filter = _ToolJsonStreamFilter()
        visible_output_filter = _AgentVisibleStreamFilter()
        chunks = (
            streaming_agent_runner(request, tool_results)
            if streaming_agent_runner
            else _run_langchain_agent_stream(
                request,
                tool_results,
                mock_api_url,
                http_client,
                history=history,
                user_memories=user_memories,
                agent_trace_context=agent_trace_context,
            )
        )
        for chunk in chunks:
            if not chunk:
                continue
            for safe_chunk in tool_json_filter.feed(chunk):
                for visible_chunk in visible_output_filter.feed(safe_chunk):
                    answer_parts.append(visible_chunk)
                    yield _format_sse("delta", {"content": visible_chunk})
        for safe_chunk in tool_json_filter.flush():
            for visible_chunk in visible_output_filter.feed(safe_chunk):
                answer_parts.append(visible_chunk)
                yield _format_sse("delta", {"content": visible_chunk})
        for visible_chunk in visible_output_filter.flush():
            answer_parts.append(visible_chunk)
            yield _format_sse("delta", {"content": visible_chunk})
    except HTTPException:
        raise
    except Exception as error:
        agent_trace_context.fail(error)
        _persist_agent_trace_safely(memory_store, agent_trace_context)
        yield _format_sse("error", {"content": f"AImodel generation failed: {error}"})
        return

    answer = "".join(answer_parts).strip()
    recommended_links = recommended_links_from_tool_results(tool_results)
    query_trace_ids = _query_trace_ids_from_tool_results(tool_results)
    try:
        message_id = memory_store.append_assistant_message(
            conversation_id,
            user_id=request.user_id,
            content=answer,
            recommended_links=recommended_links,
            query_trace_ids=query_trace_ids,
        )
        agent_trace_context.complete(
            message_id=message_id,
            query_trace_ids=query_trace_ids,
        )
        memory_store.persist_agent_trace(agent_trace_context.to_record())
        for memory in extract_user_memories_from_text(
            request.message, user_id=request.user_id
        ):
            memory_store.upsert_user_memory(
                request.user_id,
                memory_type=memory.memory_type,
                memory_value=memory.memory_value,
                evidence=memory.evidence,
                confidence=memory.confidence,
            )
    except Exception:
        # 中文注释：assistant 记忆写入失败不阻断已经生成给用户的回答，避免前端丢失本轮结果。
        pass

    yield _format_sse(
        "done",
        {
            "conversation_id": conversation_id,
            "answer": answer,
            "recommended_links": recommended_links,
        },
    )


def _run_langchain_agent(
    request: AiModelChatRequest,
    tool_results: list[AiModelToolResult],
    mock_api_url: str,
    http_client: httpx.Client | None,
    *,
    agent_trace_context: AgentTraceContext | None = None,
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

    intent_route, intent_candidates = route_aimodel_intent_with_candidates(request.message)
    rag_tool = build_rag_tool(
        tool_results,
        original_query=request.message,
        collection=intent_route.collection if intent_route.action == "rag" else None,
        collections=list(intent_route.collections) if intent_route.action == "rag" else None,
    )
    web_search_tool = build_web_search_tool(tool_results)
    agent_tools = _agent_tools_for_intent_route(
        intent_route,
        product_detail_tool=get_product_detail_from_link,
        product_search_tool=search_product_catalog,
        rag_tool=rag_tool,
        web_search_tool=web_search_tool,
    )
    _record_agent_trace_routing(
        agent_trace_context,
        intent_route,
        agent_tools,
        candidates=intent_candidates,
    )
    model = ChatOpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        model=os.getenv("AIMODEL_MODEL", "deepseek-v4-flash"),
        temperature=0,
        max_retries=2,
    )
    agent = create_agent(
        model=model,
        tools=agent_tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=_agent_trace_middleware(agent_trace_context),
    )
    result = agent.invoke({"messages": _build_langchain_messages(request)})
    return _extract_answer(result) or str(result)


def _run_langchain_agent_stream(
    request: AiModelChatRequest,
    tool_results: list[AiModelToolResult],
    mock_api_url: str,
    http_client: httpx.Client | None,
    *,
    history: list[AiModelMemoryMessage] | None = None,
    user_memories: list[AiModelUserMemory] | None = None,
    agent_trace_context: AgentTraceContext | None = None,
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

    intent_route, intent_candidates = route_aimodel_intent_with_candidates(request.message)
    rag_tool = build_rag_tool(
        tool_results,
        original_query=request.message,
        collection=intent_route.collection if intent_route.action == "rag" else None,
        collections=list(intent_route.collections) if intent_route.action == "rag" else None,
    )
    web_search_tool = build_web_search_tool(tool_results)
    agent_tools = _agent_tools_for_intent_route(
        intent_route,
        product_detail_tool=get_product_detail_from_link,
        product_search_tool=search_product_catalog,
        rag_tool=rag_tool,
        web_search_tool=web_search_tool,
    )
    _record_agent_trace_routing(
        agent_trace_context,
        intent_route,
        agent_tools,
        candidates=intent_candidates,
    )
    model = ChatOpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        model=os.getenv("AIMODEL_MODEL", "deepseek-v4-flash"),
        temperature=0,
        max_retries=2,
        streaming=True,
    )
    agent = create_agent(
        model=model,
        tools=agent_tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=_agent_trace_middleware(agent_trace_context),
    )
    # 中文注释：这里只向前端流式输出可见回答文本，不暴露模型内部隐藏推理链路。
    for update in agent.stream(
        {
            "messages": _build_langchain_messages(
                request, history=history, user_memories=user_memories
            )
        },
        stream_mode="messages",
    ):
        chunk = _extract_stream_token(update)
        if chunk:
            yield chunk


def _record_agent_trace_routing(
    agent_trace_context: AgentTraceContext | None,
    intent_route: AImodelIntentRoute,
    agent_tools: list[Any],
    *,
    candidates: list[dict[str, Any]],
) -> None:
    """Attach caller-side routing diagnostics to the current Agent Trace."""

    if agent_trace_context is None:
        return
    record_intent_route(agent_trace_context, intent_route, candidates=candidates)
    record_allowed_tools(agent_trace_context, agent_tools)


def _agent_trace_middleware(
    agent_trace_context: AgentTraceContext | None,
) -> list[Any]:
    """Return LangChain middleware only when an Agent Trace exists."""

    if agent_trace_context is None:
        return []
    return [LangChainAgentTraceMiddleware(agent_trace_context).as_middleware()]


def _persist_agent_trace_safely(
    memory_store: AiModelMemoryStore,
    agent_trace_context: AgentTraceContext,
) -> None:
    """Persist trace diagnostics without changing the user-facing answer path."""

    try:
        memory_store.persist_agent_trace(agent_trace_context.to_record())
    except Exception:
        return None


def _agent_tools_for_intent_route(
    intent_route: AImodelIntentRoute,
    *,
    product_detail_tool: Any,
    product_search_tool: Any,
    rag_tool: Any,
    web_search_tool: Any,
) -> list[Any]:
    """Return only the tools allowed by the caller-side intent route.

    Args:
        intent_route: Routing decision produced before Agent execution.
        product_detail_tool: Tool for resolving product detail links.
        product_search_tool: Tool for searching the internal product catalog.
        rag_tool: Tool for querying the internal RAG MCP knowledge service.
        web_search_tool: Tool for public web search.

    Returns:
        A LangChain tool list scoped to the selected action. Direct answers and
        refusal paths deliberately receive no tools so they cannot create RAG
        traces or leak into product/web side effects.
    """

    if intent_route.action == "rag":
        return [rag_tool]
    if intent_route.action == "product_api":
        return [product_detail_tool, product_search_tool]
    if intent_route.action == "web":
        return [web_search_tool]
    if intent_route.action in {"direct", "refuse"}:
        return []
    return []


def route_aimodel_intent(message: str) -> AImodelIntentRoute:
    """Route one AImodel user turn before building Agent tools."""

    route, _candidates = route_aimodel_intent_with_candidates(message)
    return route


def route_aimodel_intent_with_candidates(
    message: str,
) -> tuple[AImodelIntentRoute, list[dict[str, Any]]]:
    """Return the selected route and top candidate scores for tracing."""

    return load_default_aimodel_intent_router().route_with_candidates(message)


def build_web_search_tool(
    tool_results: list[AiModelToolResult],
    *,
    http_client: httpx.Client | None = None,
) -> Any:
    """Build the LangChain tool that exposes controlled Tavily web search.

    Args:
        tool_results: Mutable per-request tool result buffer. The returned tool
            appends its result here so downstream filters and tests can inspect
            the public tool contract.
        http_client: Optional injectable HTTP client for unit tests. Production
            leaves this unset so the Tavily adapter opens its own external
            client and never reuses the mock-api product client.

    Returns:
        A LangChain-compatible tool named ``search_web_with_tavily``.
    """

    try:
        from langchain.tools import tool
    except ModuleNotFoundError:
        return _SimpleAImodelTool(
            name="search_web_with_tavily",
            handler=lambda query: _run_web_search_tool(
                query,
                tool_results=tool_results,
                http_client=http_client,
            ),
        )

    @tool("search_web_with_tavily")
    def search_web_with_tavily_tool(query: str) -> dict[str, Any]:
        """Search public web information through Tavily."""

        return _run_web_search_tool(
            query,
            tool_results=tool_results,
            http_client=http_client,
        )

    return search_web_with_tavily_tool


def build_rag_tool(
    tool_results: list[AiModelToolResult],
    *,
    original_query: str,
    collection: str | None = None,
    collections: list[str] | tuple[str, ...] | None = None,
    rag_client: RagKnowledgeClient | None = None,
) -> Any:
    """Build the argument-free RAG tool exposed to the shopping Agent.

    The Agent is allowed to decide whether internal knowledge is needed, but it
    must not rewrite the user's question into ad-hoc keyword queries. This tool
    therefore captures the current turn's original question in a closure and
    exposes no ``query`` argument to LangChain. Repeated calls in one turn return
    the first payload so the final assistant message links to one stable RAG
    query trace by default.

    Args:
        tool_results: Mutable per-request tool result buffer. The first real RAG
            call appends one ``AiModelToolResult`` here so the final assistant
            message can be associated with its consumed query trace.
        original_query: User's current-turn question from ``AiModelChatRequest``.
            This exact text is sent to RAG MCP regardless of the Agent's hidden
            planning text.
        collection: Optional collection selected by AImodel Intent Router. RAG
            still receives the original query, but collection filtering is decided
            by the caller-side orchestration layer when available.
        collections: Optional ordered collection list selected by AImodel from
            scored intent candidates. When present, RAG executes multi-collection
            retrieval behind its MCP boundary.
        rag_client: Optional injectable client used by tests. Production leaves
            this as ``None`` so the H2 MCP stdio client is used.

    Returns:
        A LangChain tool named ``rag_tool``. The public payload shape matches
        other AImodel tools, while the schema intentionally exposes no query
        parameter.
    """

    cached_payload: dict[str, Any] | None = None

    def invoke_rag_for_current_turn() -> dict[str, Any]:
        """Run RAG once for this turn and reuse the payload on repeated calls."""

        nonlocal cached_payload
        if cached_payload is None:
            cached_payload = _run_rag_tool(
                original_query,
                tool_results=tool_results,
                rag_client=rag_client,
                collection=collection,
                collections=collections,
            )
        return cached_payload

    try:
        from langchain.tools import tool
    except ModuleNotFoundError:
        return _SimpleAImodelTool(
            name="rag_tool",
            handler=invoke_rag_for_current_turn,
            argumentless=True,
        )

    @tool("rag_tool")
    def rag_tool_langchain() -> dict[str, Any]:
        """Search TalonMart internal knowledge for the current user question."""

        return invoke_rag_for_current_turn()

    return rag_tool_langchain


class _SimpleAImodelTool:
    """Small test fallback that mimics the LangChain tool surface we use.

    The RAG auto-coder tests execute through the RAG uv environment, which does
    not install the full ai-service LangChain dependency set. This fallback
    keeps ``build_rag_tool`` unit-testable without changing production behavior:
    when LangChain is installed, ``build_rag_tool`` returns a real LangChain
    tool object.
    """

    def __init__(
        self,
        *,
        name: str,
        handler: Callable[..., dict[str, Any]],
        argumentless: bool = False,
    ) -> None:
        """Store the tool name and synchronous handler.

        Args:
            name: Public tool name exposed to the Agent.
            handler: Function invoked by tests when LangChain is unavailable.
                Argument-free tools receive no payload; query tools receive a
                single string query.
            argumentless: Whether this fallback tool should ignore invoke
                payloads and call ``handler`` with no arguments.
        """

        self.name = name
        self._handler = handler
        self._argumentless = argumentless

    def invoke(self, payload: dict[str, Any] | str) -> dict[str, Any]:
        """Invoke the fallback tool using LangChain-like arguments.

        Args:
            payload: Either a raw query string or a dictionary containing the
                ``query`` key, matching the call shape used by tests.

        Returns:
            Public AImodel tool-result dictionary.
        """

        if self._argumentless:
            return self._handler()
        query = payload.get("query", "") if isinstance(payload, dict) else payload
        return self._handler(str(query))


def _run_web_search_tool(
    query: str,
    *,
    tool_results: list[AiModelToolResult],
    http_client: httpx.Client | None,
) -> dict[str, Any]:
    """Execute ``search_web_with_tavily`` and record its public result."""

    result = run_search_web_with_tavily(
        query,
        http_client=http_client,
    )
    tool_results.append(result)
    return result.model_dump()


def _run_rag_tool(
    query: str,
    *,
    tool_results: list[AiModelToolResult],
    rag_client: RagKnowledgeClient | None,
    collection: str | None = None,
    collections: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Execute ``rag_tool`` and record its result for memory.

    Args:
        query: User query passed by the Agent.
        tool_results: Per-request mutable tool result buffer.
        rag_client: Optional injectable RAG client.
        collection: Optional AImodel-selected RAG collection.
        collections: Optional AImodel-selected RAG collection list.

    Returns:
        Serializable AImodel tool result dictionary returned to the Agent.
    """

    result = run_rag_tool(
        query,
        rag_client=rag_client,
        collection=collection,
        collections=collections,
    )
    tool_results.append(result)
    return result.model_dump()


def _build_user_prompt(request: AiModelChatRequest) -> str:
    links = "\n".join(f"- {link}" for link in request.links) if request.links else "无"
    # 中文注释：把用户问题和显式商品链接放进上下文，工具选择仍由 Agent 按系统提示自行决策。
    return f"用户问题：{request.message}\n用户提供的商品链接：\n{links}"


def _query_trace_ids_from_tool_results(
    tool_results: list[AiModelToolResult],
) -> list[str]:
    """Collect stable RAG trace IDs returned by Agent tools."""

    trace_ids: list[str] = []
    for result in tool_results:
        if result.tool not in {"rag_tool", "search_shopping_guides"}:
            continue
        if not isinstance(result.data, dict):
            continue
        query_trace_ids = result.data.get("query_trace_ids")
        if isinstance(query_trace_ids, list) and query_trace_ids:
            for trace_id in query_trace_ids:
                if isinstance(trace_id, str) and trace_id.strip() not in trace_ids:
                    trace_ids.append(trace_id.strip())
            continue
        trace_id = result.data.get("trace_id")
        if (
            isinstance(trace_id, str)
            and trace_id.strip()
            and trace_id.strip() not in trace_ids
        ):
            trace_ids.append(trace_id.strip())
    return trace_ids


def _build_langchain_messages(
    request: AiModelChatRequest,
    *,
    history: list[AiModelMemoryMessage] | None = None,
    user_memories: list[AiModelUserMemory] | None = None,
) -> list[HumanMessage | AIMessage]:
    # 中文注释：LangChain 输入显式使用 HumanMessage，避免手写 role dict 在不同模型适配器中行为不一致。
    messages: list[HumanMessage | AIMessage] = []
    memory_prompt = _build_user_memory_prompt(user_memories or [])
    if memory_prompt:
        messages.append(HumanMessage(content=memory_prompt))

    for message in history or []:
        if message.role == "assistant":
            messages.append(AIMessage(content=message.content))
        else:
            messages.append(HumanMessage(content=message.content))

    messages.append(HumanMessage(content=_build_user_prompt(request)))
    return messages


def _build_user_memory_prompt(user_memories: list[AiModelUserMemory]) -> str:
    if not user_memories:
        return ""

    labels = {
        "brand_preference": "品牌偏好",
        "price_preference": "价格偏好",
        "category_preference": "品类偏好",
    }
    lines = ["用户长期偏好："]
    for memory in user_memories:
        label = labels.get(memory.memory_type, memory.memory_type)
        lines.append(f"- {label}：{memory.memory_value}")
    return "\n".join(lines)


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


class _ToolJsonStreamFilter:
    # 中文注释：模型偶发会把工具返回 JSON 当正文吐出，这里按完整 JSON 对象过滤，避免前端看到内部工具数据。
    def __init__(self) -> None:
        self.pending = ""

    def feed(self, chunk: str) -> list[str]:
        self.pending += chunk
        return self._drain(final=False)

    def flush(self) -> list[str]:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[str]:
        output: list[str] = []
        while self.pending:
            object_start = self.pending.find("{")
            if object_start == -1:
                output.append(self.pending)
                self.pending = ""
                break

            if object_start > 0:
                output.append(self.pending[:object_start])
                self.pending = self.pending[object_start:]

            object_end = _find_json_object_end(self.pending)
            if object_end is None:
                if final:
                    if not _looks_like_tool_json(self.pending):
                        output.append(self.pending)
                    self.pending = ""
                break

            candidate = self.pending[: object_end + 1]
            self.pending = self.pending[object_end + 1 :]
            if not _is_tool_result_json(candidate):
                output.append(candidate)

        return [chunk for chunk in output if chunk]


def _find_json_object_end(text: str) -> int | None:
    depth = 0
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index

    return None


def _looks_like_tool_json(text: str) -> bool:
    return '"tool"' in text or "'tool'" in text


def _is_tool_result_json(text: str) -> bool:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False

    if not isinstance(data, dict):
        return False

    tool_name = data.get("tool")
    return isinstance(tool_name, str) and (
        tool_name
        in {
            "search_products",
            "search_product_catalog",
            "get_product_detail_from_link",
            "rag_tool",
            "search_shopping_guides",
            "search_web_with_tavily",
        }
        or {"ok", "input"}.issubset(data.keys())
    )


_INTERNAL_OUTPUT_MARKER_PATTERN = re.compile(
    r"\b(?:chunk_id|trace_id|query_trace_id|chunk\s+id|trace\s+id)\b",
    flags=re.IGNORECASE,
)
_INTERNAL_OUTPUT_MARKERS = (
    "chunk_id",
    "trace_id",
    "query_trace_id",
    "chunk id",
    "trace id",
)


class _AgentVisibleStreamFilter:
    """Filter internal identifiers from streamed answer text across chunks.

    The system prompt already tells the model not to expose chunk IDs, trace
    IDs, or raw tool fields, but streamed model output can still include those
    internals as ordinary text. Streaming boundaries may also split a field name
    such as ``chunk_id`` into ``chunk_`` and ``id``. This filter therefore keeps
    a small pending buffer for unfinished lines and marker prefixes before text
    is emitted to the frontend.
    """

    def __init__(self) -> None:
        """Initialize an empty pending buffer for the current streamed line."""

        self.pending = ""

    def feed(self, chunk: str) -> list[str]:
        """Accept one post-tool-json text fragment and return safe fragments.

        Args:
            chunk: A text fragment that has already passed raw tool JSON
                filtering.

        Returns:
            User-visible fragments whose complete lines have no internal RAG
            markers. Incomplete suspicious lines are buffered until more text or
            final flush decides whether they should be emitted or dropped.
        """

        self.pending += chunk
        return self._drain(final=False)

    def flush(self) -> list[str]:
        """Return the final safe fragments and drop any remaining internal line."""

        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[str]:
        output: list[str] = []
        while True:
            newline_index = self.pending.find("\n")
            if newline_index == -1:
                break
            line = self.pending[: newline_index + 1]
            self.pending = self.pending[newline_index + 1 :]
            if not _contains_internal_output_marker(line):
                output.append(line)

        if not self.pending:
            return [chunk for chunk in output if chunk]

        if _contains_internal_output_marker(self.pending):
            if final:
                self.pending = ""
            return [chunk for chunk in output if chunk]

        marker_prefix_start = _internal_marker_prefix_start(self.pending)
        if marker_prefix_start is not None:
            safe_prefix = self.pending[:marker_prefix_start]
            self.pending = self.pending[marker_prefix_start:]
            if safe_prefix:
                output.append(safe_prefix)
            return [chunk for chunk in output if chunk]

        if final:
            output.append(self.pending)
            self.pending = ""
        else:
            output.append(self.pending)
            self.pending = ""

        return [chunk for chunk in output if chunk]


def _contains_internal_output_marker(text: str) -> bool:
    """Return whether text contains a user-hidden RAG identifier marker."""

    return bool(_INTERNAL_OUTPUT_MARKER_PATTERN.search(text))


def _internal_marker_prefix_start(text: str) -> int | None:
    """Find a trailing partial internal marker that may complete next chunk.

    Args:
        text: Current incomplete streamed line.

    Returns:
        The start index of the trailing marker prefix that must be buffered, or
        ``None`` when the text can be safely emitted now.
    """

    lowered = text.lower()
    max_marker_length = max(len(marker) for marker in _INTERNAL_OUTPUT_MARKERS)
    for length in range(min(max_marker_length - 1, len(lowered)), 0, -1):
        suffix = lowered[-length:]
        if any(marker.startswith(suffix) for marker in _INTERNAL_OUTPUT_MARKERS):
            return len(text) - length
    return None


def _format_sse(event: str, data: dict[str, Any]) -> str:
    # 中文注释：SSE 用同一个 /chat 接口返回，前端按 event 类型更新状态、增量内容和最终推荐链接。
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
