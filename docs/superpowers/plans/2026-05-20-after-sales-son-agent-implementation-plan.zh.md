# 售后 Son Agent 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 n8n chat gateway 中新增售后 son agent，并连接到后端 API 工具，用于查询订单和物流状态。

**Architecture:** 飞书协议细节保留在 `feishu-adapter`。编排保留在 n8n。把售后 son agent 和工具加到现有 `Wechat Gateway to Qwen Agent` workflow 中，让 parent agent 把业务请求分发给专业 agent。

**Tech Stack:** n8n workflow JSON、LangChain agent/tool nodes、mock-api FastAPI endpoints、Docker Compose。

---

### Task 1: 版本化 Workflow

**Files:**
- 新建：`n8n/workflows/chat-parent-son-agent.json`
- 新建：`tests/test_chat_parent_son_workflow.py`

- [ ] 导出 active n8n workflow `wechat-qwen-agent-template`。
- [ ] 保存本地 workflow JSON，稳定 id 为 `wechat-qwen-agent-template`。
- [ ] 新增 JSON 测试，断言 workflow 包含 `after_sales_agent`、`order_status_tool` 和 parent prompt 路由。

### Task 2: 新增售后 Son Agent

**Files:**
- 修改：`n8n/workflows/chat-parent-son-agent.json`
- 测试：`tests/test_chat_parent_son_workflow.py`

- [ ] 新增 `after_sales_agent`，类型为 `@n8n/n8n-nodes-langchain.agentTool`。
- [ ] 将 Qwen chat model node 连接到 `after_sales_agent`。
- [ ] 更新 parent prompt，把订单、物流、退款、退货、换货、投诉、物流延迟任务路由到 `after_sales_agent`。

### Task 3: 新增后端工具

**Files:**
- 修改：`n8n/workflows/chat-parent-son-agent.json`
- 测试：`tests/test_chat_parent_son_workflow.py`

- [ ] 新增 `order_status_tool`，类型为 `@n8n/n8n-nodes-langchain.toolCode`。
- [ ] 工具提取 `ord_*`，调用 `mock-api /orders/{order_id}`，再调用 `mock-api /shipments/{shipment_id}`。
- [ ] 将 `order_status_tool` 作为 AI tool 连接到 `after_sales_agent`。

### Task 4: 发布和验证

**Files:**
- 修改：`README.md`
- 修改：`README.zh.md`
- 修改：`docs/local-runbook.md`
- 修改：`docs/local-runbook.zh.md`

- [ ] 将 `chat-parent-son-agent.json` 导入 n8n。
- [ ] 发布 workflow `wechat-qwen-agent-template`。
- [ ] 运行 workflow 结构测试。
- [ ] 运行服务测试。
- [ ] 用 `帮我查一下订单 ord_100` smoke test `/webhook/chat-agent-inbound`。
- [ ] 用中英文文档记录 Feishu 到售后 agent 的流程。
