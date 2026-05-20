# 本地运行手册

## 启动

```powershell
docker compose up --build -d
```

## 健康检查

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8010/health
Invoke-RestMethod http://localhost:8002/orders/ord_100
```

## n8n

打开 `http://localhost:5678`。

Compose 文件使用 `docker.n8n.io/n8nio/n8n:stable`，跟随 n8n 的稳定 Docker 发布通道。当前已验证的容器版本是 `2.20.11`。

将 `n8n/workflows/ecommerce-after-sales.json` 导入 n8n。该 workflow 暴露的 webhook 路径是：

```text
POST /webhook/after-sales-event
```

如果使用 Docker 容器中的 CLI 导入：

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/ecommerce-after-sales.json
docker compose exec -T n8n n8n publish:workflow --id=wf_ecommerce_after_sales
docker compose exec -T n8n n8n update:workflow --id=wf_ecommerce_after_sales --active=true
docker compose restart n8n
```

如果还要导入 message-agent workflow：

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/message-agent.json
docker compose exec -T n8n n8n publish:workflow --id=wf_message_agent
docker compose restart n8n
```

导入飞书使用的 parent/son chat gateway workflow：

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/chat-parent-son-agent.json
docker compose exec -T n8n n8n publish:workflow --id=wechat-qwen-agent-template
docker compose restart n8n
```

## 发送 Demo 事件

```powershell
./scripts/send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

如果本机 PowerShell 禁用了脚本执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

## 发送 Demo 消息

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -MessageFile fixtures\messages\order_status_text.json
```

如果想绕过 n8n，直接调用 AI service：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -Url http://localhost:8001/message/handle -MessageFile fixtures\messages\order_status_text.json
```

带 transcript 的音频形态消息可以这样测试：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -Url http://localhost:8001/message/handle -MessageFile fixtures\messages\order_status_audio_transcript.json
```

## 飞书 Adapter

默认真实飞书接入使用长连接模式：

```text
FEISHU_EVENT_MODE=long_connection
```

启动或重建 adapter：

```powershell
docker compose up -d --build feishu-adapter
docker compose logs -f feishu-adapter
```

预期启动日志：

```text
started feishu long connection listener
connected to wss://msg-frontier.feishu.cn/ws/v2...
```

真实机器人消息到达时，adapter 会依次输出 `received feishu long connection event`、`forwarded ... to n8n`、`replied to feishu`。adapter 会按 `message_id` 对重复推送做去重。

本地模拟端点是：

```text
POST http://localhost:8010/feishu/events
```

验证飞书 URL challenge：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"type":"url_verification","challenge":"challenge-code"}' http://localhost:8010/feishu/events
```

HTTP callback 模式仍保留，用于本地模拟或备用 callback 部署。当 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 存在时，adapter 会把消息转发到 `N8N_CHAT_WEBHOOK_URL`，再把 agent 回复发送回飞书。

如果长连接已经 `connected`，但发送机器人消息后没有 `received feishu long connection event`，需要检查飞书应用是否订阅 `im.message.receive_v1`、应用版本是否已发布、机器人是否安装到目标会话。

adapter 会先返回 `accepted=true`，再在后台把消息转发到 n8n，agent 完成后才尝试回复飞书。如果日志里有 `received feishu event` 但没有 `forwarded ... to n8n`，检查 `N8N_CHAT_WEBHOOK_URL` 和 n8n workflow 是否可用。如果日志里有 `forwarded ... has_reply=True`，随后出现 `failed to reply to feishu`，说明 workflow 已经运行，但飞书回复 API 拒绝了回包。需要检查应用权限，以及 message ID 是否来自真实飞书回调。

本地模拟飞书事件：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"schema":"2.0","header":{"event_id":"evt_local_probe","event_type":"im.message.receive_v1","token":"verify-token"},"event":{"sender":{"sender_id":{"open_id":"ou_local_probe"}},"message":{"message_id":"om_local_probe","chat_id":"oc_local_probe","message_type":"text","content":"{\"text\":\"帮我查一下订单 ord_100\"}"}}}' http://localhost:8010/feishu/events
```

直接通过 n8n 测试 parent/son 售后路由：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"platform":"feishu","message_type":"text","sender_id":"local","chat_id":"local","message_id":"local_ord_100","text":"帮我查一下订单 ord_100"}' http://localhost:5678/webhook/chat-agent-inbound
```

预期本地结果：回复中包含订单状态 `delivered`、物流商 `UPS`、物流状态 `delivered`，并说明没有延迟。响应里的 `raw_agent_output.intermediateSteps` 应显示 `AI Agent` 调用了 `after_sales_agent`，son agent 的 observation 中应显示调用了 `order_status_tool`。

## Replay 失败事件

```powershell
./scripts/replay_failed_event.ps1 -EventId evt_mock_api_failure
```

## 说明

n8n 容器可能会记录 Python task runner warning。本项目不在 n8n 中使用 Python Code node，所以该 warning 不阻塞 demo workflow。
