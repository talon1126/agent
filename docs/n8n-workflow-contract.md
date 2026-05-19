# n8n Workflow Contract

Webhook path: `/webhook/after-sales-event`

Required steps:

1. Receive event.
2. Fetch order, customer, shipment, and inventory from `mock-api`.
3. Build `EventContext`.
4. POST to `ai-service /decide`.
5. If `requires_approval` is true, POST to `mock-api /approval-requests`.
6. Otherwise POST to `mock-api /tickets` or `/internal-notifications`.
7. POST run result to `mock-api /run-logs`.
8. On unrecoverable error, POST to `mock-api /dead-letter`.

## Message Agent Workflow

Webhook path: `/webhook/message-agent`

Required steps:

1. Receive text or audio-shaped message payload.
2. POST the normalized payload to `ai-service /message/handle`.
3. Write result metadata to `mock-api /run-logs`.
4. Return `answer`, `intent`, `tool_calls`, `requires_human`, and optional `transcription`.

Audio support currently uses provider configuration. `TRANSCRIPTION_PROVIDER=mock` is deterministic. `TRANSCRIPTION_PROVIDER=qwen` requires `QWEN_API_ENDPOINT`, `QWEN_API_KEY`, the confirmed model name, and a provider response example before real network calls are enabled.
