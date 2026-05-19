# 电商售后 Agent 实现计划

> **给 agentic workers:** 必须使用的子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法进行跟踪。

**目标:** 构建一个 Docker-first 的电商售后 multi-agent workflow demo，包含 n8n 编排、FastAPI AI service、FastAPI mock 企业 API、Postgres 存储、fixtures、测试和作品集文档。

**架构:** n8n 是 workflow 编排器，通过 Docker 网络调用内部服务。`mock-api` 模拟企业系统和运维记录。`ai-service` 负责面向模型的行为、结构化决策输出和确定性测试行为。

**技术栈:** Docker Compose、n8n、Python 3.12、FastAPI、Pydantic v2、pytest、httpx、Postgres 16、SQLAlchemy 2、Ruff。

---

## 实现决策

- `services/ai-service` 和 `services/mock-api` 都使用 FastAPI。
- Docker Compose 中使用 Postgres，服务代码通过 `DATABASE_URL` 读取连接信息。
- 第一版先使用 deterministic fake AI mode，让测试和 demo 不依赖付费 API key。
- MVP 阶段通过 `mock-api` 模拟审批。
- n8n workflow 的导入/导出文件固定为 `n8n/workflows/ecommerce-after-sales.json`。

## 文件地图

- `docker-compose.yml`: 运行 n8n、Postgres、ai-service 和 mock-api。
- `.env.example`: 记录本地环境变量。
- `README.md`: 面向作品集的安装、架构和 demo 说明。
- `services/ai-service/app/main.py`: FastAPI 的 health 和 `/decide` 路由。
- `services/ai-service/app/schemas.py`: 请求和响应模型。
- `services/ai-service/app/decision_engine.py`: triage、policy、inventory 和 response drafting 逻辑。
- `services/ai-service/tests/`: AI service 测试。
- `services/mock-api/app/main.py`: 企业 mock 系统的 FastAPI 路由。
- `services/mock-api/app/store.py`: fixture 加载和后续持久化辅助逻辑。
- `services/mock-api/tests/`: mock API 测试。
- `fixtures/events/*.json`: 脚本化 demo 事件。
- `fixtures/policies/after_sales_policy.md`: 退款、物流、评论和库存规则。
- `fixtures/data/*.json`: 确定性的订单、客户、物流和库存数据。
- `scripts/send_event.ps1`: 向 n8n 发送 demo 事件。
- `scripts/replay_failed_event.ps1`: 通过 mock-api replay 失败事件。
- `n8n/workflows/ecommerce-after-sales.json`: 可导入的 n8n workflow export。
- `docs/architecture.md`: 架构说明和可替换 SaaS 点。
- `docs/demo-script.md`: 3-5 分钟作品集 demo 脚本。

---

### Task 1: 搭建 Docker-first 项目结构

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `services/ai-service/pyproject.toml`
- Create: `services/ai-service/Dockerfile`
- Create: `services/ai-service/app/__init__.py`
- Create: `services/mock-api/pyproject.toml`
- Create: `services/mock-api/Dockerfile`
- Create: `services/mock-api/app/__init__.py`

- [ ] **Step 1: 创建目录**

运行：

```powershell
New-Item -ItemType Directory -Force -Path services/ai-service/app, services/ai-service/tests, services/mock-api/app, services/mock-api/tests, fixtures/events, fixtures/policies, fixtures/data, scripts, n8n/workflows, docs | Out-Null
```

预期：所有项目目录都存在。

