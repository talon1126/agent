import os

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.routers.AImodel.schemas import AiModelChatRequest
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
