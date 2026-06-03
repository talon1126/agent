import os

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.routers.AImodel.memory import get_aimodel_memory_store
from app.routers.AImodel.schemas import AiModelChatRequest, AiModelConversationSummary, AiModelStoredMessage
from app.routers.AImodel.service import ensure_aimodel_configured, stream_chat_events

router = APIRouter(prefix="/AImodel", tags=["AImodel"])


@router.post("/chat")
def chat_with_aimodel(request: AiModelChatRequest) -> StreamingResponse:
    mock_api_url = os.getenv("MOCK_API_URL", "http://mock-api:8000")
    ensure_aimodel_configured()
    # 中文注释：保留同一个 /chat 路由，用 SSE 流式返回状态、答案增量和最终推荐链接。
    return StreamingResponse(
        stream_chat_events(request, mock_api_url=mock_api_url),
        media_type="text/event-stream",
    )


@router.get("/conversations", response_model=list[AiModelConversationSummary])
def list_aimodel_conversations(user_id: int) -> list[AiModelConversationSummary]:
    # 中文注释：AI 模式打开时先按当前 users.id 查询历史会话，供用户选择继续或新建。
    return [
        AiModelConversationSummary(
            id=conversation.id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )
        for conversation in get_aimodel_memory_store().list_conversations(user_id=user_id)
    ]


@router.get("/conversations/{conversation_id}/messages", response_model=list[AiModelStoredMessage])
def list_aimodel_conversation_messages(conversation_id: int, user_id: int) -> list[AiModelStoredMessage]:
    # 中文注释：加载旧会话时只返回该用户自己的自然语言消息，不暴露工具调用结果。
    return [
        AiModelStoredMessage(
            id=message.id,
            role=message.role,
            content=message.content,
            links=message.links,
            recommended_links=message.recommended_links,
            created_at=message.created_at,
        )
        for message in get_aimodel_memory_store().list_messages(conversation_id, user_id=user_id)
    ]