- [ ] **Step 2: 创建 `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: agent_ops
      POSTGRES_USER: agent
      POSTGRES_PASSWORD: agent
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agent -d agent_ops"]
      interval: 5s
      timeout: 3s
      retries: 20

  ai-service:
    build:
      context: ./services/ai-service
    environment:
      AI_MODE: fake
      POLICY_PATH: /app/fixtures/policies/after_sales_policy.md
    volumes:
      - ./fixtures:/app/fixtures:ro
    ports:
      - "8001:8000"

  mock-api:
    build:
      context: ./services/mock-api
    environment:
      DATABASE_URL: postgresql+psycopg://agent:agent@postgres:5432/agent_ops
      FIXTURE_DIR: /app/fixtures
    volumes:
      - ./fixtures:/app/fixtures:ro
    ports:
      - "8002:8000"
    depends_on:
      postgres:
        condition: service_healthy

  n8n:
    image: n8nio/n8n:1.95.3
    environment:
      N8N_HOST: localhost
      N8N_PORT: 5678
      N8N_PROTOCOL: http
      WEBHOOK_URL: http://localhost:5678/
      N8N_SECURE_COOKIE: "false"
      AI_SERVICE_URL: http://ai-service:8000
      MOCK_API_URL: http://mock-api:8000
    ports:
      - "5678:5678"
    volumes:
      - n8n_data:/home/node/.n8n
      - ./n8n/workflows:/workflows:ro
    depends_on:
      - ai-service
      - mock-api

volumes:
  postgres_data:
  n8n_data:
```

- [ ] **Step 3: 创建 `.env.example`**

```dotenv
AI_MODE=fake
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
DATABASE_URL=postgresql+psycopg://agent:agent@localhost:5432/agent_ops
AI_SERVICE_URL=http://localhost:8001
MOCK_API_URL=http://localhost:8002
N8N_URL=http://localhost:5678
```

- [ ] **Step 4: 创建 package config 和 Dockerfile**

`services/ai-service/pyproject.toml`:

```toml
[project]
name = "ai-service"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi==0.115.12", "uvicorn[standard]==0.34.2", "pydantic==2.11.4"]

[project.optional-dependencies]
test = ["pytest==8.3.5", "httpx==0.28.1", "ruff==0.11.10"]

[tool.pytest.ini_options]
pythonpath = ["."]
```

`services/mock-api/pyproject.toml`:

```toml
[project]
name = "mock-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi==0.115.12", "uvicorn[standard]==0.34.2", "pydantic==2.11.4", "sqlalchemy==2.0.41", "psycopg[binary]==3.2.9"]

[project.optional-dependencies]
test = ["pytest==8.3.5", "httpx==0.28.1", "ruff==0.11.10"]

[tool.pytest.ini_options]
pythonpath = ["."]
```

两个服务都使用这个 Dockerfile：

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY app ./app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 5: 提交 scaffold**

```powershell
git add docker-compose.yml .env.example services/ai-service services/mock-api
git commit -m "chore: scaffold docker services"
```

---

### Task 2: 添加 Demo Fixtures 和政策文件

**Files:**
- Create: `fixtures/policies/after_sales_policy.md`
- Create: `fixtures/data/customers.json`
- Create: `fixtures/data/orders.json`
- Create: `fixtures/data/shipments.json`
- Create: `fixtures/data/inventory.json`
- Create: `fixtures/events/*.json`

- [ ] **Step 1: 创建 `fixtures/policies/after_sales_policy.md`**

```markdown
# After-sales Policy

## Refunds
- Orders below 100 USD can be recommended for refund when the customer reports a damaged item within 30 days.
- Orders at or above 100 USD require human approval before refund recommendation is final.
- VIP customers require human approval for financial compensation.

## Logistics
- Delivery delay above 5 days can receive a shipping credit recommendation.
- Lost package claims require logistics case creation.

## Reviews
- Public bad reviews with rating 2 or below require brand-risk approval.
- Draft replies must acknowledge the issue, avoid blame, and offer a concrete next step.

## Inventory
- Stock below reorder threshold should trigger procurement alert.
- Stockout risk is high when available stock minus pending orders is below zero.
```

- [ ] **Step 2: 创建数据 fixtures**

`fixtures/data/customers.json`:

