# Demo Script

Use this as a 3-5 minute walkthrough for a portfolio interview or screen recording.

## 1. Business Problem

"This project simulates an internal ecommerce after-sales workflow. In a real company, refund requests, logistics delays, public bad reviews, and low-stock events often touch multiple systems: ecommerce backend, support desk, logistics provider, inventory, approval process, and team notifications. The goal is to use an AI-assisted workflow to classify the event, gather context, decide the next action, and keep operational records."

## 2. Architecture

"The system has three main application layers. n8n is the workflow orchestrator. `ai-service` is the model-facing boundary that returns structured decisions. `mock-api` simulates enterprise systems like orders, customers, shipments, inventory, approvals, tickets, notifications, run logs, dead letters, and replay."

Show:

- `docker-compose.yml`
- `n8n/workflows/ecommerce-after-sales.json`
- `services/ai-service/app/schemas.py`
- `services/mock-api/app/main.py`

## 3. Start the Stack

```powershell
docker compose up --build -d
```

Then open n8n:

```text
http://localhost:5678
```

Check services:

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
```

## 4. Trigger Refund Approval Event

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

Explain:

"This event represents a high-value refund request from a customer. n8n fetches the order, customer, shipment, and inventory context, sends the context to `ai-service`, then applies the decision. Because the order is high-value, the decision requires approval."

## 5. Show AI Decision JSON

Point to these fields in the response:

- `category`: `refund_request`
- `priority`: `high`
- `recommended_action`: `review_refund_request`
- `requires_approval`: `true`
- `confidence`: deterministic demo confidence
- `policy_references`: refund policy guardrail

Use this explanation:

"The AI layer does not return free-form text only. It returns a structured decision that the workflow can safely route."

## 6. Show Approval and Run Log

Run:

```powershell
Invoke-RestMethod http://localhost:8002/approval-requests | ConvertTo-Json -Depth 10
Invoke-RestMethod http://localhost:8002/run-logs | ConvertTo-Json -Depth 10
```

Explain:

"The approval request is the business action. The run log is the AI Ops record. It gives us event id, workflow id, status, latency, model, token estimate, and error field."

## 7. Explain Failure Replay

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\replay_failed_event.ps1 -EventId evt_mock_api_failure
```

Expected output:

```json
{
  "event_id": "evt_mock_api_failure",
  "status": "queued_for_replay"
}
```

Explain:

"Production AI workflows need recovery paths. This demo includes a replay endpoint so failed events can be queued and retried instead of disappearing."

## 8. Map to Real SaaS Systems

Close with:

"The mock systems can be replaced with real SaaS APIs. Orders and inventory can come from Shopify or an internal commerce backend. Support tickets can go to Zendesk. Notifications can go to Slack or Teams. Procurement can connect to ERP. Shipment status can come from logistics providers. The AI service can be connected to OpenAI, Azure OpenAI, Anthropic, or an internal model gateway without rewriting the n8n orchestration."
