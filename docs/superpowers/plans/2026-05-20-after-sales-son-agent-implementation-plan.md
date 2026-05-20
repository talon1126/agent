# After-sales Son Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an after-sales son agent to the n8n chat gateway and connect it to a backend API tool for order and shipment status.

**Architecture:** Keep Feishu protocol handling in `feishu-adapter`. Keep orchestration in n8n. Add the after-sales son agent and tool inside the existing `Wechat Gateway to Qwen Agent` workflow so the parent agent dispatches business requests to a specialist.

**Tech Stack:** n8n workflow JSON, LangChain agent/tool nodes, mock-api FastAPI endpoints, Docker Compose.

---

### Task 1: Version The Workflow

**Files:**
- Create: `n8n/workflows/chat-parent-son-agent.json`
- Create: `tests/test_chat_parent_son_workflow.py`

- [ ] Export the active n8n workflow `wechat-qwen-agent-template`.
- [ ] Save a local workflow JSON file with stable id `wechat-qwen-agent-template`.
- [ ] Add a JSON test that asserts the workflow has `after_sales_agent`, `order_status_tool`, and parent prompt routing.

### Task 2: Add After-sales Son Agent

**Files:**
- Modify: `n8n/workflows/chat-parent-son-agent.json`
- Test: `tests/test_chat_parent_son_workflow.py`

- [ ] Add `after_sales_agent` as `@n8n/n8n-nodes-langchain.agentTool`.
- [ ] Connect a Qwen chat model node to `after_sales_agent`.
- [ ] Update the parent prompt to route order, logistics, refund, return, exchange, complaint, and shipping-delay tasks to `after_sales_agent`.

### Task 3: Add Backend Tool

**Files:**
- Modify: `n8n/workflows/chat-parent-son-agent.json`
- Test: `tests/test_chat_parent_son_workflow.py`

- [ ] Add `order_status_tool` as `@n8n/n8n-nodes-langchain.toolCode`.
- [ ] The tool extracts `ord_*`, calls `mock-api /orders/{order_id}`, then calls `mock-api /shipments/{shipment_id}`.
- [ ] Connect `order_status_tool` as an AI tool to `after_sales_agent`.

### Task 4: Publish And Verify

**Files:**
- Modify: `README.md`
- Modify: `README.zh.md`
- Modify: `docs/local-runbook.md`
- Modify: `docs/local-runbook.zh.md`

- [ ] Import `chat-parent-son-agent.json` into n8n.
- [ ] Publish workflow `wechat-qwen-agent-template`.
- [ ] Run workflow structure tests.
- [ ] Run service tests.
- [ ] Smoke test `/webhook/chat-agent-inbound` with `帮我查一下订单 ord_100`.
- [ ] Document the Feishu-to-after-sales flow in English and Chinese.