```json
[
  {"customer_id":"cus_100","tier":"standard","region":"US","lifetime_value":340},
  {"customer_id":"cus_200","tier":"vip","region":"US","lifetime_value":4200}
]
```

`fixtures/data/orders.json`:

```json
[
  {"order_id":"ord_100","customer_id":"cus_100","sku":"sku_bottle_1","value":45,"status":"delivered","shipment_id":"ship_100"},
  {"order_id":"ord_200","customer_id":"cus_200","sku":"sku_bag_1","value":240,"status":"delivered","shipment_id":"ship_200"},
  {"order_id":"ord_300","customer_id":"cus_100","sku":"sku_lamp_1","value":80,"status":"in_transit","shipment_id":"ship_300"}
]
```

`fixtures/data/shipments.json`:

```json
[
  {"shipment_id":"ship_100","carrier":"UPS","status":"delivered","delay_days":0},
  {"shipment_id":"ship_200","carrier":"FedEx","status":"delivered","delay_days":0},
  {"shipment_id":"ship_300","carrier":"DHL","status":"delayed","delay_days":7}
]
```

`fixtures/data/inventory.json`:

```json
[
  {"sku":"sku_bottle_1","available":42,"pending_orders":8,"reorder_threshold":20},
  {"sku":"sku_bag_1","available":5,"pending_orders":9,"reorder_threshold":15},
  {"sku":"sku_lamp_1","available":2,"pending_orders":6,"reorder_threshold":10}
]
```

- [ ] **Step 3: 创建事件 fixtures**

每个文件必须包含 `event_id`、`event_type`、`source`、`customer_id`、`order_id`、`sku`、`shipment_id`、`message` 和 `created_at`。

创建：

- `refund_normal.json`
- `refund_high_value.json`
- `logistics_delay.json`
- `bad_review_public.json`
- `low_stock.json`
- `low_confidence.json`
- `mock_api_failure.json`

使用这个结构：

```json
{
  "event_id":"evt_refund_high_value",
  "event_type":"refund_request",
  "source":"support_inbox",
  "customer_id":"cus_200",
  "order_id":"ord_200",
  "sku":"sku_bag_1",
  "shipment_id":"ship_200",
  "message":"The premium bag arrived with a broken zipper. I want a full refund.",
  "created_at":"2026-05-19T05:00:00Z"
}
```

- [ ] **Step 4: 提交 fixtures**

```powershell
git add fixtures
git commit -m "test: add after-sales demo fixtures"
```

---

### Task 3: 实现 AI Service

**Files:**
- Create: `services/ai-service/app/schemas.py`
- Create: `services/ai-service/app/decision_engine.py`
- Create: `services/ai-service/app/main.py`
- Create: `services/ai-service/tests/test_decision_engine.py`
- Create: `services/ai-service/tests/test_api.py`

- [ ] **Step 1: 编写失败测试**

`services/ai-service/tests/test_decision_engine.py`:

```python
from app.decision_engine import decide
from app.schemas import EventContext


def test_high_value_refund_requires_approval():
    event = EventContext(
        event_id="evt_refund_high_value",
        event_type="refund_request",
        source="support_inbox",
        customer={"customer_id": "cus_200", "tier": "vip"},
        order={"order_id": "ord_200", "value": 240, "sku": "sku_bag_1"},
        inventory={"sku": "sku_bag_1", "available": 5, "pending_orders": 9, "reorder_threshold": 15},
        shipment={"shipment_id": "ship_200", "status": "delivered", "delay_days": 0},
        message="The premium bag arrived with a broken zipper. I want a full refund.",
        created_at="2026-05-19T05:00:00Z",
    )
    decision = decide(event)
    assert decision.category == "refund_request"
    assert decision.priority == "high"
    assert decision.requires_approval is True
    assert decision.recommended_action == "review_refund_request"
```

- [ ] **Step 2: 验证测试失败**

