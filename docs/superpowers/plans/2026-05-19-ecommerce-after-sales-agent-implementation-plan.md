# Ecommerce After-sales Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Docker-first ecommerce after-sales multi-agent workflow demo with n8n orchestration, FastAPI AI service, FastAPI mock enterprise APIs, Postgres storage, fixtures, tests, and portfolio documentation.

**Architecture:** n8n is the workflow orchestrator and calls internal services over the Docker network. `mock-api` simulates enterprise systems and operational records. `ai-service` owns model-facing behavior, structured decision output, and deterministic test behavior.

**Tech Stack:** Docker Compose, n8n, Python 3.12, FastAPI, Pydantic v2, pytest, httpx, Postgres 16, SQLAlchemy 2, Ruff.

---

## Implementation Decisions

- Use FastAPI for both `services/ai-service` and `services/mock-api`.
- Use Postgres in Docker Compose, with service code reading `DATABASE_URL`.
- Use deterministic fake AI mode first so tests and demos run without paid API keys.
- Simulate approvals through `mock-api` in the MVP.
- Keep workflow import/export in `n8n/workflows/ecommerce-after-sales.json`.

## File Map

- `docker-compose.yml`: runs n8n, Postgres, ai-service, and mock-api.
- `.env.example`: documents local environment variables.
- `README.md`: portfolio-facing setup, architecture, and demo instructions.
- `services/ai-service/app/main.py`: FastAPI routes for health and `/decide`.
- `services/ai-service/app/schemas.py`: request and response models.
- `services/ai-service/app/decision_engine.py`: triage, policy, inventory, and response drafting logic.
- `services/ai-service/tests/`: AI service tests.
- `services/mock-api/app/main.py`: FastAPI routes for enterprise mock systems.
- `services/mock-api/app/store.py`: fixture loading and later persistence helpers.
- `services/mock-api/tests/`: mock API tests.
- `fixtures/events/*.json`: scripted demo events.
- `fixtures/policies/after_sales_policy.md`: refund, logistics, review, and inventory rules.
- `fixtures/data/*.json`: deterministic orders, customers, shipments, and inventory.
- `scripts/send_event.ps1`: sends a demo event to n8n.
- `scripts/replay_failed_event.ps1`: replays a failed event through mock-api.
- `n8n/workflows/ecommerce-after-sales.json`: importable n8n workflow export.
- `docs/architecture.md`: architecture explanation and replaceable SaaS points.
- `docs/demo-script.md`: 3-5 minute portfolio demo script.

---

### Task 1: Scaffold Docker-first Project Structure

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `services/ai-service/pyproject.toml`
- Create: `services/ai-service/Dockerfile`
- Create: `services/ai-service/app/__init__.py`
- Create: `services/mock-api/pyproject.toml`
- Create: `services/mock-api/Dockerfile`
- Create: `services/mock-api/app/__init__.py`

- [ ] **Step 1: Create directories**

Run:

```powershell
New-Item -ItemType Directory -Force -Path services/ai-service/app, services/ai-service/tests, services/mock-api/app, services/mock-api/tests, fixtures/events, fixtures/policies, fixtures/data, scripts, n8n/workflows, docs | Out-Null
```

Expected: all project directories exist.

- [ ] **Step 2: Create `docker-compose.yml`**

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

- [ ] **Step 3: Create `.env.example`**

```dotenv
AI_MODE=fake
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
DATABASE_URL=postgresql+psycopg://agent:agent@localhost:5432/agent_ops
AI_SERVICE_URL=http://localhost:8001
MOCK_API_URL=http://localhost:8002
N8N_URL=http://localhost:5678
```

- [ ] **Step 4: Create package configs and Dockerfiles**

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

Use this Dockerfile for both services:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY app ./app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 5: Commit scaffold**

```powershell
git add docker-compose.yml .env.example services/ai-service services/mock-api
git commit -m "chore: scaffold docker services"
```

---

### Task 2: Add Demo Fixtures and Policies

