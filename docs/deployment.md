# Deployment and Operations

This project is designed to be shown locally first, then moved toward a production-like deployment without changing the core boundaries.

## Local Demo

Use Docker Compose as the default runtime:

```powershell
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://localhost:8010/health/details | ConvertTo-Json -Depth 10
```

Import and activate the four department workflows in n8n before testing real Feishu messages.

## Production-Like Shape

Keep the same service split:

- `feishu-adapter`: protocol gateway, bot routing, deduplication, Feishu replies, run logging.
- `n8n`: workflow orchestration and department-specific process ownership.
- `ai-service`: model prompts, deterministic fast paths, memory policy, transcription, RAG and provider fallback.
- `mock-api` replacement: real order, warehouse, procurement, operations, ticketing, approval and notification systems.
- `postgres`: durable session state, user profile summaries, run logs, dead letters and replay records.

## Required Environment

Do not commit `.env`. Configure these at runtime:

- `FEISHU_BOTS_JSON`: one entry per department bot, including `bot_open_id` for shared group routing.
- `FEISHU_EVENT_MODE=long_connection`
- `FEISHU_RUN_LOG_URL=http://mock-api:8000/run-logs` or the production run-log endpoint.
- `FEISHU_INVENTORY_TABLE_APP_TOKEN` and table app credentials when Warehouse Agent should provision the inventory table.
- `FEISHU_INVENTORY_TABLE_ID` after provisioning, so Warehouse Agent can publish inventory snapshots to the created Feishu table.
- `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL` for the model gateway.
- `DATABASE_URL` for durable memory and operational state.

## Operational Checks

Before a demo or deployment, verify:

```powershell
pytest services\ai-service\tests -v
pytest services\mock-api\tests -v
pytest services\feishu-adapter\tests -v
pytest tests\test_department_workflows.py -v
docker compose config --quiet
```

Then check live services:

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8010/health/details | ConvertTo-Json -Depth 10
Invoke-RestMethod http://localhost:8002/run-logs | ConvertTo-Json -Depth 10
```

## Monitoring Signals

Track these fields in logs and run-log records:

- `message_id`, `bot_name`, and `workflow`
- `status` and `error`
- `latency_ms`, `n8n_ms`, `token_ms`, and `reply_ms`
- `tool_calls` when returned by a workflow
- duplicate-message and group-message skip logs

These signals answer the most important production questions: which bot handled the message, which workflow ran, how long it took, which tool was used, and where a failure happened.

## Cost and Latency Controls

Use deterministic paths before LLM calls whenever possible:

- Fast path for common order and refund follow-ups.
- RAG before generation for policy questions.
- One-way Feishu table snapshots for warehouse visibility; keep warehouse data writes in the source system.
- Short memory windows plus compact session state.
- Timeout and fallback replies for slow n8n, tool, or model calls.
- Run logs for latency comparisons between fast path and LLM path.

## CI/CD

GitHub Actions runs service tests, workflow structure tests, and Docker Compose validation on pushes to `master` and pull requests. Keep this green before merging new Agent behavior.
