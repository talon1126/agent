# 消息 Agent 工具调用实现计划

> **给 agentic workers：** 必须使用子技能：推荐使用 superpowers:subagent-driven-development，或使用 superpowers:executing-plans 按任务执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 增加一个 message-agent 路径，让 n8n 接收文本或音频形态消息，AI service 负责意图识别和工具选择，第一个工具返回订单与物流状态。

**架构：** 保持 n8n 作为编排层，AI service 作为 agent 边界。用 FastAPI 实现确定性的消息处理，暴露与供应商无关的转写 adapter，并把 mock-api 作为企业工具接口。现有售后事件 workflow 不改动。

**技术栈：** Python、FastAPI、Pydantic、httpx、pytest、n8n workflow JSON、Docker Compose、PowerShell demo scripts。

---

## 文件结构

- 新建 `services/ai-service/app/message_schemas.py`：消息处理、转写元数据、工具调用的 Pydantic 请求和响应模型。
- 新建 `services/ai-service/app/transcription.py`：供应商无关的 transcription adapter，包含 `mock` 和 `qwen` 模式。
- 新建 `services/ai-service/app/order_status_tool.py`：从 mock-api 获取订单和物流数据的确定性工具客户端。
- 新建 `services/ai-service/app/message_agent.py`：规则化消息意图识别、订单 ID 提取、转写处理和工具执行。
- 修改 `services/ai-service/app/main.py`：注册 `POST /message/handle`。
- 修改 `services/ai-service/tests/test_api.py`：增加文本和音频路径的接口级测试。
- 新建 `services/ai-service/tests/test_message_agent.py`：为意图、提取、转写、工具输出行为增加单元测试。
- 修改 `services/ai-service/pyproject.toml`：确保 `httpx` 可用于 mock-api 调用。
- 修改 `docker-compose.yml`：加入 mock-api 和 transcription provider 相关环境变量。
- 新建 `fixtures/messages/order_status_text.json`：文本消息 demo payload。
- 新建 `fixtures/messages/order_status_audio_transcript.json`：带 transcript 的音频形态 demo payload。
- 新建 `fixtures/messages/order_status_audio_qwen_missing_config.json`：触发 Qwen 配置错误的音频 demo payload。
- 新建 `scripts/send_message.ps1`：调用 message workflow 或直接调用 AI endpoint 的脚本。
- 新建 `n8n/workflows/message-agent.json`：独立的 `/webhook/message-agent` n8n workflow export。
- 修改 `docs/n8n-workflow-contract.md` 和 `docs/n8n-workflow-contract.zh.md`：记录 message workflow 契约。
- 修改 `docs/local-runbook.md` 和 `docs/local-runbook.zh.md`：增加导入、发布和 demo 命令。

## Task 1：消息 Schema 与 Agent 单元测试

**文件：**
- 新建：`services/ai-service/app/message_schemas.py`
- 新建：`services/ai-service/app/message_agent.py`
- 新建：`services/ai-service/tests/test_message_agent.py`

- [ ] **Step 1：写失败的单元测试**

创建 `services/ai-service/tests/test_message_agent.py`：

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

- [ ] **Step 2：运行测试并确认失败**

运行：

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

预期：失败，因为 `app.message_agent` 和 `app.message_schemas` 尚不存在。

- [ ] **Step 3：实现 schema models**

创建 `services/ai-service/app/message_schemas.py`：

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

- [ ] **Step 4：实现最小消息 helper**

创建 `services/ai-service/app/message_agent.py`：

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

- [ ] **Step 5：运行测试并确认通过**

运行：

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

预期：本文件全部通过。

- [ ] **Step 6：提交**

```powershell
git add services\ai-service\app\message_schemas.py services\ai-service\app\message_agent.py services\ai-service\tests\test_message_agent.py
git commit -m "feat: add message agent schemas"
```

## Task 2：转写 Adapter

**文件：**
- 新建：`services/ai-service/app/transcription.py`
- 修改：`services/ai-service/app/message_agent.py`
- 修改：`services/ai-service/tests/test_message_agent.py`

