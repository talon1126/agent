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
