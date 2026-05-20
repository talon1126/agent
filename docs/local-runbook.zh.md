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

adapter 端点是：

```text
POST http://localhost:8010/feishu/events
```

验证飞书 URL challenge：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"type":"url_verification","challenge":"challenge-code"}' http://localhost:8010/feishu/events
```

真实接入飞书事件订阅时，需要通过公网 HTTPS 暴露该端点，并在飞书开发者后台配置公网 URL。当 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 存在时，adapter 会把消息转发到 `N8N_CHAT_WEBHOOK_URL`，再把 agent 回复发送回飞书。

直接通过 n8n 测试 parent/son 售后路由：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"platform":"feishu","message_type":"text","sender_id":"local","chat_id":"local","message_id":"local_ord_100","text":"帮我查一下订单 ord_100"}' http://localhost:5678/webhook/chat-agent-inbound
```

预期本地结果：回复中包含 `订单 ord_100 查询成功。`、订单状态 `delivered`、物流商 `UPS`、物流状态 `delivered` 和 `延迟天数：0`。这条 `ord_*` 路径是确定性分支，会在 LLM fallback 前直接调用 `mock-api`，方便稳定验证飞书链路。

## Replay 失败事件

```powershell
./scripts/replay_failed_event.ps1 -EventId evt_mock_api_failure
```

## 说明

n8n 容器可能会记录 Python task runner warning。本项目不在 n8n 中使用 Python Code node，所以该 warning 不阻塞 demo workflow。
