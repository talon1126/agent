import os

from fastapi import APIRouter

from app.routers.AImodel.schemas import AiModelChatRequest, AiModelChatResponse
from app.routers.AImodel.service import handle_chat

router = APIRouter(prefix="/AImodel", tags=["AImodel"])


@router.post("/chat", response_model=AiModelChatResponse)
def chat_with_aimodel(request: AiModelChatRequest) -> AiModelChatResponse:
    mock_api_url = os.getenv("MOCK_API_URL", "http://mock-api:8000")
    # 中文注释：HTTP 层只读取运行环境并转交 service，AImodel 业务逻辑保持在独立模块。
    return handle_chat(request, mock_api_url=mock_api_url)
