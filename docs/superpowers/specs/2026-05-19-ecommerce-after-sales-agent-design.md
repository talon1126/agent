# Ecommerce After-sales Multi-agent Workflow Design

Date: 2026-05-19

## Goal

Build a portfolio-first AI workflow project that demonstrates practical enterprise automation with n8n, AI service development, Docker deployment, and AI operations.

The system handles ecommerce after-sales and inventory coordination events. It should look like a realistic internal business workflow rather than a small prompt demo.

Primary audience:

- Recruiters and hiring managers evaluating AI automation, AI operations, or AI application engineering ability.
- Future maintainers who want to understand how n8n, mock enterprise systems, and model-facing services fit together.

## Confirmed Direction

The first project will be an ecommerce after-sales and inventory coordination system.

Confirmed choices:

- Project goal: portfolio and job-search proof first.
- Orchestration tool: n8n-led.
- Enterprise system integration: mock systems first, with clear replacement points for real SaaS APIs.
- Delivery: Docker-first local demo, followed by optional online deployment documentation.
- AI scope: comprehensive MVP covering routing, knowledge-based recommendations, customer reply drafting, approval, logging, and replay.
- Architecture approach: n8n orchestration plus an independent AI service.

## Recommended Architecture

n8n owns orchestration, workflow branching, retries, integration calls, and approval routing.

The AI service owns model-facing behavior:

- Incident classification.
- Policy-aware recommendations.
- Structured output validation.
- Decision explanation.
- Customer response drafting.
- Retrieval over local policy and product rules.

Mock enterprise APIs represent:

- Orders and customer history.
- Inventory and SKU risk.
- Logistics and delivery status.
- Support tickets and internal notifications.

This boundary keeps the project more engineering-oriented than a pure n8n workflow while still making n8n the visible automation layer.

## System Components

### n8n

n8n is the main workflow engine.

Responsibilities:

- Receive incoming after-sales events through Webhook triggers.
- Fetch related business context from mock APIs.
- Route events into refund, logistics, review, or inventory workflows.
- Call the AI service for classification, policy reasoning, and drafting.
- Handle retries and failure paths.
- Create approval requests for high-risk actions.
- Write run metadata and decision summaries to storage.

### AI Service

The AI service should be implemented as a small API service, preferably FastAPI for the first version.

Responsibilities:

- Accept normalized event payloads from n8n.
- Return strict JSON responses.
- Query local policy and product knowledge.
- Generate recommendation, explanation, confidence, and draft response.
- Validate model output before returning it to n8n.
- Expose health and diagnostic endpoints for deployment checks.

### Mock Enterprise APIs

Mock APIs provide deterministic local enterprise context.

Responsibilities:

- Serve order, customer, shipment, inventory, and support data.
- Support scripted success and failure cases.
- Allow the demo to run without external SaaS accounts.
- Make replacement points obvious for Shopify, Zendesk, Slack, ERP, logistics, or spreadsheet systems.

### Storage

The first version can use SQLite or Postgres through Docker Compose.

Stored data:

- Incoming event payloads.
- AI decisions and explanations.
- Approval status.
- Workflow run history.
- Failure records and replay metadata.

SQLite is simpler for a first local demo. Postgres is more portfolio-friendly if the implementation cost stays low.

## Agent Responsibilities

The system should use small, auditable AI capability modules rather than vague autonomous agents.

### Triage Agent

Input:

- Event payload.
- Customer and order context.

Output:

- Event category.
- Priority.
- Business impact.
- Target team.
- Routing reason.

### Policy Agent

Input:

- Event category.
- Order, customer, and product context.
- Relevant policy documents or rules.

Output:

- Allowed actions.
- Disallowed actions.
- Required approvals.
- Policy citations or policy reason summaries.

### Inventory Risk Agent

Input:

- SKU.
- Current inventory.
- Pending orders.
- Reorder threshold.

Output:

- Risk level.
- Suggested replenishment or substitution action.
- Target internal team.
- Explanation.

### Response Draft Agent

Input:

- Event context.
- Policy result.
- Recommended action.
- Customer tier and tone constraints.

Output:

- Customer-facing reply draft.
- Internal task summary.
- Escalation note when needed.

## Workflow Paths

The MVP should include four primary workflow paths.

### Refund Request

Flow:

1. Receive refund request.
2. Fetch order and customer data.
3. Classify request and priority.
4. Check refund policy.
5. Generate recommendation and customer reply draft.
6. Require approval for high-value, VIP, policy-edge, or low-confidence cases.
7. Create support task and write audit record.

### Logistics Delay

Flow:

1. Receive delivery delay or customer complaint.
2. Fetch shipment and tracking data.
3. Classify severity.
4. Check logistics compensation policy.
5. Draft apology and proposed compensation.
6. Create support task or approval request.

### Bad Review

Flow:

1. Receive public review or customer complaint.
2. Fetch customer and order context.
3. Classify brand risk and urgency.
4. Check policy and recovery options.
5. Draft response and internal escalation summary.
6. Require approval for VIP, public, or high-risk cases.

### Low Stock Risk

Flow:

