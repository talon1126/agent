import os

from fastapi import FastAPI

from app.after_sales_fast_path import handle_after_sales_fast_path
from app.decision_engine import decide
from app.message_agent import handle_message
from app.message_schemas import (
    AfterSalesFastPathRequest,
    AfterSalesFastPathResponse,
    MessageAgentResponse,
    MessageRequest,
)
from app.schemas import DecisionOutput, EventContext
from app.routers.AImodel import router as aimodel_router
from app.routers.AImodel.memory import get_aimodel_memory_store
from app.session_store import get_session_store

app = FastAPI(title="Ecommerce After-sales AI Service")
app.include_router(aimodel_router)


@app.on_event("startup")
def initialize_session_store() -> None:
    get_session_store().initialize()
    # 中文注释：AImodel 会话记忆表由 ai-service 管理，启动时确保 conversation/message/user_memory 已初始化。
    get_aimodel_memory_store().initialize()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/decide", response_model=DecisionOutput)
def create_decision(event: EventContext) -> DecisionOutput:
    return decide(event)


@app.post("/message/handle", response_model=MessageAgentResponse)
def handle_incoming_message(message: MessageRequest) -> MessageAgentResponse:
    mock_api_url = os.getenv("MOCK_API_URL", "http://mock-api:8000")
    return handle_message(message, mock_api_url=mock_api_url)


@app.post("/after-sales/fast-path", response_model=AfterSalesFastPathResponse)
def handle_after_sales_fast_path_request(
    message: AfterSalesFastPathRequest,
) -> AfterSalesFastPathResponse:
    mock_api_url = os.getenv("MOCK_API_URL", "http://mock-api:8000")
    return handle_after_sales_fast_path(
        message,
        mock_api_url=mock_api_url,
        state_store=get_session_store(),
    )
