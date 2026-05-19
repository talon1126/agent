# Message Agent Tooling and Audio Transcription Design

Date: 2026-05-19

## Goal

Extend the ecommerce after-sales workflow so n8n can receive a user message, normalize it, and ask the AI service to handle it as a small agent workflow.

The first version should support text messages immediately and define a clean adapter boundary for audio transcription. Qwen can be connected later by filling in environment variables and the provider-specific request shape without changing the agent contract.

## Scope

This is an incremental extension to the existing project, not a replacement for the current after-sales event workflow.

Included:

- A message handling API in `ai-service`.
- A simple agent tool for checking order and shipment status.
- Mock enterprise API support for the tool call.
- A message workflow contract for n8n.
- Text input support now.
- Audio input contract now, with transcription adapter boundaries for Qwen.
- Bilingual documentation.

Not included in the first implementation:

- Real Qwen API calls without credentials and confirmed request/response examples.
- Full conversational memory.
- Multi-turn chat UI.
- Production authentication.
- Payment, refund execution, or live SaaS integrations.

## Recommended Architecture

n8n remains the orchestration layer.

AI service owns message interpretation, tool selection, response construction, and provider adapter boundaries.

mock-api continues to represent enterprise systems. For this feature it exposes deterministic order and shipment data that the agent can query through a tool.

The first agent tool is intentionally narrow:

- Tool name: `get_order_status`
- Input: `order_id`
- Dependencies: mock order API and mock shipment API
- Output: order status, shipment status, estimated delivery date, and a customer-facing summary

This creates a realistic pattern for later tools such as refund eligibility, inventory substitute lookup, customer profile lookup, or support ticket creation.

## Message Input Contract

The n8n webhook should accept a normalized message payload.

Fields:

- `message_id`: unique message identifier.
- `source`: origin system, such as `webhook`, `wechat`, `whatsapp`, or `internal_test`.
- `message_type`: `text` or `audio`.
- `text`: plain text when available.
- `audio_url`: optional URL for audio input.
- `audio_base64`: optional inline audio payload for local tests.
- `mime_type`: optional audio MIME type.
- `customer_id`: optional customer identifier.
- `order_id`: optional order identifier.
- `created_at`: ISO timestamp.

For the first runnable version, text messages are the primary path. Audio messages can be accepted only when a transcript is already provided or when Qwen credentials and endpoint details are configured later.

## Audio Transcription Boundary

The AI service should expose a provider-neutral transcription function.

Internal shape:

- Input: audio URL or base64 payload, MIME type, language hint, provider name.
- Output: transcript text, provider name, model name, confidence if available, raw provider metadata.

Initial provider modes:

- `mock`: deterministic local transcription for tests and demos.
- `qwen`: reserved adapter that validates required settings and returns a clear configuration error until credentials and exact API shape are provided.

Required Qwen details from the user:

- API endpoint.
- API key environment variable name, recommended default: `QWEN_API_KEY`.
- Model name, initial configurable default: `qwen3.6plus`.
- Audio input format expected by the API: URL, base64, multipart file, or n8n binary data.
- Example response JSON, especially the transcript field path.

## Agent Handling API

New endpoint:

`POST /message/handle`

Responsibilities:

1. Validate the message payload.
2. If message type is text, use the text directly.
3. If message type is audio, call the transcription adapter or use a provided transcript.
4. Detect whether the message asks for order status.
5. If an order ID is available or extractable, call `get_order_status`.
6. Return a structured agent response.
7. Include tool call metadata for auditability.

Response fields:

- `message_id`
- `normalized_text`
- `intent`
- `tool_calls`
- `answer`
- `requires_human`
- `confidence`
- `transcription`
- `error`

## Tool Design

### get_order_status

Input:

- `order_id`

Process:

1. Fetch order from `mock-api /orders/{order_id}`.
2. Use `shipment_id` from the order if present.
3. Fetch shipment from `mock-api /shipments/{shipment_id}`.
4. Build a concise status summary.

Output:

- `order_id`
- `order_status`
- `shipment_id`
- `shipment_status`
- `estimated_delivery`
- `summary`

The implementation should stay deterministic. The agent can use simple rules to identify order status questions and extract order IDs such as `ord_100`.

## n8n Workflow Contract

The message workflow should be separate from the existing after-sales event workflow.

Webhook path:

`/webhook/message-agent`

Required steps:

1. Receive message payload.
2. Normalize text/audio fields.
3. POST to `ai-service /message/handle`.
4. POST a run log to `mock-api /run-logs`.
5. Return the agent answer and tool call metadata to the caller.

Failure handling:

- Validation failures return a clear 4xx response.
- Missing Qwen configuration for audio returns a clear configuration error.
- Tool failures return `requires_human: true` and write a run log.

## Testing Strategy

AI service tests:

- Text message asking for order status triggers `get_order_status`.
- Text message without order ID asks for missing information.
- Audio message with provided transcript follows the same path as text.
- Audio message without configured provider returns a controlled error.
- Tool output schema is stable.

Mock API tests:

- Order and shipment fixture data can support the tool response.

Workflow verification:

- n8n workflow JSON imports successfully.
- Text demo message returns an answer.
- Audio contract demo returns either transcript-based success or configuration error.

## Implementation Notes

Keep this feature small and inspectable:

- Do not introduce a full agent framework yet.
- Do not make network calls to Qwen until credentials and request examples are available.
- Use environment variables for provider configuration.
- Keep mock mode deterministic so tests and demos are reliable.