**Files:**
- Create: `fixtures/policies/after_sales_policy.md`
- Create: `fixtures/data/customers.json`
- Create: `fixtures/data/orders.json`
- Create: `fixtures/data/shipments.json`
- Create: `fixtures/data/inventory.json`
- Create: `fixtures/events/*.json`

- [ ] **Step 1: Create `fixtures/policies/after_sales_policy.md`**

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

- [ ] **Step 2: Create data fixtures**

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

- [ ] **Step 3: Create event fixtures**

Each file must include `event_id`, `event_type`, `source`, `customer_id`, `order_id`, `sku`, `shipment_id`, `message`, and `created_at`.

Create:

- `refund_normal.json`
- `refund_high_value.json`
- `logistics_delay.json`
- `bad_review_public.json`
- `low_stock.json`
- `low_confidence.json`
- `mock_api_failure.json`

Use this shape:

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

- [ ] **Step 4: Commit fixtures**

```powershell
git add fixtures
git commit -m "test: add after-sales demo fixtures"
```

---

### Task 3: Implement AI Service

**Files:**
- Create: `services/ai-service/app/schemas.py`
- Create: `services/ai-service/app/decision_engine.py`
- Create: `services/ai-service/app/main.py`
- Create: `services/ai-service/tests/test_decision_engine.py`
- Create: `services/ai-service/tests/test_api.py`

- [ ] **Step 1: Write failing tests**

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

- [ ] **Step 2: Verify failure**

```powershell
cd services/ai-service
python -m pip install -e .[test]
pytest tests/test_decision_engine.py -v
```

Expected: import failure because implementation files do not exist.

- [ ] **Step 3: Implement schemas**

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

- [ ] **Step 4: Implement deterministic decision engine**

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

- [ ] **Step 5: Implement API**

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

- [ ] **Step 6: Add API test**

`services/ai-service/tests/test_api.py`:

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}
```

- [ ] **Step 7: Run tests and commit**

```powershell
cd services/ai-service
pytest -v
cd ../..
git add services/ai-service
git commit -m "feat: add deterministic AI decision service"
```

---

### Task 4: Implement Mock API

**Files:**
- Create: `services/mock-api/app/store.py`
- Create: `services/mock-api/app/main.py`
- Create: `services/mock-api/tests/test_api.py`

- [ ] **Step 1: Write failing tests**

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

- [ ] **Step 2: Implement fixture store**

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

- [ ] **Step 3: Implement mock API**

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

- [ ] **Step 4: Run tests and commit**

```powershell
cd services/mock-api
python -m pip install -e .[test]
pytest -v
cd ../..
git add services/mock-api
git commit -m "feat: add mock enterprise API"
```

---

### Task 5: Add Scripts and n8n Workflow Contract

**Files:**
- Create: `scripts/send_event.ps1`
- Create: `scripts/replay_failed_event.ps1`
- Create: `docs/n8n-workflow-contract.md`
- Create: `n8n/workflows/ecommerce-after-sales.json`

- [ ] **Step 1: Create `scripts/send_event.ps1`**

```powershell
param(
  [string]$EventFile = "fixtures/events/refund_high_value.json",
  [string]$WebhookUrl = "http://localhost:5678/webhook/after-sales-event"
)

$payload = Get-Content -Raw $EventFile
Invoke-RestMethod -Method Post -Uri $WebhookUrl -ContentType "application/json" -Body $payload | ConvertTo-Json -Depth 10
```

- [ ] **Step 2: Create `scripts/replay_failed_event.ps1`**

```powershell
param(
  [string]$EventId = "evt_mock_api_failure",
  [string]$MockApiUrl = "http://localhost:8002"
)

