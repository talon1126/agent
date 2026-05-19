# Message Agent Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a message-agent path where n8n accepts text or audio-shaped messages, AI service handles intent and tool selection, and the first tool returns order and shipment status.

**Architecture:** Keep n8n as orchestration and the AI service as the agent boundary. Implement deterministic message handling in FastAPI, expose a provider-neutral transcription adapter, and call mock-api as the enterprise tool surface. Keep the existing after-sales workflow unchanged.

**Tech Stack:** Python, FastAPI, Pydantic, httpx, pytest, n8n workflow JSON, Docker Compose, PowerShell demo scripts.

---

## File Structure

- Create `services/ai-service/app/message_schemas.py`: Pydantic request and response models for message handling, transcription metadata, and tool calls.
- Create `services/ai-service/app/transcription.py`: provider-neutral transcription adapter with `mock` and `qwen` modes.
- Create `services/ai-service/app/order_status_tool.py`: deterministic tool client that fetches order and shipment data from mock-api.
- Create `services/ai-service/app/message_agent.py`: rule-based message intent detection, order ID extraction, transcription handling, and tool execution.
- Modify `services/ai-service/app/main.py`: register `POST /message/handle`.
- Modify `services/ai-service/tests/test_api.py`: add endpoint-level tests for text and audio paths.
- Create `services/ai-service/tests/test_message_agent.py`: unit tests for intent, extraction, transcription, and tool output behavior.
- Modify `services/ai-service/pyproject.toml`: ensure `httpx` is available for mock-api calls.
- Modify `docker-compose.yml`: add message-agent environment variables for mock-api and transcription provider.
- Create `fixtures/messages/order_status_text.json`: demo text payload.
- Create `fixtures/messages/order_status_audio_transcript.json`: demo audio-shaped payload with transcript.
- Create `fixtures/messages/order_status_audio_qwen_missing_config.json`: demo audio payload that triggers the controlled Qwen configuration error.
- Create `scripts/send_message.ps1`: helper script for calling the message workflow or direct AI endpoint.
- Create `n8n/workflows/message-agent.json`: independent n8n workflow export for `/webhook/message-agent`.
- Modify `docs/n8n-workflow-contract.md` and `docs/n8n-workflow-contract.zh.md`: document the message workflow contract.
- Modify `docs/local-runbook.md` and `docs/local-runbook.zh.md`: add import, publish, and demo commands.

## Task 1: Message Schema and Agent Unit Tests

**Files:**
- Create: `services/ai-service/app/message_schemas.py`
- Create: `services/ai-service/app/message_agent.py`
- Create: `services/ai-service/tests/test_message_agent.py`

- [ ] **Step 1: Write failing unit tests**

Create `services/ai-service/tests/test_message_agent.py` with tests that describe the behavior before implementation:

```python
from app.message_agent import extract_order_id, infer_intent, normalize_message_text
from app.message_schemas import MessageRequest


def test_extracts_order_id_from_text():
    assert extract_order_id("Please check order ord_100 for me") == "ord_100"


def test_uses_explicit_order_id_first():
    request = MessageRequest(
        message_id="msg_1",
        source="internal_test",
        message_type="text",
        text="Where is my order?",
        order_id="ord_100",
        created_at="2026-05-19T10:00:00Z",
    )
    assert normalize_message_text(request) == "Where is my order?"
    assert infer_intent(request.text or "") == "order_status"


def test_audio_request_uses_transcript_text():
    request = MessageRequest(
        message_id="msg_audio_1",
        source="internal_test",
        message_type="audio",
        transcript="Please check ord_100",
        mime_type="audio/mpeg",
        created_at="2026-05-19T10:00:00Z",
    )
    assert normalize_message_text(request) == "Please check ord_100"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

Expected result: FAIL because `app.message_agent` and `app.message_schemas` do not exist.

- [ ] **Step 3: Implement schema models**

Create `services/ai-service/app/message_schemas.py`:

```python
from typing import Any, Literal

from pydantic import BaseModel, Field

MessageType = Literal["text", "audio"]
Intent = Literal["order_status", "unknown"]


