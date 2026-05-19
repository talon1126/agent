# Demo 脚本

这份脚本适合用于作品集面试讲解或录屏，时长控制在 3-5 分钟。

## 1. 业务问题

“这个项目模拟一个企业内部电商售后 workflow。在真实公司里，退款请求、物流延误、公开差评和低库存事件通常会跨多个系统：电商后端、客服系统、物流服务商、库存系统、审批流程和团队通知。这个项目的目标是用 AI-assisted workflow 对事件分类、获取上下文、决定下一步动作，并保留运维记录。”

## 2. 架构

“系统有三个主要应用层。n8n 是 workflow orchestrator。`ai-service` 是面向模型的边界，负责返回结构化决策。`mock-api` 模拟企业系统，包括订单、客户、物流、库存、审批、工单、通知、运行日志、dead letter 和 replay。”

展示：

- `docker-compose.yml`
- `n8n/workflows/ecommerce-after-sales.json`
- `services/ai-service/app/schemas.py`
- `services/mock-api/app/main.py`

## 3. 启动服务

```powershell
docker compose up --build -d
```

然后打开 n8n：

```text
http://localhost:5678
```

检查服务：

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
```

## 4. 触发退款审批事件

运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

讲解：

“这个事件代表一个高价值退款请求。n8n 会获取订单、客户、物流和库存上下文，把上下文发送给 `ai-service`，然后根据决策执行动作。因为订单金额较高，所以这个决策需要审批。”

## 5. 展示 AI Decision JSON

重点展示这些字段：

- `category`: `refund_request`
- `priority`: `high`
- `recommended_action`: `review_refund_request`
- `requires_approval`: `true`
- `confidence`: deterministic demo confidence
- `policy_references`: refund policy guardrail

讲解：

“AI 层不是只返回自由文本，而是返回结构化 decision，这样 workflow 才能安全地路由到不同业务动作。”

## 6. 展示审批和运行日志

运行：

```powershell
Invoke-RestMethod http://localhost:8002/approval-requests | ConvertTo-Json -Depth 10
Invoke-RestMethod http://localhost:8002/run-logs | ConvertTo-Json -Depth 10
```

讲解：

“approval request 是业务动作。run log 是 AI Ops 记录，包含 event id、workflow id、status、latency、model、token estimate 和 error 字段。”

## 7. 解释失败 Replay

运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\replay_failed_event.ps1 -EventId evt_mock_api_failure
```

预期输出：

```json
{
  "event_id": "evt_mock_api_failure",
  "status": "queued_for_replay"
}
```

讲解：

“生产级 AI workflow 需要恢复路径。这个 demo 提供 replay endpoint，让失败事件可以重新排队和重试，而不是静默丢失。”

## 8. 映射到真实 SaaS 系统

收尾：

“这些 mock systems 可以替换成真实 SaaS API。订单和库存可以来自 Shopify 或内部电商后端。客服工单可以进入 Zendesk。通知可以发到 Slack 或 Teams。采购提醒可以接 ERP。物流状态可以接物流服务商。AI service 可以接 OpenAI、Azure OpenAI、Anthropic 或内部模型网关，而不需要重写 n8n 编排。”
