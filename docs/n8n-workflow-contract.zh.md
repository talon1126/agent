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

## Message Agent Workflow

Webhook path：`/webhook/message-agent`

必需步骤：

1. 接收文本或音频形态的 message payload。
2. 将标准化 payload POST 到 `ai-service /message/handle`。
3. 将结果元数据写入 `mock-api /run-logs`。
4. 返回 `answer`、`intent`、`tool_calls`、`requires_human` 和可选的 `transcription`。

音频支持当前通过 provider 配置控制。`TRANSCRIPTION_PROVIDER=mock` 是确定性 demo 模式。`TRANSCRIPTION_PROVIDER=qwen` 需要 `QWEN_API_ENDPOINT`、`QWEN_API_KEY`、确认后的模型名，以及供应商响应示例后，才会启用真实网络调用。