class MessageRequest(BaseModel):
    message_id: str
    source: str
    message_type: MessageType
    text: str | None = None
    transcript: str | None = None
    audio_url: str | None = None
    audio_base64: str | None = None
    mime_type: str | None = None
    customer_id: str | None = None
    order_id: str | None = None
    created_at: str


class TranscriptionResult(BaseModel):
    provider: str
    model: str
    transcript: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ToolCall(BaseModel):
    tool_name: str
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    status: Literal["succeeded", "failed", "skipped"]
    error: str | None = None


class MessageAgentResponse(BaseModel):
    message_id: str
    normalized_text: str
    intent: Intent
    tool_calls: list[ToolCall] = Field(default_factory=list)
    answer: str
    requires_human: bool
    confidence: float = Field(ge=0, le=1)
    transcription: TranscriptionResult | None = None
    error: str | None = None
```

- [ ] **Step 4: Implement minimal message helpers**

Create `services/ai-service/app/message_agent.py`:

```python
import re

from app.message_schemas import MessageRequest

ORDER_ID_PATTERN = re.compile(r"\bord_[0-9A-Za-z]+\b", re.IGNORECASE)
ORDER_STATUS_KEYWORDS = ("order", "订单", "物流", "delivery", "shipment", "tracking", "where")


def extract_order_id(text: str) -> str | None:
    match = ORDER_ID_PATTERN.search(text)
    return match.group(0).lower() if match else None


def infer_intent(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ORDER_STATUS_KEYWORDS):
        return "order_status"
    return "unknown"


def normalize_message_text(request: MessageRequest) -> str:
    if request.message_type == "text":
        return (request.text or "").strip()
    return (request.transcript or request.text or "").strip()
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

Expected result: PASS for all tests in this file.

- [ ] **Step 6: Commit**

```powershell
git add services\ai-service\app\message_schemas.py services\ai-service\app\message_agent.py services\ai-service\tests\test_message_agent.py
git commit -m "feat: add message agent schemas"
```

## Task 2: Transcription Adapter

**Files:**
- Create: `services/ai-service/app/transcription.py`
- Modify: `services/ai-service/app/message_agent.py`
- Modify: `services/ai-service/tests/test_message_agent.py`

- [ ] **Step 1: Add failing transcription tests**

Append to `services/ai-service/tests/test_message_agent.py`:

```python
from app.transcription import transcribe_audio


def test_mock_transcription_returns_deterministic_text():
    result = transcribe_audio(
        provider="mock",
        model="mock-transcriber",
        audio_url=None,
        audio_base64="bW9jayBhdWRpbw==",
        mime_type="audio/mpeg",
    )
    assert result.transcript == "Please check order ord_100"
    assert result.provider == "mock"
    assert result.error is None


def test_qwen_transcription_without_endpoint_returns_configuration_error():
    result = transcribe_audio(
        provider="qwen",
        model="qwen3.6plus",
        audio_url="https://example.com/audio.mp3",
        audio_base64=None,
        mime_type="audio/mpeg",
    )
    assert result.transcript is None
    assert result.error == "Qwen transcription is not configured. Provide QWEN_API_ENDPOINT and QWEN_API_KEY."
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

Expected result: FAIL because `app.transcription` does not exist.

- [ ] **Step 3: Implement transcription adapter**

Create `services/ai-service/app/transcription.py`:

```python
import os

from app.message_schemas import TranscriptionResult


def transcribe_audio(
    *,
    provider: str,
    model: str,
    audio_url: str | None,
    audio_base64: str | None,
    mime_type: str | None,
) -> TranscriptionResult:
    if provider == "mock":
        return TranscriptionResult(
            provider="mock",
            model=model,
            transcript="Please check order ord_100",
            confidence=0.99,
            metadata={"mime_type": mime_type, "input": "audio_url" if audio_url else "audio_base64"},
        )

    if provider == "qwen":
        endpoint = os.getenv("QWEN_API_ENDPOINT")
        api_key = os.getenv("QWEN_API_KEY")
        if not endpoint or not api_key:
            return TranscriptionResult(
                provider="qwen",
                model=model,
                error="Qwen transcription is not configured. Provide QWEN_API_ENDPOINT and QWEN_API_KEY.",
                metadata={"mime_type": mime_type},
            )
        return TranscriptionResult(
            provider="qwen",
            model=model,
            error="Qwen transcription request shape is pending provider details.",
            metadata={"endpoint": endpoint, "mime_type": mime_type},
        )

    return TranscriptionResult(
        provider=provider,
        model=model,
        error=f"Unsupported transcription provider: {provider}",
    )
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

