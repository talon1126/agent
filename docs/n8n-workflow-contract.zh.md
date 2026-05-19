# n8n Workflow Contract

Webhook path: `/webhook/after-sales-event`

必需步骤：

1. 接收事件。
2. 从 `mock-api` 获取 order、customer、shipment 和 inventory。
3. 构建 `EventContext`。
4. POST 到 `ai-service /decide`。
5. 如果 `requires_approval` 为 true，POST 到 `mock-api /approval-requests`。
6. 否则 POST 到 `mock-api /tickets` 或 `/internal-notifications`。
7. 将运行结果 POST 到 `mock-api /run-logs`。
8. 遇到不可恢复错误时，POST 到 `mock-api /dead-letter`。