1. Trigger from scheduled inventory check or webhook.
2. Fetch SKU, sales velocity, pending orders, and inventory threshold.
3. Classify stockout risk.
4. Recommend reorder, substitution, or operational alert.
5. Notify procurement or operations.

## Human Approval Rules

The system must stop for manual review before finalizing high-risk recommendations.

Approval is required when:

- Refund value exceeds a configured threshold.
- Customer is VIP or enterprise-tier.
- AI confidence is below threshold.
- Response is public-facing and brand-sensitive.
- Policy result is ambiguous.
- The recommended action touches financial compensation.

Approval records must include:

- Event ID.
- AI recommendation.
- Explanation.
- Reviewer decision.
- Final action.
- Timestamp.

## Data Model

### Event Payload

Fields:

- `event_id`
- `event_type`
- `source`
- `customer`
- `order`
- `sku`
- `shipment`
- `message`
- `created_at`

Supported `event_type` values:

- `refund_request`
- `logistics_delay`
- `bad_review`
- `low_stock`

### AI Decision Output

Fields:

- `event_id`
- `category`
- `priority`
- `recommended_action`
- `requires_approval`
- `confidence`
- `explanation`
- `draft_response`
- `internal_task_summary`
- `policy_references`

The AI service must validate this shape before returning to n8n.

## Mock API Scope

### Orders API

Endpoints:

- `GET /orders/{id}`
- `GET /customers/{id}`
- `POST /refund-cases`

### Inventory API

Endpoints:

- `GET /inventory/{sku}`
- `GET /substitutes/{sku}`
- `POST /reorder-alerts`

### Logistics API

Endpoints:

- `GET /shipments/{id}`
- `GET /tracking/{id}`
- `POST /delivery-cases`

### Support API

Endpoints:

- `POST /tickets`
- `POST /approval-requests`
- `POST /internal-notifications`

## Demo Events

The repository should include 6-8 scripted demo events.

Required coverage:

- Normal refund request.
- High-value refund requiring approval.
- Logistics delay with compensation suggestion.
- Public bad review requiring brand-risk approval.
- Low stock risk triggering procurement alert.
- Low-confidence AI decision requiring review.
- Mock API failure with retry and dead-letter path.
- Replay of a failed event.

These events should also serve as automated test fixtures.

## AI Ops Requirements

The project should visibly demonstrate operational thinking.

Required features:

- Run logs with event ID, workflow ID, latency, model name, estimated token use, outcome, and error state.
- Retry path for transient mock API failures.
- Dead-letter path for unrecoverable workflow failures.
- Replay path for failed events.
- Schema validation for AI output.
- Confidence threshold guardrail.
- Risky-action guardrail for refunds, VIP customers, and public replies.
- Approval audit trail.

Optional later features:

- Simple dashboard for run history.
- Cost and latency summary.
- Cloud deployment guide.
- Alert integration through Slack, Discord, or email.

## Delivery Plan

### Stage 1: Local Docker Demo

The first complete version should run through Docker Compose.

Services:

- n8n.
- AI service.
- Mock API service.
- Storage service.

Expected local demo:

1. Start services.
2. Import n8n workflow.
3. Send scripted demo events.
4. Observe workflow routing and AI decisions.
5. Approve a high-risk case.
6. Inspect run logs and replay a failed case.

### Stage 2: Portfolio Polish

Deliverables:

- English README.
- Architecture diagram.
- n8n workflow screenshots.
- Demo event examples.
- API contract examples.
- Local setup guide.
- Test guide.
- Demo video script.

### Stage 3: Optional Online Deployment

After the local demo is stable, add a deployment guide for a small VM or platform service.

The public deployment should not be required for the first portfolio version to be useful.

## Testing Strategy

Test coverage should focus on behavior that proves the system is reliable.

AI service tests:

- Output schema validation.
- Event classification behavior with fixture data.
- Approval threshold behavior.
- Guardrails for risky actions.
- Policy lookup fallback behavior.

Mock API tests:

- Expected fixture responses.
- Failure simulation endpoints.

Workflow verification:

- Importable n8n workflow export.
- Scripted events hit the expected workflow paths.
- Failure path creates a retry or dead-letter record.
- Approval path records the expected audit fields.

## Repository Shape

Planned structure:

```text
.
|-- docker-compose.yml
|-- README.md
|-- docs/
|   |-- architecture.md
|   |-- demo-script.md
|   `-- superpowers/specs/
|-- n8n/
|   `-- workflows/
|-- services/
|   |-- ai-service/
|   `-- mock-api/
|-- fixtures/
|   |-- events/
|   `-- policies/
`-- tests/
```

## Open Decisions for Implementation Planning

These can be decided during implementation planning:

- FastAPI versus Node for the mock API service.
- SQLite versus Postgres for first storage.
- Whether to use a local LLM fallback or require an OpenAI-compatible API key.
- Whether approval is simulated through mock API only or also through n8n forms.

Default recommendation:

- FastAPI for AI service.
- FastAPI for mock API unless there is a reason to split stacks.
- Postgres if setup remains simple through Docker Compose; otherwise SQLite for first pass.
- OpenAI-compatible API key through environment variables, with deterministic fake mode for tests.