```powershell
cd services/ai-service
python -m pip install -e .[test]
pytest tests/test_decision_engine.py -v
```

预期：因为实现文件还不存在，出现 import failure。

- [ ] **Step 3: 实现 schemas**

`services/ai-service/app/schemas.py`:

```python
from typing import Any, Literal
from pydantic import BaseModel, Field

EventType = Literal["refund_request", "logistics_delay", "bad_review", "low_stock"]
Priority = Literal["low", "medium", "high"]


class EventContext(BaseModel):
    event_id: str
    event_type: EventType
    source: str
    customer: dict[str, Any] | None = None
    order: dict[str, Any] | None = None
    inventory: dict[str, Any] | None = None
    shipment: dict[str, Any] | None = None
    message: str
    created_at: str


class DecisionOutput(BaseModel):
    event_id: str
    category: EventType
    priority: Priority
    recommended_action: str
    requires_approval: bool
    confidence: float = Field(ge=0, le=1)
    explanation: str
    draft_response: str
    internal_task_summary: str
    policy_references: list[str]
```

- [ ] **Step 4: 实现 deterministic decision engine**

`services/ai-service/app/decision_engine.py`:

```python
from app.schemas import DecisionOutput, EventContext


def decide(event: EventContext) -> DecisionOutput:
    if event.event_type == "refund_request":
        value = float((event.order or {}).get("value", 0))
        tier = (event.customer or {}).get("tier", "standard")
        requires_approval = value >= 100 or tier == "vip"
        return DecisionOutput(
            event_id=event.event_id,
            category="refund_request",
            priority="high" if requires_approval else "medium",
            recommended_action="review_refund_request" if requires_approval else "offer_refund",
            requires_approval=requires_approval,
            confidence=0.86,
            explanation="Refund policy requires approval for high-value orders or VIP customers.",
            draft_response="Thanks for sharing the issue. We are reviewing the order and will follow up shortly.",
            internal_task_summary=f"Review refund request for order {(event.order or {}).get('order_id', 'unknown')}.",
            policy_references=["Refunds: high-value and VIP compensation require approval"],
        )
    if event.event_type == "logistics_delay":
        return DecisionOutput(
            event_id=event.event_id,
            category="logistics_delay",
            priority="high",
            recommended_action="offer_shipping_credit",
            requires_approval=(event.customer or {}).get("tier") == "vip",
            confidence=0.84,
            explanation="Shipping credit is recommended when delivery delay exceeds 5 days.",
            draft_response="We are sorry for the delivery delay. We checked the shipment and will help resolve this promptly.",
            internal_task_summary="Create logistics follow-up case and notify support.",
            policy_references=["Logistics: delays above 5 days can receive shipping credit"],
        )
    if event.event_type == "bad_review":
        return DecisionOutput(
            event_id=event.event_id,
            category="bad_review",
            priority="high",
            recommended_action="draft_recovery_reply",
            requires_approval=True,
            confidence=0.81,
            explanation="Public bad reviews require brand-risk approval before final response.",
            draft_response="We are sorry about your experience and would like to make this right.",
            internal_task_summary="Review public response draft before publishing.",
            policy_references=["Reviews: public bad reviews require approval"],
        )
    inventory = event.inventory or {}
    available = int(inventory.get("available", 0))
    pending = int(inventory.get("pending_orders", 0))
    threshold = int(inventory.get("reorder_threshold", 0))
    high_risk = available - pending < 0 or available < threshold
    return DecisionOutput(
        event_id=event.event_id,
        category="low_stock",
        priority="high" if high_risk else "medium",
        recommended_action="create_reorder_alert" if high_risk else "monitor_inventory",
        requires_approval=False,
        confidence=0.9,
        explanation="Available stock is below threshold or cannot cover pending orders.",
        draft_response="",
        internal_task_summary=f"Create procurement alert for SKU {inventory.get('sku', 'unknown')}.",
        policy_references=["Inventory: stock below reorder threshold should trigger procurement alert"],
    )
```