- [ ] **Step 1：增加失败的转写测试**

追加到 `services/ai-service/tests/test_message_agent.py`：

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

- [ ] **Step 2：运行测试并确认失败**

运行：

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

预期：失败，因为 `app.transcription` 不存在。

- [ ] **Step 3：实现 transcription adapter**

创建 `services/ai-service/app/transcription.py`：

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

- [ ] **Step 4：运行测试并确认通过**

运行：

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

预期：通过。

- [ ] **Step 5：提交**

```powershell
git add services\ai-service\app\transcription.py services\ai-service\tests\test_message_agent.py
git commit -m "feat: add transcription adapter"
```

## Task 3：订单状态工具与消息处理器

**文件：**
- 新建：`services/ai-service/app/order_status_tool.py`
- 修改：`services/ai-service/app/message_agent.py`
- 修改：`services/ai-service/pyproject.toml`
- 修改：`services/ai-service/tests/test_message_agent.py`

- [ ] **Step 1：增加失败的 agent handler 测试**

追加到 `services/ai-service/tests/test_message_agent.py`：

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

- [ ] **Step 2：运行测试并确认失败**

运行：

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

预期：失败，因为 `handle_message` 和订单状态工具尚未实现。

- [ ] **Step 3：增加 httpx 依赖**

修改 `services/ai-service/pyproject.toml` dependencies，加入：

```toml
"httpx>=0.27.0",
```

- [ ] **Step 4：实现订单状态工具**

创建 `services/ai-service/app/order_status_tool.py`：

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

- [ ] **Step 5：实现完整消息处理器**

扩展 `services/ai-service/app/message_agent.py`：

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

- [ ] **Step 6：运行测试并确认通过**

运行：

```powershell
pytest services\ai-service\tests\test_message_agent.py -v
```

预期：通过。

- [ ] **Step 7：提交**

```powershell
git add services\ai-service\app\order_status_tool.py services\ai-service\app\message_agent.py services\ai-service\pyproject.toml services\ai-service\tests\test_message_agent.py
git commit -m "feat: add order status agent tool"
```

## Task 4：API Endpoint 与 Docker 配置

**文件：**
- 修改：`services/ai-service/app/main.py`
- 修改：`services/ai-service/tests/test_api.py`
- 修改：`docker-compose.yml`

- [ ] **Step 1：增加失败的 endpoint 测试**

追加到 `services/ai-service/tests/test_api.py`：

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

- [ ] **Step 2：运行 endpoint 测试并确认失败**

运行：

```powershell
pytest services\ai-service\tests\test_api.py -v
```

预期：失败，因为 `/message/handle` 尚不存在。

- [ ] **Step 3：注册 endpoint**

修改 `services/ai-service/app/main.py`：

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

- [ ] **Step 4：增加 Docker 环境变量**

修改 `docker-compose.yml` 的 `ai-service.environment`：

```yaml
      MOCK_API_URL: http://mock-api:8000
      TRANSCRIPTION_PROVIDER: mock
      TRANSCRIPTION_MODEL: qwen3.6plus
```

- [ ] **Step 5：运行 endpoint 测试并确认通过**

运行：

```powershell
pytest services\ai-service\tests\test_api.py -v
```

预期：通过。

- [ ] **Step 6：提交**

```powershell
git add services\ai-service\app\main.py services\ai-service\tests\test_api.py docker-compose.yml
git commit -m "feat: expose message agent endpoint"
```

## Task 5：Fixtures、脚本和 n8n Workflow

**文件：**
- 新建：`fixtures/messages/order_status_text.json`
- 新建：`fixtures/messages/order_status_audio_transcript.json`
- 新建：`fixtures/messages/order_status_audio_qwen_missing_config.json`
- 新建：`scripts/send_message.ps1`
- 新建：`n8n/workflows/message-agent.json`

- [ ] **Step 1：增加 demo message fixtures**

创建 `fixtures/messages/order_status_text.json`：

