# Local Runbook

## Start

```powershell
docker compose up --build -d
```

## Health Checks

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8010/health
Invoke-RestMethod http://localhost:8002/orders/ord_100
```

## n8n

Open `http://localhost:5678`.

The Compose file uses `docker.n8n.io/n8nio/n8n:stable`, which follows n8n's stable Docker release channel. The currently verified container version is `2.20.11`.

Import `n8n/workflows/ecommerce-after-sales.json` into n8n. The workflow exposes this webhook path:

```text
POST /webhook/after-sales-event
```

For CLI import in the Docker container:

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/ecommerce-after-sales.json
docker compose exec -T n8n n8n publish:workflow --id=wf_ecommerce_after_sales
docker compose exec -T n8n n8n update:workflow --id=wf_ecommerce_after_sales --active=true
docker compose restart n8n
```

To also import the message-agent workflow:

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/message-agent.json
docker compose exec -T n8n n8n publish:workflow --id=wf_message_agent
docker compose restart n8n
```

To import the recommended department chat workflows used by the Feishu Gateway Adapter:

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/customer-support-workflow.json
docker compose exec -T n8n n8n import:workflow --input=/workflows/warehouse-workflow.json
docker compose exec -T n8n n8n import:workflow --input=/workflows/procurement-workflow.json
docker compose exec -T n8n n8n import:workflow --input=/workflows/operations-workflow.json
docker compose exec -T n8n n8n publish:workflow --id=customer-support-workflow
docker compose exec -T n8n n8n publish:workflow --id=warehouse-workflow
docker compose exec -T n8n n8n publish:workflow --id=procurement-workflow
docker compose exec -T n8n n8n publish:workflow --id=operations-workflow
docker compose restart n8n
```

`n8n/workflows/chat-parent-son-agent.json` remains available as a legacy compatibility workflow, but new internal chat integrations should use the four department workflows.

## Send Demo Event

```powershell
./scripts/send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

If local PowerShell script execution is disabled:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

## Send Demo Message

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -MessageFile fixtures\messages\order_status_text.json
```

For a direct AI service call without n8n:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -Url http://localhost:8001/message/handle -MessageFile fixtures\messages\order_status_text.json
```

Audio-shaped messages can be tested with a transcript:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -Url http://localhost:8001/message/handle -MessageFile fixtures\messages\order_status_audio_transcript.json
```

## Feishu Adapter

The default real Feishu integration uses long connection mode:

```text
FEISHU_EVENT_MODE=long_connection
```

For multiple department bots, configure one gateway adapter with `FEISHU_BOTS_JSON`:

```text
FEISHU_BOTS_JSON=[{"name":"customer_support","app_id":"cli_customer","app_secret":"secret_customer","bot_open_id":"ou_customer_bot","n8n_webhook_url":"http://n8n:5678/webhook/customer-support-inbound"},{"name":"warehouse","app_id":"cli_warehouse","app_secret":"secret_warehouse","bot_open_id":"ou_warehouse_bot","n8n_webhook_url":"http://n8n:5678/webhook/warehouse-inbound"},{"name":"procurement","app_id":"cli_procurement","app_secret":"secret_procurement","bot_open_id":"ou_procurement_bot","n8n_webhook_url":"http://n8n:5678/webhook/procurement-inbound"},{"name":"operations","app_id":"cli_operations","app_secret":"secret_operations","bot_open_id":"ou_operations_bot","n8n_webhook_url":"http://n8n:5678/webhook/operations-inbound"}]
```

Leave `FEISHU_BOTS_JSON` empty to use the legacy single-bot fallback with `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, and `N8N_CHAT_WEBHOOK_URL`.

If multiple department bots are installed in the same Feishu group, send messages by mentioning the target bot. The adapter uses `bot_open_id` and the event `mentions` list to prevent one group message from triggering every department workflow. Unmentioned group messages are ignored in multi-bot mode; direct bot chats continue to work without mentions.

Start or rebuild the adapter:

```powershell
docker compose up -d --build feishu-adapter
docker compose logs -f feishu-adapter
Invoke-RestMethod http://localhost:8010/health/details | ConvertTo-Json -Depth 10
```

Expected startup log:

```text
started feishu long connection listener bot=<bot_name>
connected to wss://msg-frontier.feishu.cn/ws/v2...
```

When a real bot message arrives, the adapter logs `received feishu long connection event`, then `forwarded ... bot=<bot_name> ... to n8n`, and finally `replied to feishu`. In group chats, skipped messages are logged as `skipping group feishu event ... because bot was not mentioned` or `skipping unmentioned group feishu event ...`. The adapter deduplicates repeated pushes by `bot_name + message_id`.

When `FEISHU_RUN_LOG_URL` is configured, every completed Feishu message writes a structured run log with `message_id`, `bot_name`, `workflow`, `status`, latency fields, `error`, and workflow `tool_calls` if returned.

The local simulation endpoint is:

```text
POST http://localhost:8010/feishu/events
```

Verify Feishu URL challenge handling:

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"type":"url_verification","challenge":"challenge-code"}' http://localhost:8010/feishu/events
```

HTTP callback mode remains available for local simulation or fallback deployments. In callback mode the `/feishu/events` endpoint uses the first configured bot. When `FEISHU_BOTS_JSON` is empty, the adapter forwards messages to `N8N_CHAT_WEBHOOK_URL` and posts the agent reply back to Feishu with the legacy single-bot credentials.

If long connection is connected but no `received feishu long connection event` appears after sending a bot message, check that the Feishu app subscribes to `im.message.receive_v1`, the app version has been published, and the bot is installed in the target chat.

The adapter returns `accepted=true` before the agent finishes, then forwards the message to n8n in the background. If the logs show `received feishu event` but not `forwarded ... to n8n`, check `N8N_CHAT_WEBHOOK_URL` and n8n workflow availability. If the logs show `forwarded ... has_reply=True` followed by `failed to reply to feishu`, the workflow ran but Feishu rejected the reply API call. Check app permissions and whether the message ID is from a real Feishu callback.

Simulate the Feishu event locally:

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"schema":"2.0","header":{"event_id":"evt_local_probe","event_type":"im.message.receive_v1","token":"verify-token"},"event":{"sender":{"sender_id":{"open_id":"ou_local_probe"}},"message":{"message_id":"om_local_probe","chat_id":"oc_local_probe","message_type":"text","content":"{\"text\":\"帮我查一下订单 ord_100\"}"}}}' http://localhost:8010/feishu/events
```

Test department routes through n8n directly:

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"platform":"feishu","message_type":"text","sender_id":"local","chat_id":"local","message_id":"local_ord_100","text":"帮我查一下订单 ord_100"}' http://localhost:5678/webhook/customer-support-inbound
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"platform":"feishu","message_type":"text","sender_id":"local","chat_id":"local","message_id":"local_sku_bag_1","text":"sku_bag_1 今天能发货吗"}' http://localhost:5678/webhook/warehouse-inbound
```

Expected local result: the customer-support reply uses `order_status_tool`, and the warehouse reply uses the warehouse tools. These direct n8n checks may call the configured LLM; use mock-api endpoint checks when avoiding model quota.

## Replay Failed Event

```powershell
./scripts/replay_failed_event.ps1 -EventId evt_mock_api_failure
```

## Notes

n8n may log a Python task runner warning in this container. This project does not use Python Code nodes in n8n, so the warning does not block the demo workflow.
