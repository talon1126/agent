# Agent Context Summary

Use this file first when working in this repository. It is intentionally short so future Codex sessions do not need to rescan the full project before small tasks.

## Project Root

- User-facing root: `D:\Project\agent`
- Active implementation worktree: `D:\Project\agent\.worktrees\after-sales-implementation`
- Main branch in use for current work: `after-sales-implementation`
- Remote: `https://github.com/talon1126/agent.git`

## Runtime Shape

The project is a Docker-first ecommerce after-sales multi-agent workflow.

- `n8n` owns workflow orchestration, webhook routing, parent/son agent layout, and calls between services.
- `feishu-adapter` owns Feishu/Lark protocol handling. It uses long connection mode by default, normalizes inbound messages, forwards them to n8n, deduplicates repeated Feishu message pushes, and replies to Feishu.
- `ai-service` owns backend AI logic that should be testable without n8n. It currently exposes deterministic decisioning and a message handling endpoint.
- `mock-api` simulates enterprise systems: orders, customers, shipments, inventory, approvals, tickets, internal notifications, run logs, dead letters, and replay.
- `postgres` exists as the operational store target. Current demo state is still mostly in fixtures or in-memory mock endpoints.
- The chat workflow now uses n8n Postgres Chat Memory for Feishu-scoped conversation history and a `policy_search_tool` for policy lookup with clause metadata.

## Key Entry Points

- Feishu chat path: Feishu -> `feishu-adapter` -> `n8n /webhook/chat-agent-inbound` -> Parent Agent -> son agent -> tool/API -> Feishu reply.
- Parent/son workflow export: `n8n/workflows/chat-parent-son-agent.json`
- Message-agent workflow export: `n8n/workflows/message-agent.json`
- Event workflow export: `n8n/workflows/ecommerce-after-sales.json`
- AI message endpoint: `POST /message/handle` in `services/ai-service/app/main.py`
- AI decision endpoint: `POST /decide` in `services/ai-service/app/main.py`
- Order status tool code: `services/ai-service/app/order_status_tool.py`
- n8n after-sales son agent tool: `order_status_tool` inside `n8n/workflows/chat-parent-son-agent.json`
- n8n memory nodes: `Parent Postgres Chat Memory` and `After-sales Postgres Chat Memory`
- n8n policy RAG tool: `policy_search_tool` inside `n8n/workflows/chat-parent-son-agent.json`
- Policy search API: `POST /policies/search` in `services/mock-api/app/main.py`

## ai-service Structure

- `services/ai-service/app/main.py`: FastAPI app and HTTP endpoints.
- `services/ai-service/app/message_agent.py`: deterministic message intent handling, order-id extraction, audio transcript handling, and order status tool invocation.
- `services/ai-service/app/order_status_tool.py`: calls `mock-api /orders/{order_id}` and `/shipments/{shipment_id}` and returns a structured summary.
- `services/ai-service/app/decision_engine.py`: deterministic after-sales event decision rules.
- `services/ai-service/app/schemas.py`: event decision request/response schemas.
- `services/ai-service/app/message_schemas.py`: message-agent request/response schemas.
- `services/ai-service/app/transcription.py`: audio transcription adapter boundary with mock and Qwen-ready modes.

## mock-api Structure

- `services/mock-api/app/main.py`: FastAPI mock enterprise API.
- `services/mock-api/app/store.py`: fixture loading helpers.
- `fixtures/data/orders.json`: order fixture data.
- `fixtures/data/customers.json`: customer fixture data.
- `fixtures/data/shipments.json`: shipment fixture data.
- `fixtures/data/inventory.json`: inventory fixture data.
- `fixtures/policies/after_sales_policy.md` and `fixtures/policies/after_sales_policy.zh.md`: current after-sales policy documents with stable clause IDs such as `REFUND-001`.

## Docs Structure

- `README.md` and `README.zh.md`: top-level usage and workflow import notes.
- `docs/architecture.md` and `docs/architecture.zh.md`: service boundaries and architecture explanation.
- `docs/local-runbook.md` and `docs/local-runbook.zh.md`: local Docker/n8n/Feishu verification steps.
- `docs/n8n-workflow-contract.md` and `docs/n8n-workflow-contract.zh.md`: workflow payload contracts.
- `docs/superpowers/specs/`: design specs. Keep English and Chinese versions together.
- `docs/superpowers/plans/`: implementation plans. Keep English and Chinese versions together.

## Current Design Constraints

- Keep Feishu protocol handling in `feishu-adapter`.
- Keep orchestration in n8n.
- Keep model-facing logic and deterministic testable behavior in `ai-service`.
- Keep enterprise API simulations in `mock-api`.
- Use n8n Postgres Chat Memory for conversational references such as "this order".
- Use `policy_search_tool` and `/policies/search` for company-policy answers that require `source_file`, `section`, and `clause_id` metadata.
- Do not commit `.env` or print secrets.
- When adding an English Markdown document, add the Chinese `.zh.md` counterpart.
- Prefer Docker-first verification before cloud deployment.

## Common Verification

Run from `D:\Project\agent\.worktrees\after-sales-implementation`:

```powershell
pytest services\ai-service\tests -v
pytest services\mock-api\tests -v
pytest services\feishu-adapter\tests -v
pytest tests\test_chat_parent_son_workflow.py -v
docker compose config --quiet
docker compose ps
```

Useful smoke paths:

- n8n chat webhook: `http://localhost:5678/webhook/chat-agent-inbound`
- ai-service local port: `http://localhost:8001`
- mock-api local port: `http://localhost:8002`
- feishu-adapter local port: `http://localhost:8010`

Expected order smoke phrase for `ord_100`: `Order ord_100 is delivered. Shipment status is delivered.`