Expected result: PASS.

- [ ] **Step 5: Commit**

```powershell
git add services\ai-service\app\transcription.py services\ai-service\tests\test_message_agent.py
git commit -m "feat: add transcription adapter"
```

## Task 3: Order Status Tool and Message Handler

**Files:**
- Create: `services/ai-service/app/order_status_tool.py`
- Modify: `services/ai-service/app/message_agent.py`
- Modify: `services/ai-service/pyproject.toml`
- Modify: `services/ai-service/tests/test_message_agent.py`

- [ ] **Step 1: Add failing agent handler tests**

Append to `services/ai-service/tests/test_message_agent.py`:

```python
import httpx

from app.message_agent import handle_message


def test_handle_message_calls_order_status_tool():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orders/ord_100":
            return httpx.Response(
                200,
                json={
                    "order_id": "ord_100",
                    "status": "paid",
                    "shipment_id": "ship_100",
                },
            )
        if request.url.path == "/shipments/ship_100":
            return httpx.Response(
                200,
                json={
                    "shipment_id": "ship_100",
                    "status": "in_transit",
                    "estimated_delivery": "2026-05-22",
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock-api")
    request = MessageRequest(
        message_id="msg_1",
        source="internal_test",
        message_type="text",
        text="Where is order ord_100?",
        created_at="2026-05-19T10:00:00Z",
    )

    response = handle_message(request, mock_api_url="http://mock-api", http_client=client)

    assert response.intent == "order_status"
    assert response.requires_human is False
    assert response.tool_calls[0].tool_name == "get_order_status"
    assert response.tool_calls[0].status == "succeeded"
    assert "ord_100" in response.answer
    assert "in_transit" in response.answer


def test_handle_message_requests_order_id_when_missing():
    request = MessageRequest(
        message_id="msg_missing",
        source="internal_test",
        message_type="text",
        text="Where is my order?",
        created_at="2026-05-19T10:00:00Z",
    )

    response = handle_message(request, mock_api_url="http://mock-api")

    assert response.intent == "order_status"
    assert response.requires_human is False
    assert response.tool_calls == []
    assert response.answer == "Please provide the order ID so I can check the latest status."
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

Expected result: FAIL because `handle_message` and the order status tool are not implemented.

- [ ] **Step 3: Add httpx dependency**

Modify `services/ai-service/pyproject.toml` dependencies to include:

```toml
"httpx>=0.27.0",
```

- [ ] **Step 4: Implement order status tool**

Create `services/ai-service/app/order_status_tool.py`:

```python
import httpx


def get_order_status(
    *,
    order_id: str,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
) -> dict:
    owns_client = http_client is None
    client = http_client or httpx.Client(base_url=mock_api_url, timeout=5)
    try:
        order_response = client.get(f"/orders/{order_id}")
        order_response.raise_for_status()
        order = order_response.json()

        shipment = None
        shipment_id = order.get("shipment_id")
        if shipment_id:
            shipment_response = client.get(f"/shipments/{shipment_id}")
            shipment_response.raise_for_status()
            shipment = shipment_response.json()

        shipment_status = shipment.get("status") if shipment else "unknown"
        estimated_delivery = shipment.get("estimated_delivery") if shipment else None
        summary = (
            f"Order {order_id} is {order.get('status', 'unknown')}. "
            f"Shipment status is {shipment_status}."
        )
        if estimated_delivery:
            summary += f" Estimated delivery is {estimated_delivery}."

        return {
            "order_id": order_id,
            "order_status": order.get("status", "unknown"),
            "shipment_id": shipment_id,
            "shipment_status": shipment_status,
            "estimated_delivery": estimated_delivery,
            "summary": summary,
        }
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 5: Implement full message handler**

