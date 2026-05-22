# Agent Context Summary

Use this file first when working in this repository. It is intentionally short so future Codex sessions do not need to rescan the full project before small tasks.

## Project Root

- User-facing root: `D:\Project\agent`
- Active implementation worktree: `D:\Project\agent\.worktrees\after-sales-implementation`
- Main branch in use for current work: `after-sales-implementation`
- Remote: `https://github.com/talon1126/agent.git`

## Runtime Shape

The project is a Docker-first internal ecommerce operations copilot.

- `n8n` owns workflow orchestration, department webhook routing, and calls between services.
- `feishu-adapter` owns Feishu/Lark protocol handling. It supports a multi-bot gateway mode with `FEISHU_BOTS_JSON`, uses long connection mode by default, normalizes inbound messages, forwards each bot to its department n8n webhook, deduplicates by `bot_name + message_id`, and replies to Feishu.
- `ai-service` owns backend AI logic that should be testable without n8n. It currently exposes deterministic decisioning and a message handling endpoint.
- `mock-api` simulates enterprise systems: orders, customers, shipments, inventory, warehouse operations, approvals, tickets, internal notifications, run logs, dead letters, and replay.
- `feishu-adapter` can publish structured message run logs to `FEISHU_RUN_LOG_URL`; the default Docker target is `mock-api /run-logs`.
- `postgres` exists as the operational store target. Current demo state is still mostly in fixtures or in-memory mock endpoints.
- `ai-service` creates `session_state` and `user_profile` in Postgres when `DATABASE_URL` is configured. Fast path stores `last_order_id` in `session_state` and mirrors it into `user_profile.profile` when `sender_id` is available.
- The recommended chat architecture is now one Feishu Gateway Adapter plus independent department workflows: `Customer Support Workflow`, `Warehouse Workflow`, `Procurement Workflow`, and `Operations Workflow`.
- `chat-parent-son-agent.json` remains as a legacy compatibility artifact, but the main internal chat path should use department workflows instead of Parent -> son dispatch.

## Key Entry Points

