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

To import the parent/son chat gateway workflow used by Feishu:

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/chat-parent-son-agent.json
docker compose exec -T n8n n8n publish:workflow --id=wechat-qwen-agent-template
docker compose restart n8n
```

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

The adapter endpoint is:

```text
POST http://localhost:8010/feishu/events
```

Verify Feishu URL challenge handling:

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"type":"url_verification","challenge":"challenge-code"}' http://localhost:8010/feishu/events
```

For a real Feishu subscription, expose this endpoint through public HTTPS and configure the public URL in Feishu Developer Console. When `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are present, the adapter forwards messages to `N8N_CHAT_WEBHOOK_URL` and posts the agent reply back to Feishu.

Watch adapter callbacks while sending a real Feishu bot message:

```powershell
docker compose logs -f feishu-adapter
```

If no new `POST /feishu/events` appears after sending a Feishu message, the request is not reaching Docker. Check that the Feishu Developer Console event subscription request URL is a public HTTPS URL that forwards to `http://localhost:8010/feishu/events`, that the app subscribes to `im.message.receive_v1`, and that the bot is installed in the target chat.

The adapter returns `accepted=true` before the agent finishes, then forwards the message to n8n in the background. If the logs show `received feishu event` but not `forwarded ... to n8n`, check `N8N_CHAT_WEBHOOK_URL` and n8n workflow availability. If the logs show `forwarded ... has_reply=True` followed by `failed to reply to feishu`, the workflow ran but Feishu rejected the reply API call. Check app permissions and whether the message ID is from a real Feishu callback.

Simulate the Feishu event locally:

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"schema":"2.0","header":{"event_id":"evt_local_probe","event_type":"im.message.receive_v1","token":"verify-token"},"event":{"sender":{"sender_id":{"open_id":"ou_local_probe"}},"message":{"message_id":"om_local_probe","chat_id":"oc_local_probe","message_type":"text","content":"{\"text\":\"帮我查一下订单 ord_100\"}"}}}' http://localhost:8010/feishu/events
```

Test the parent/son after-sales route through n8n directly:

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"platform":"feishu","message_type":"text","sender_id":"local","chat_id":"local","message_id":"local_ord_100","text":"帮我查一下订单 ord_100"}' http://localhost:5678/webhook/chat-agent-inbound
```

Expected local result: the reply includes order status `delivered`, carrier `UPS`, shipment status `delivered`, and no delay. The response `raw_agent_output.intermediateSteps` should show `AI Agent` calling `after_sales_agent`, and the son agent observation should show `order_status_tool` being called.

## Replay Failed Event

```powershell
./scripts/replay_failed_event.ps1 -EventId evt_mock_api_failure
```

## Notes

n8n may log a Python task runner warning in this container. This project does not use Python Code nodes in n8n, so the warning does not block the demo workflow.