Extend `services/ai-service/app/message_agent.py`:

```python
import os
import re

import httpx

from app.message_schemas import MessageAgentResponse, MessageRequest, ToolCall
from app.order_status_tool import get_order_status
from app.transcription import transcribe_audio

ORDER_ID_PATTERN = re.compile(r"\bord_[0-9A-Za-z]+\b", re.IGNORECASE)
ORDER_STATUS_KEYWORDS = ("order", "订单", "物流", "delivery", "shipment", "tracking", "where")


def extract_order_id(text: str) -> str | None:
    match = ORDER_ID_PATTERN.search(text)
    return match.group(0).lower() if match else None


def infer_intent(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ORDER_STATUS_KEYWORDS):
        return "order_status"
    return "unknown"


def normalize_message_text(request: MessageRequest) -> str:
    if request.message_type == "text":
        return (request.text or "").strip()
    return (request.transcript or request.text or "").strip()


def handle_message(
    request: MessageRequest,
    *,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
) -> MessageAgentResponse:
    transcription = None
    normalized_text = normalize_message_text(request)

    if request.message_type == "audio" and not normalized_text:
        provider = os.getenv("TRANSCRIPTION_PROVIDER", "mock")
        model = os.getenv("TRANSCRIPTION_MODEL", "qwen3.6plus")
        transcription = transcribe_audio(
            provider=provider,
            model=model,
            audio_url=request.audio_url,
            audio_base64=request.audio_base64,
            mime_type=request.mime_type,
        )
        if transcription.error:
            return MessageAgentResponse(
                message_id=request.message_id,
                normalized_text="",
                intent="unknown",
                answer=transcription.error,
                requires_human=True,
                confidence=0.0,
                transcription=transcription,
                error=transcription.error,
            )
        normalized_text = transcription.transcript or ""

    intent = infer_intent(normalized_text)
    if intent != "order_status":
        return MessageAgentResponse(
            message_id=request.message_id,
            normalized_text=normalized_text,
            intent="unknown",
            answer="I can currently help check order status. Please provide an order status question with an order ID.",
            requires_human=False,
            confidence=0.5,
            transcription=transcription,
        )

    order_id = request.order_id or extract_order_id(normalized_text)
    if not order_id:
        return MessageAgentResponse(
            message_id=request.message_id,
            normalized_text=normalized_text,
            intent="order_status",
            answer="Please provide the order ID so I can check the latest status.",
            requires_human=False,
            confidence=0.7,
            transcription=transcription,
        )

    try:
        tool_output = get_order_status(
            order_id=order_id,
            mock_api_url=mock_api_url,
            http_client=http_client,
        )
    except Exception as exc:
        return MessageAgentResponse(
            message_id=request.message_id,
            normalized_text=normalized_text,
            intent="order_status",
            tool_calls=[
                ToolCall(
                    tool_name="get_order_status",
                    input={"order_id": order_id},
                    status="failed",
                    error=str(exc),
                )
            ],
            answer="I could not retrieve the order status. A human teammate should review this request.",
            requires_human=True,
            confidence=0.2,
            transcription=transcription,
            error=str(exc),
        )

    return MessageAgentResponse(
        message_id=request.message_id,
        normalized_text=normalized_text,
        intent="order_status",
        tool_calls=[
            ToolCall(
                tool_name="get_order_status",
                input={"order_id": order_id},
                output=tool_output,
                status="succeeded",
            )
        ],
        answer=tool_output["summary"],
        requires_human=False,
        confidence=0.9,
        transcription=transcription,
    )
```

- [ ] **Step 6: Run tests and verify pass**

Run:

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

Expected result: PASS.

- [ ] **Step 7: Commit**

```powershell
git add services\ai-service\app\order_status_tool.py services\ai-service\app\message_agent.py services\ai-service\pyproject.toml services\ai-service\tests\test_message_agent.py
git commit -m "feat: add order status agent tool"
```

