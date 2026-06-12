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
    search_shopping_guides as run_search_shopping_guides,
    search_products,
)

AgentRunner = Callable[[AiModelChatRequest, list[AiModelToolResult]], str]
StreamingAgentRunner = Callable[[AiModelChatRequest, list[AiModelToolResult]], Iterable[str]]

SYSTEM_PROMPT = """
你是 TalonMart 的 AImodel 购物助手。
你需要根据用户意图决定是否调用工具。
推荐场景：当用户想要商品推荐、购买建议或“有什么值得买”时，必须使用商品搜索工具获取真实商品后再推荐。
商品链接对比场景：当用户提供一个或多个商品链接并要求对比、总结或判断时，必须使用商品详情工具获取真实商品信息后再回答。
选购指南场景：当用户询问怎么选、判断标准、避坑点、参数含义或品类知识时，必须使用 RAG 工具检索选购指南。
政策 FAQ 场景：当用户询问售后政策、退换货、保修、配送规则或文档 FAQ 时，必须使用 RAG 工具检索政策 FAQ 或文档知识。
当用户提供商品链接时，必须使用商品详情工具获取真实商品信息后再对比或总结。
当用户询问推荐时，必须使用商品搜索工具获取真实商品后再推荐。
只能推荐工具返回的真实商品和链接，不能编造商品、价格、库存或链接。
商品事实必须来自商品搜索工具或商品详情工具，包括价格、库存、优惠、规格、可购买商品和商品链接。
RAG 工具只用于补充选购指南、品类知识、政策 FAQ、售后规则和文档知识上下文。
不能使用 RAG 内容当作实时商品事实来源，不能用 RAG 生成价格、库存、优惠、可购买商品或商品链接。
RAG 返回引用时可以在回答中展示引用标题或章节，但不能编造引用，也不能展示内部 chunk id、trace id 或原始工具 JSON。
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
        yield _format_sse("error", {"content": f"AImodel generation failed: {error}"})
        return

    answer = "".join(answer_parts).strip()
    recommended_links = recommended_links_from_tool_results(tool_results)
    try:
        memory_store.append_assistant_message(
            conversation_id,
            user_id=request.user_id,
            content=answer,
            recommended_links=recommended_links,
            query_trace_ids=_query_trace_ids_from_tool_results(tool_results),
        )
        for memory in extract_user_memories_from_text(request.message, user_id=request.user_id):
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

    rag_tool = build_rag_tool(tool_results)
    model = ChatOpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        model=os.getenv("AIMODEL_MODEL", "deepseek-v4-flash"),
        temperature=0,
        max_retries=2,
    )
    agent = create_agent(
        model=model,
        tools=[get_product_detail_from_link, search_product_catalog, rag_tool],
        system_prompt=SYSTEM_PROMPT,
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

    rag_tool = build_rag_tool(tool_results)
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
        tools=[get_product_detail_from_link, search_product_catalog, rag_tool],
        system_prompt=SYSTEM_PROMPT,
    )
    # 中文注释：这里只向前端流式输出可见回答文本，不暴露模型内部隐藏推理链路。
    for update in agent.stream(
        {"messages": _build_langchain_messages(request, history=history, user_memories=user_memories)},
        stream_mode="messages",
    ):
        chunk = _extract_stream_token(update)
        if chunk:
            yield chunk


def build_rag_tool(
    tool_results: list[AiModelToolResult],
    *,
    rag_client: RagKnowledgeClient | None = None,
) -> Any:
    """Build the LangChain tool that exposes shopping-guide RAG knowledge.

    Args:
        tool_results: Mutable per-request tool result buffer. The returned tool
            appends its ``AiModelToolResult`` here so the final assistant
            message can be associated with every consumed RAG query trace.
        rag_client: Optional injectable client used by tests. Production leaves
            this as ``None`` so the H2 MCP stdio client is used.

    Returns:
        A LangChain tool named ``search_shopping_guides``. The tool returns the
        same public dictionary shape as other AImodel tools, allowing the
        existing stream filter and trace-id collector to work without special
        cases.
    """

    try:
        from langchain.tools import tool
    except ModuleNotFoundError:
        return _SimpleAImodelTool(
            name="search_shopping_guides",
            handler=lambda query: _run_rag_tool(
                query,
                tool_results=tool_results,
                rag_client=rag_client,
            ),
        )

    @tool("search_shopping_guides")
    def search_shopping_guides_tool(query: str) -> dict[str, Any]:
        """Search shopping guide knowledge and return grounded context."""

        return _run_rag_tool(
            query,
            tool_results=tool_results,
            rag_client=rag_client,
        )

    return search_shopping_guides_tool


class _SimpleAImodelTool:
    """Small test fallback that mimics the LangChain tool surface we use.

    The RAG auto-coder tests execute through the RAG uv environment, which does
    not install the full ai-service LangChain dependency set. This fallback
    keeps ``build_rag_tool`` unit-testable without changing production behavior:
    when LangChain is installed, ``build_rag_tool`` returns a real LangChain
    tool object.
    """

    def __init__(self, *, name: str, handler: Callable[[str], dict[str, Any]]) -> None:
        """Store the tool name and synchronous handler.

        Args:
            name: Public tool name exposed to the Agent.
            handler: Function invoked with the user query and returning the
                public tool payload.
        """

        self.name = name
        self._handler = handler

    def invoke(self, payload: dict[str, Any] | str) -> dict[str, Any]:
        """Invoke the fallback tool using LangChain-like arguments.

        Args:
            payload: Either a raw query string or a dictionary containing the
                ``query`` key, matching the call shape used by tests.

        Returns:
            Public AImodel tool-result dictionary.
        """

        query = payload.get("query", "") if isinstance(payload, dict) else payload
        return self._handler(str(query))


def _run_rag_tool(
    query: str,
    *,
    tool_results: list[AiModelToolResult],
    rag_client: RagKnowledgeClient | None,
) -> dict[str, Any]:
    """Execute ``search_shopping_guides`` and record its result for memory.

    Args:
        query: User query passed by the Agent.
        tool_results: Per-request mutable tool result buffer.
        rag_client: Optional injectable RAG client.

    Returns:
        Serializable AImodel tool result dictionary returned to the Agent.
    """

    result = run_search_shopping_guides(
        query,
        rag_client=rag_client,
    )
    tool_results.append(result)
    return result.model_dump()


def _build_user_prompt(request: AiModelChatRequest) -> str:
    links = "\n".join(f"- {link}" for link in request.links) if request.links else "无"
    # 中文注释：把显式链接放进用户上下文，便于 agent 判断是否必须调用商品详情工具。
    return f"用户问题：{request.message}\n用户提供的商品链接：\n{links}"


def _query_trace_ids_from_tool_results(
    tool_results: list[AiModelToolResult],
) -> list[str]:
    """Collect stable RAG trace IDs returned by Agent tools."""

    trace_ids: list[str] = []
    for result in tool_results:
        if result.tool != "search_shopping_guides":
            continue
        trace_id = result.data.get("trace_id") if isinstance(result.data, dict) else None
        if isinstance(trace_id, str) and trace_id.strip() and trace_id.strip() not in trace_ids:
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
            "search_shopping_guides",
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