- Feishu department chat path: department bot -> `feishu-adapter` -> department n8n webhook -> department Agent -> tool/API -> Feishu reply.
- Feishu gateway diagnostics: `GET /health/details` on `feishu-adapter` reports bot configuration, listener count, processed message count, and run-log status without secrets.
- Warehouse inventory table provisioning and sync: `POST /warehouse/inventory-table/provision` creates or reuses a fixed-schema table inside an existing Feishu Bitable app/base; `POST /warehouse/inventory-table/sync` auto-provisions when needed and publishes a one-way snapshot from `mock-api /warehouse/inventory/{sku}`.
- Fast path: Feishu -> `feishu-adapter` -> `n8n /webhook/chat-agent-inbound` -> `ai-service /after-sales/fast-path` -> Feishu reply. If the fast path declines, the workflow falls back to Parent Agent.
- Department workflow exports: `n8n/workflows/customer-support-workflow.json`, `n8n/workflows/warehouse-workflow.json`, `n8n/workflows/procurement-workflow.json`, and `n8n/workflows/operations-workflow.json`
- Parent/son workflow export: `n8n/workflows/chat-parent-son-agent.json` is legacy compatibility.
- Message-agent workflow export: `n8n/workflows/message-agent.json`
- Event workflow export: `n8n/workflows/ecommerce-after-sales.json`
- AI message endpoint: `POST /message/handle` in `services/ai-service/app/main.py`
- AI decision endpoint: `POST /decide` in `services/ai-service/app/main.py`
- Order status tool code: `services/ai-service/app/order_status_tool.py`
- n8n customer-support tools: `order_status_tool` and `policy_search_tool` inside `n8n/workflows/customer-support-workflow.json`
- n8n warehouse tools: `warehouse_inventory_tool`, `warehouse_exception_tool`, `warehouse_fulfillment_tool`, `warehouse_inventory_table_provision_tool`, and `warehouse_inventory_table_sync_tool` inside `n8n/workflows/warehouse-workflow.json`
- n8n procurement and operations tools: `procurement_mock_tool` and `operations_mock_tool` inside their department workflows.
- n8n memory nodes: `Parent Postgres Chat Memory` and `Customer Support Postgres Chat Memory`
- Parent and son memory may share the same physical table, but their `sessionKey` values must be namespaced separately (`parent:` and `customer_support:`) to avoid cross-agent context pollution.
- n8n policy RAG tool: `policy_search_tool` inside `n8n/workflows/chat-parent-son-agent.json`
- Policy search API: `POST /policies/search` in `services/mock-api/app/main.py`
- Policy RAG eval cases: `fixtures/evals/policy_rag_eval.json`

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
- `fixtures/data/warehouse_locations.json`: warehouse location-level stock fixture data.
- `fixtures/data/warehouse_exceptions.json`: warehouse exception fixture data.
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
- Use `session_state` for durable short-term backend state that must survive `ai-service` restarts, such as the fast path `last_order_id`.
- Use `user_profile` for durable user-level facts and future summaries/preferences; keep it compact and avoid storing full chat transcripts there.
- Use department Feishu bots and department workflows for the main internal chat path; do not add new business features to the legacy Parent/Son graph unless preserving compatibility.
- The fast path may handle refund-only follow-ups like "How do I refund?" only when the same session already has a remembered `last_order_id`; otherwise it must decline so the workflow falls back to the Parent Agent.
- Use `policy_search_tool` and `/policies/search` for company-policy answers that require `source_file`, `section`, and `clause_id` metadata.
- Warehouse Agent owns inventory availability, warehouse locations, warehouse exceptions, and fulfillment-risk questions.
- Use `warehouse_inventory_tool` and `/warehouse/inventory/{sku}` for SKU stock, reserved stock, location, exception, and risk lookup.
- Use `warehouse_exception_tool` and `/warehouse/exceptions/search` for stock mismatch, pending putaway, damage, missing-location, and picking-delay questions.
- Use `warehouse_fulfillment_tool` and `/warehouse/fulfillment/check` for shipping eligibility, fulfillment blockers, and next warehouse actions.
- Use `warehouse_inventory_table_provision_tool` only when users explicitly ask to create, initialize, or configure the Feishu inventory table. It creates or reuses a table in an existing Bitable app/base, adds colored single-select fields for risk/status, and does not create the base or the inventory source of truth.
- Use `warehouse_inventory_table_sync_tool` only when users explicitly ask to sync/export/publish/show a Feishu table snapshot. If no table id is configured, the backend may auto-provision or reuse the table before syncing. Feishu table data is a read model, not the inventory source of truth.
- `Procurement Agent` and `Operations Agent` are backed by deterministic mock endpoints in their own department workflows.
- Do not commit `.env` or print secrets.
- When adding an English Markdown document, add the Chinese `.zh.md` counterpart.
- Prefer Docker-first verification before cloud deployment.

## Common Verification

Run from `D:\Project\agent\.worktrees\after-sales-implementation`:

```powershell
pytest services\ai-service\tests -v
pytest services\mock-api\tests -v
pytest services\feishu-adapter\tests -v
pytest tests\test_department_workflows.py -v
pytest tests\test_chat_parent_son_workflow.py -v
docker compose config --quiet
docker compose ps
```

Useful smoke paths:

- n8n department chat webhooks: `http://localhost:5678/webhook/customer-support-inbound`, `http://localhost:5678/webhook/warehouse-inbound`, `http://localhost:5678/webhook/procurement-inbound`, and `http://localhost:5678/webhook/operations-inbound`
- ai-service local port: `http://localhost:8001`
- mock-api local port: `http://localhost:8002`
- feishu-adapter local port: `http://localhost:8010`
- feishu-adapter diagnostics: `http://localhost:8010/health/details`
- warehouse inventory table provision: `http://localhost:8010/warehouse/inventory-table/provision`
- warehouse inventory table sync: `http://localhost:8010/warehouse/inventory-table/sync`

Expected order smoke phrase for `ord_100`: `Order ord_100 is delivered. Shipment status is delivered.`