## Task 4: API Endpoint and Docker Configuration

**Files:**
- Modify: `services/ai-service/app/main.py`
- Modify: `services/ai-service/tests/test_api.py`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add failing endpoint tests**

Append to `services/ai-service/tests/test_api.py`:

```python
def test_message_handle_missing_order_id():
    response = client.post(
        "/message/handle",
        json={
            "message_id": "msg_missing",
            "source": "internal_test",
            "message_type": "text",
            "text": "Where is my order?",
            "created_at": "2026-05-19T10:00:00Z",
        },
    )
    assert response.status_code == 200
    assert response.json()["intent"] == "order_status"
    assert response.json()["answer"] == "Please provide the order ID so I can check the latest status."


def test_message_handle_audio_without_text_uses_mock_transcription():
    response = client.post(
        "/message/handle",
        json={
            "message_id": "msg_audio",
            "source": "internal_test",
            "message_type": "audio",
            "audio_base64": "bW9jayBhdWRpbw==",
            "mime_type": "audio/mpeg",
            "created_at": "2026-05-19T10:00:00Z",
        },
    )
    assert response.status_code == 200
    assert response.json()["normalized_text"] == "Please check order ord_100"
```

- [ ] **Step 2: Run endpoint tests and verify failure**

Run:

```powershell
pytest services\ai-service\tests\test_api.py -v
```

Expected result: FAIL because `/message/handle` is missing.

- [ ] **Step 3: Register endpoint**

Modify `services/ai-service/app/main.py`:

```python
import os

from fastapi import FastAPI

from app.decision_engine import decide
from app.message_agent import handle_message
from app.message_schemas import MessageAgentResponse, MessageRequest
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
```

- [ ] **Step 4: Add Docker environment variables**

Modify `docker-compose.yml` under `ai-service.environment`:

```yaml
      MOCK_API_URL: http://mock-api:8000
      TRANSCRIPTION_PROVIDER: mock
      TRANSCRIPTION_MODEL: qwen3.6plus
```

- [ ] **Step 5: Run endpoint tests and verify pass**

Run:

```powershell
pytest services\ai-service\tests\test_api.py -v
```

Expected result: PASS.

- [ ] **Step 6: Commit**

```powershell
git add services\ai-service\app\main.py services\ai-service\tests\test_api.py docker-compose.yml
git commit -m "feat: expose message agent endpoint"
```

## Task 5: Fixtures, Script, and n8n Workflow

**Files:**
- Create: `fixtures/messages/order_status_text.json`
- Create: `fixtures/messages/order_status_audio_transcript.json`
- Create: `fixtures/messages/order_status_audio_qwen_missing_config.json`
- Create: `scripts/send_message.ps1`
- Create: `n8n/workflows/message-agent.json`

- [ ] **Step 1: Add demo message fixtures**

Create `fixtures/messages/order_status_text.json`:

```json
{
  "message_id": "msg_order_status_text",
  "source": "internal_test",
  "message_type": "text",
  "text": "Can you check order ord_100 for me?",
  "created_at": "2026-05-19T10:00:00Z"
}
```

Create `fixtures/messages/order_status_audio_transcript.json`:

```json
{
  "message_id": "msg_order_status_audio_transcript",
  "source": "internal_test",
  "message_type": "audio",
  "transcript": "Can you check order ord_100 for me?",
  "mime_type": "audio/mpeg",
  "created_at": "2026-05-19T10:01:00Z"
}
```

Create `fixtures/messages/order_status_audio_qwen_missing_config.json`:

```json
{
  "message_id": "msg_order_status_audio_qwen_missing_config",
  "source": "internal_test",
  "message_type": "audio",
  "audio_url": "https://example.com/customer-message.mp3",
  "mime_type": "audio/mpeg",
  "created_at": "2026-05-19T10:02:00Z"
}
```

- [ ] **Step 2: Create send_message helper**

Create `scripts/send_message.ps1`:

```powershell
param(
    [string]$MessageFile = "fixtures\messages\order_status_text.json",
    [string]$Url = "http://localhost:5678/webhook/message-agent"
)

$payload = Get-Content -Raw $MessageFile
Invoke-RestMethod -Method Post -Uri $Url -ContentType "application/json" -Body $payload
```