```json
{
  "message_id": "msg_order_status_text",
  "source": "internal_test",
  "message_type": "text",
  "text": "Can you check order ord_100 for me?",
  "created_at": "2026-05-19T10:00:00Z"
}
```

创建 `fixtures/messages/order_status_audio_transcript.json`：

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

创建 `fixtures/messages/order_status_audio_qwen_missing_config.json`：

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

- [ ] **Step 2：创建 send_message helper**

创建 `scripts/send_message.ps1`：

```powershell
param(
    [string]$MessageFile = "fixtures\messages\order_status_text.json",
    [string]$Url = "http://localhost:5678/webhook/message-agent"
)

$payload = Get-Content -Raw $MessageFile
Invoke-RestMethod -Method Post -Uri $Url -ContentType "application/json" -Body $payload
```

- [ ] **Step 3：创建 n8n workflow**

创建 `n8n/workflows/message-agent.json`，要求：

- workflow name 为 `Message Agent`。
- webhook path 为 `message-agent`。
- HTTP Request 节点 POST 到 `http://ai-service:8000/message/handle`。
- Code 节点构造 run log，包含 `message_id`、`workflow`、`intent`、`requires_human`、`status`。
- HTTP Request 节点 POST 到 `http://mock-api:8000/run-logs`。
- Respond to Webhook 节点返回 AI service 的结果。

- [ ] **Step 4：验证 JSON**

运行：

```powershell
Get-Content -Raw n8n\workflows\message-agent.json | ConvertFrom-Json | Out-Null
```

预期：命令退出码为 0。

- [ ] **Step 5：提交**

```powershell
git add fixtures\messages scripts\send_message.ps1 n8n\workflows\message-agent.json
git commit -m "feat: add message agent workflow"
```

## Task 6：文档与验证

**文件：**
- 修改：`docs/n8n-workflow-contract.md`
- 修改：`docs/n8n-workflow-contract.zh.md`
- 修改：`docs/local-runbook.md`
- 修改：`docs/local-runbook.zh.md`
- 修改：`README.md`
- 修改：`README.zh.md`

- [ ] **Step 1：更新 workflow contract 文档**

给 `docs/n8n-workflow-contract.md` 增加：

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

给 `docs/n8n-workflow-contract.zh.md` 增加对应中文内容。

- [ ] **Step 2：更新 runbook 文档**

给 `docs/local-runbook.md` 和 `docs/local-runbook.zh.md` 增加：

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/message-agent.json
docker compose exec -T n8n n8n publish:workflow --id=wf_message_agent
docker compose restart n8n
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -MessageFile fixtures\messages\order_status_text.json
```

- [ ] **Step 3：更新 README 文档**

在中英文 README 增加简短说明：

- message-agent workflow 的用途。
- 文本消息 demo。
- 音频契约和接入 Qwen 需要你提供的信息。

- [ ] **Step 4：运行全部 AI service 测试**

运行：

```powershell
pytest services\ai-service\tests -v
```

预期：通过。

- [ ] **Step 5：运行 mock API 测试**

运行：

```powershell
pytest services\mock-api\tests -v
```

预期：通过。

- [ ] **Step 6：验证 workflow JSON 文件**

运行：

```powershell
Get-Content -Raw n8n\workflows\ecommerce-after-sales.json | ConvertFrom-Json | Out-Null
Get-Content -Raw n8n\workflows\message-agent.json | ConvertFrom-Json | Out-Null
```

预期：两个命令都退出码为 0。

- [ ] **Step 7：可选 Docker smoke test**

运行：

```powershell
docker compose up -d --build
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -Url http://localhost:8001/message/handle -MessageFile fixtures\messages\order_status_text.json
```

预期：health checks 返回 `{"status":"ok"}`，直接 message 调用返回包含 `ord_100` 的 answer。

- [ ] **Step 8：提交并推送**

```powershell
git add docs\n8n-workflow-contract.md docs\n8n-workflow-contract.zh.md docs\local-runbook.md docs\local-runbook.zh.md README.md README.zh.md
git commit -m "docs: document message agent workflow"
git push origin after-sales-implementation
```

