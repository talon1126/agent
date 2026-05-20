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

app = FastAPI(title="Ecommerce After-sales AI Service")


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
    return handle_after_sales_fast_path(message, mock_api_url=mock_api_url)