- [ ] **Step 3: Create n8n workflow**

Create `n8n/workflows/message-agent.json` as an importable workflow with:

- Manual trigger-safe metadata.
- Webhook node using path `message-agent`.
- HTTP Request node POSTing to `http://ai-service:8000/message/handle`.
- Code node building a run log payload with `message_id`, `workflow`, `intent`, `requires_human`, and `status`.
- HTTP Request node POSTing to `http://mock-api:8000/run-logs`.
- Respond to Webhook node returning the AI service result.

The workflow should use fixed node IDs and name `Message Agent`.

- [ ] **Step 4: Validate JSON**

Run:

```powershell
Get-Content -Raw n8n\workflows\message-agent.json | ConvertFrom-Json | Out-Null
```

Expected result: command exits with code 0.

- [ ] **Step 5: Commit**

```powershell
git add fixtures\messages scripts\send_message.ps1 n8n\workflows\message-agent.json
git commit -m "feat: add message agent workflow"
```

## Task 6: Documentation and Verification

**Files:**
- Modify: `docs/n8n-workflow-contract.md`
- Modify: `docs/n8n-workflow-contract.zh.md`
- Modify: `docs/local-runbook.md`
- Modify: `docs/local-runbook.zh.md`
- Modify: `README.md`
- Modify: `README.zh.md`

- [ ] **Step 1: Update workflow contract docs**

Add a section to `docs/n8n-workflow-contract.md`:

```markdown
## Message Agent Workflow

Webhook path: `/webhook/message-agent`

Required steps:

1. Receive text or audio-shaped message payload.
2. POST the normalized payload to `ai-service /message/handle`.
3. Write result metadata to `mock-api /run-logs`.
4. Return `answer`, `intent`, `tool_calls`, `requires_human`, and optional `transcription`.

Audio support currently uses provider configuration. `TRANSCRIPTION_PROVIDER=mock` is deterministic. `TRANSCRIPTION_PROVIDER=qwen` requires `QWEN_API_ENDPOINT`, `QWEN_API_KEY`, the confirmed model name, and a provider response example before real network calls are enabled.
```

Add the corresponding Chinese section to `docs/n8n-workflow-contract.zh.md`.

- [ ] **Step 2: Update runbook docs**

Add commands to `docs/local-runbook.md` and `docs/local-runbook.zh.md`:

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/message-agent.json
docker compose exec -T n8n n8n publish:workflow --id=wf_message_agent
docker compose restart n8n
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -MessageFile fixtures\messages\order_status_text.json
```

- [ ] **Step 3: Update README docs**

Add a short section to both README files describing:

- Message-agent workflow purpose.
- Text message demo.
- Audio contract and Qwen details needed from the user.

- [ ] **Step 4: Run all AI service tests**

Run:

```powershell
pytest services\ai-service\tests -v
```

Expected result: PASS.

- [ ] **Step 5: Run mock API tests**

Run:

```powershell
pytest services\mock-api\tests -v
```

Expected result: PASS.

- [ ] **Step 6: Validate workflow JSON files**

Run:

```powershell
Get-Content -Raw n8n\workflows\ecommerce-after-sales.json | ConvertFrom-Json | Out-Null
Get-Content -Raw n8n\workflows\message-agent.json | ConvertFrom-Json | Out-Null
```

Expected result: both commands exit with code 0.

- [ ] **Step 7: Optional Docker smoke test**

Run:

```powershell
docker compose up -d --build
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -Url http://localhost:8001/message/handle -MessageFile fixtures\messages\order_status_text.json
```

Expected result: health checks return `{"status":"ok"}` and the direct message call returns an answer containing `ord_100`.

- [ ] **Step 8: Commit and push**

```powershell
git add docs\n8n-workflow-contract.md docs\n8n-workflow-contract.zh.md docs\local-runbook.md docs\local-runbook.zh.md README.md README.zh.md
git commit -m "docs: document message agent workflow"
git push origin after-sales-implementation
```