- [ ] **Step 5: 实现 API**

`services/ai-service/app/main.py`:

```python
from fastapi import FastAPI
from app.decision_engine import decide
from app.schemas import DecisionOutput, EventContext

app = FastAPI(title="Ecommerce After-sales AI Service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/decide", response_model=DecisionOutput)
def create_decision(event: EventContext) -> DecisionOutput:
    return decide(event)
```

- [ ] **Step 6: 添加 API 测试**

`services/ai-service/tests/test_api.py`:

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}
```

- [ ] **Step 7: 运行测试并提交**

```powershell
cd services/ai-service
pytest -v
cd ../..
git add services/ai-service
git commit -m "feat: add deterministic AI decision service"
```

---

### Task 4: 实现 Mock API

**Files:**
- Create: `services/mock-api/app/store.py`
- Create: `services/mock-api/app/main.py`
- Create: `services/mock-api/tests/test_api.py`

- [ ] **Step 1: 编写失败测试**

`services/mock-api/tests/test_api.py`:

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_get_order_fixture():
    response = client.get("/orders/ord_100")
    assert response.status_code == 200
    assert response.json()["order_id"] == "ord_100"


def test_create_approval_request():
    payload = {"event_id": "evt_refund_high_value", "recommended_action": "review_refund_request", "explanation": "High-value refund requires approval."}
    response = client.post("/approval-requests", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
```

- [ ] **Step 2: 实现 fixture store**

`services/mock-api/app/store.py`:

```python
import json
import os
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(os.getenv("FIXTURE_DIR", "../../fixtures")).resolve()


def load_json(name: str) -> list[dict[str, Any]]:
    with (FIXTURE_DIR / "data" / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_by_id(filename: str, key: str, value: str) -> dict[str, Any] | None:
    for item in load_json(filename):
        if item.get(key) == value:
            return item
    return None
```

- [ ] **Step 3: 实现 mock API**

`services/mock-api/app/main.py`:

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from app.store import find_by_id

app = FastAPI(title="Ecommerce Mock Enterprise API")

APPROVALS: list[dict] = []
TICKETS: list[dict] = []
RUN_LOGS: list[dict] = []
DEAD_LETTERS: list[dict] = []
REPLAYS: list[dict] = []


class ApprovalRequest(BaseModel):
    event_id: str
    recommended_action: str
    explanation: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/orders/{order_id}")