Invoke-RestMethod -Method Post -Uri "$MockApiUrl/replay/$EventId" | ConvertTo-Json -Depth 5
```

- [ ] **Step 3: Create workflow contract**

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

- [ ] **Step 4: Create initial workflow export**

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

- [ ] **Step 5: Commit workflow contract**

```powershell
git add scripts n8n/workflows/ecommerce-after-sales.json docs/n8n-workflow-contract.md
git commit -m "docs: define n8n workflow contract"
```

---

### Task 6: Verify Docker Runtime and Build Real n8n Workflow

**Files:**
- Replace: `n8n/workflows/ecommerce-after-sales.json`
- Create: `docs/local-runbook.md`

- [ ] **Step 1: Start Docker services**

```powershell
docker compose up --build
```

Expected:

- n8n at `http://localhost:5678`.
- AI service health at `http://localhost:8001/health`.
- Mock API health at `http://localhost:8002/health`.

- [ ] **Step 2: Verify endpoints**

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/orders/ord_100
Invoke-RestMethod http://localhost:8002/inventory/sku_lamp_1
```

- [ ] **Step 3: Build n8n workflow in UI**

Create nodes:

1. Webhook `after-sales-event`.
2. HTTP Request to get order.
3. HTTP Request to get customer.
4. HTTP Request to get shipment.
5. HTTP Request to get inventory.
6. Code or Set node to build `EventContext`.
7. HTTP Request `POST {{$env.AI_SERVICE_URL}}/decide`.
8. IF node for `requires_approval`.
9. Approval branch to `POST {{$env.MOCK_API_URL}}/approval-requests`.
10. Normal branch to `POST {{$env.MOCK_API_URL}}/tickets`.
11. Final log to `POST {{$env.MOCK_API_URL}}/run-logs`.

- [ ] **Step 4: Export workflow and test**

Export workflow JSON from n8n and replace `n8n/workflows/ecommerce-after-sales.json`.

Then run:

```powershell
./scripts/send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

Expected: an approval request is created in mock-api.

- [ ] **Step 5: Commit exported workflow and runbook**

```powershell
git add n8n/workflows/ecommerce-after-sales.json docs/local-runbook.md
git commit -m "feat: add n8n after-sales workflow"
```

---

### Task 7: Add Portfolio Documentation

**Files:**
- Modify: `README.md`
- Create: `docs/architecture.md`
- Create: `docs/demo-script.md`

- [ ] **Step 1: Write `README.md`**

Required sections:

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

- [ ] **Step 2: Write `docs/architecture.md`**

Include:

- n8n as orchestration layer.
- ai-service as model-facing logic boundary.
- mock-api as replaceable enterprise system simulation.
- Postgres as operational store.
- SaaS replacement points: Shopify, Zendesk, Slack, ERP, logistics provider.

- [ ] **Step 3: Write `docs/demo-script.md`**

Include a 3-5 minute walkthrough:

1. Business problem.
2. Architecture.
3. Run Docker Compose.
4. Trigger refund approval event.
5. Show AI decision JSON.
6. Show approval or run log.
7. Explain failure replay.
8. Explain mapping to real SaaS systems.

- [ ] **Step 4: Commit docs**

```powershell
git add README.md docs/architecture.md docs/demo-script.md
git commit -m "docs: add portfolio documentation"
```

---

### Task 8: Final Verification

**Files:**
- Modify only if verification finds a real issue.

- [ ] **Step 1: Run unit tests**

```powershell
cd services/ai-service
pytest -v
cd ../mock-api
pytest -v
cd ../..
```

Expected: all tests pass.

- [ ] **Step 2: Run Docker Compose**

```powershell
docker compose up --build
```

Expected: n8n, ai-service, mock-api, and postgres start.

- [ ] **Step 3: Run health checks**

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8002/orders/ord_100
```

Expected: JSON responses.

- [ ] **Step 4: Run demo scripts**

```powershell
./scripts/send_event.ps1 -EventFile fixtures/events/refund_high_value.json
./scripts/replay_failed_event.ps1 -EventId evt_mock_api_failure
```

Expected: event reaches n8n after workflow import; replay returns `queued_for_replay`.

- [ ] **Step 5: Commit final fixes if needed**

```powershell
git status --short
```

Expected: clean working tree. If files changed, commit with:

```powershell
git add .
git commit -m "fix: resolve final verification issues"
```

