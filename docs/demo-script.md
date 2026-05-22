# Demo Script

Use this 5-7 minute walkthrough for portfolio interviews or screen recordings. The demo positions the project as an **Internal Ecommerce Operations Copilot** for employees, not an external customer bot.

## 1. Opening Story

"This project is a Docker-first internal ecommerce operations copilot. Employees talk to department Feishu bots. One Feishu Gateway Adapter normalizes messages and routes each bot to its own n8n workflow. n8n orchestrates the department process, calls AI and backend tools, and returns the reply to Feishu. The backend services are normal FastAPI services, so the AI logic and enterprise APIs are testable without n8n."

Show:

- `AGENTS.md`
- `docker-compose.yml`
- `n8n/workflows/customer-support-workflow.json`
- `services/feishu-adapter/app/main.py`
- `services/mock-api/app/main.py`

## 2. Start and Inspect the Stack

```powershell
docker compose up --build -d
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8010/health/details | ConvertTo-Json -Depth 10
```

Explain:

"The detailed adapter health endpoint intentionally reports bot names, webhook configuration, listener count, and run-log status without exposing app secrets. This is the kind of operational visibility I would need before trusting a multi-bot Agent system."

## 3. Customer Support Demo

Send to the Customer Support bot:

```text
帮我查询订单 ord_100
这个订单怎么退款？
```

Expected behavior:

- The first message uses order status context.
- The follow-up can use memory/session state to understand "this order".
- Refund answers should cite policy metadata such as `fixtures/policies/after_sales_policy.zh.md`, `section=退款`, and a `clause_id` such as `REFUND-001`.

Explain:

"This shows short-term conversation memory plus auditable policy RAG. The Agent should not invent refund rules; it must point to policy metadata."

## 4. Warehouse Demo

Send to the Warehouse bot or mention it in a shared group:

```text
@Warehouse 查询 sku_bag_1 的库存、库位和履约风险
```

Expected behavior:

- Only the Warehouse workflow runs.
- The warehouse tools return inventory, locations, open exceptions, and risk.
- Other department workflows do not execute.

Explain:

"This project hit a real multi-bot failure mode: in a shared Feishu group, one message can be received by every bot. The gateway now filters group messages by mention and bot open_id so one message cannot fan out to every workflow."

## 5. Procurement Demo

Send to the Procurement bot:

```text
SKU sku_bag_1 是否需要补货？给出采购建议
```

Expected behavior:

- Procurement workflow calls the procurement tool.
- The reply explains whether a purchase request is needed based on inventory and pending orders.

Explain:

"Procurement is separated from Warehouse. Warehouse owns stock and fulfillment risk; Procurement owns replenishment recommendation. This separation is closer to real enterprise ownership."

## 6. Operations Demo

Send to the Operations bot:

```text
生成今天的运营日报，包含订单、库存、采购和异常摘要
```

Expected behavior:

- Operations workflow calls the operations summary tool.
- The reply produces a cross-domain summary and next actions.

Explain:

"Operations is a cross-department reporting workflow, but it still has its own bot and workflow. I avoided a single global Parent Agent because internal departments usually need clear permissions, ownership, and audit boundaries."

## 7. AI Ops Evidence

Show:

```powershell
Invoke-RestMethod http://localhost:8002/run-logs | ConvertTo-Json -Depth 10
Invoke-RestMethod http://localhost:8002/dead-letter | ConvertTo-Json -Depth 10
```

Explain:

"Each handled Feishu message can write a structured run log with message id, bot name, workflow URL, latency, status, error, and tool calls when the workflow returns them. This makes the project inspectable as a running system, not just a chat demo."

## 8. Close

"The important part is not that I used n8n or Qwen. The important part is the architecture: protocol gateway, workflow orchestration, testable AI service, replaceable enterprise APIs, memory boundaries, RAG citations, run logs, failure recovery, and CI. This is how I would build an Agent workflow that a company can operate and debug."