def get_order(order_id: str) -> dict:
    order = find_by_id("orders.json", "order_id", order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@app.get("/customers/{customer_id}")
def get_customer(customer_id: str) -> dict:
    customer = find_by_id("customers.json", "customer_id", customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    return customer


@app.get("/shipments/{shipment_id}")
def get_shipment(shipment_id: str) -> dict:
    shipment = find_by_id("shipments.json", "shipment_id", shipment_id)
    if not shipment:
        raise HTTPException(status_code=404, detail="shipment not found")
    return shipment


@app.get("/inventory/{sku}")
def get_inventory(sku: str) -> dict:
    inventory = find_by_id("inventory.json", "sku", sku)
    if not inventory:
        raise HTTPException(status_code=404, detail="inventory not found")
    return inventory


@app.post("/approval-requests")
def create_approval(request: ApprovalRequest) -> dict:
    record = request.model_dump() | {"status": "pending"}
    APPROVALS.append(record)
    return record


@app.post("/tickets")
def create_ticket(payload: dict) -> dict:
    record = payload | {"status": "open"}
    TICKETS.append(record)
    return record


@app.post("/internal-notifications")
def create_notification(payload: dict) -> dict:
    return payload | {"status": "sent"}


@app.post("/run-logs")
def create_run_log(payload: dict) -> dict:
    RUN_LOGS.append(payload)
    return payload


@app.post("/dead-letter")
def create_dead_letter(payload: dict) -> dict:
    record = payload | {"status": "dead_lettered"}
    DEAD_LETTERS.append(record)
    return record


@app.post("/replay/{event_id}")
def replay_event(event_id: str) -> dict:
    record = {"event_id": event_id, "status": "queued_for_replay"}
    REPLAYS.append(record)
    return record
```

- [ ] **Step 4: 运行测试并提交**

```powershell
cd services/mock-api
python -m pip install -e .[test]
pytest -v
cd ../..
git add services/mock-api
git commit -m "feat: add mock enterprise API"
```

---

### Task 5: 添加脚本和 n8n Workflow Contract

**Files:**
- Create: `scripts/send_event.ps1`
- Create: `scripts/replay_failed_event.ps1`
- Create: `docs/n8n-workflow-contract.md`
- Create: `n8n/workflows/ecommerce-after-sales.json`

- [ ] **Step 1: 创建 `scripts/send_event.ps1`**

```powershell
param(
  [string]$EventFile = "fixtures/events/refund_high_value.json",
  [string]$WebhookUrl = "http://localhost:5678/webhook/after-sales-event"
)

$payload = Get-Content -Raw $EventFile
Invoke-RestMethod -Method Post -Uri $WebhookUrl -ContentType "application/json" -Body $payload | ConvertTo-Json -Depth 10
```

- [ ] **Step 2: 创建 `scripts/replay_failed_event.ps1`**

```powershell
param(
  [string]$EventId = "evt_mock_api_failure",
  [string]$MockApiUrl = "http://localhost:8002"
)

Invoke-RestMethod -Method Post -Uri "$MockApiUrl/replay/$EventId" | ConvertTo-Json -Depth 5
```

- [ ] **Step 3: 创建 workflow contract**

`docs/n8n-workflow-contract.md`:

```markdown
# n8n Workflow Contract

Webhook path: `/webhook/after-sales-event`

Required steps:

1. Receive event.
2. Fetch order, customer, shipment, and inventory from `mock-api`.
3. Build `EventContext`.
4. POST to `ai-service /decide`.
5. If `requires_approval` is true, POST to `mock-api /approval-requests`.
6. Otherwise POST to `mock-api /tickets` or `/internal-notifications`.
7. POST run result to `mock-api /run-logs`.
8. On unrecoverable error, POST to `mock-api /dead-letter`.
```

- [ ] **Step 4: 创建初始 workflow export**

`n8n/workflows/ecommerce-after-sales.json`:

```json
{
  "name": "Ecommerce After-sales Agent Workflow",
  "nodes": [],
  "connections": {},
  "settings": {},
  "staticData": null,
  "pinData": {}
}
```

- [ ] **Step 5: 提交 workflow contract**

```powershell
git add scripts n8n/workflows/ecommerce-after-sales.json docs/n8n-workflow-contract.md
git commit -m "docs: define n8n workflow contract"
```

---

### Task 6: 验证 Docker Runtime 并构建真实 n8n Workflow

**Files:**
- Replace: `n8n/workflows/ecommerce-after-sales.json`
- Create: `docs/local-runbook.md`

- [ ] **Step 1: 启动 Docker 服务**

```powershell
docker compose up --build
```

预期：

- n8n 位于 `http://localhost:5678`。
- AI service health 位于 `http://localhost:8001/health`。
- Mock API health 位于 `http://localhost:8002/health`。

- [ ] **Step 2: 验证端点**

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/orders/ord_100
Invoke-RestMethod http://localhost:8002/inventory/sku_lamp_1
```

- [ ] **Step 3: 在 UI 中构建 n8n workflow**

创建节点：

1. Webhook `after-sales-event`。
2. HTTP Request 获取 order。
3. HTTP Request 获取 customer。
4. HTTP Request 获取 shipment。
5. HTTP Request 获取 inventory。
6. Code 或 Set node 构建 `EventContext`。
7. HTTP Request `POST {{$env.AI_SERVICE_URL}}/decide`。
8. IF node 判断 `requires_approval`。
9. 审批分支请求 `POST {{$env.MOCK_API_URL}}/approval-requests`。
10. 普通分支请求 `POST {{$env.MOCK_API_URL}}/tickets`。
11. 最终日志请求 `POST {{$env.MOCK_API_URL}}/run-logs`。

- [ ] **Step 4: 导出 workflow 并测试**

从 n8n 导出 workflow JSON，并替换 `n8n/workflows/ecommerce-after-sales.json`。

然后运行：

```powershell
./scripts/send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

预期：mock-api 中创建一条 approval request。

- [ ] **Step 5: 提交导出的 workflow 和 runbook**

```powershell
git add n8n/workflows/ecommerce-after-sales.json docs/local-runbook.md
git commit -m "feat: add n8n after-sales workflow"
```

---

### Task 7: 添加作品集文档

**Files:**
- Modify: `README.md`
- Create: `docs/architecture.md`
- Create: `docs/demo-script.md`

- [ ] **Step 1: 编写 `README.md`**

必须包含以下章节：

```markdown
# Ecommerce After-sales Multi-agent Workflow

## What This Demonstrates

- n8n workflow orchestration for enterprise-style automation.
- FastAPI AI service with structured outputs and deterministic test mode.
- Mock enterprise APIs for orders, inventory, logistics, support, approvals, run logs, and replay.
- Docker Compose local deployment.
- AI Ops patterns: schema validation, approval guardrails, run logging, dead-letter, replay.

## Quick Start

```powershell
docker compose up --build
```

Open n8n at `http://localhost:5678`.

## Demo

```powershell
./scripts/send_event.ps1 -EventFile fixtures/events/refund_high_value.json
./scripts/replay_failed_event.ps1 -EventId evt_mock_api_failure
```
```

- [ ] **Step 2: 编写 `docs/architecture.md`**

包含：

- n8n 作为编排层。
- ai-service 作为面向模型逻辑的边界。
- mock-api 作为可替换企业系统模拟层。
- Postgres 作为运维存储。
- SaaS 替换点：Shopify、Zendesk、Slack、ERP、物流服务商。

- [ ] **Step 3: 编写 `docs/demo-script.md`**

包含 3-5 分钟 walkthrough：

1. 业务问题。
2. 架构。
3. 运行 Docker Compose。
4. 触发退款审批事件。
5. 展示 AI decision JSON。
6. 展示 approval 或 run log。
7. 解释 failure replay。
8. 解释如何映射到真实 SaaS 系统。

- [ ] **Step 4: 提交文档**

```powershell
git add README.md docs/architecture.md docs/demo-script.md
git commit -m "docs: add portfolio documentation"
```

---

### Task 8: 最终验证

**Files:**
- 只有验证发现真实问题时才修改。

- [ ] **Step 1: 运行单元测试**

```powershell
cd services/ai-service
pytest -v
cd ../mock-api
pytest -v
cd ../..
```

预期：全部测试通过。

- [ ] **Step 2: 运行 Docker Compose**

```powershell
docker compose up --build
```

预期：n8n、ai-service、mock-api 和 postgres 启动。

- [ ] **Step 3: 运行 health checks**

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8002/orders/ord_100
```

预期：返回 JSON 响应。

- [ ] **Step 4: 运行 demo scripts**

```powershell
./scripts/send_event.ps1 -EventFile fixtures/events/refund_high_value.json
./scripts/replay_failed_event.ps1 -EventId evt_mock_api_failure
```

预期：导入 workflow 后事件能到达 n8n；replay 返回 `queued_for_replay`。

- [ ] **Step 5: 如有需要提交最终修复**

```powershell
git status --short
```

预期：工作区干净。如果有文件变更，使用：

```powershell
git add .
git commit -m "fix: resolve final verification issues"
```

