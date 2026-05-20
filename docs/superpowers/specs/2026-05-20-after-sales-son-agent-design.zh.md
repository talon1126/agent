# 售后 Son Agent 设计

## 目标

在现有 n8n chat gateway 中新增一个专门的售后 son agent，让飞书消息先由 parent agent 判断任务类型，再分发给专业 agent 调用后端 API，并把结果返回到飞书。

## 当前流程

`feishu-adapter` 在 `/feishu/events` 接收飞书事件，将消息归一化后转发到 n8n `/webhook/chat-agent-inbound`，再把 n8n 返回的 `reply` 发回飞书。

当前 active n8n workflow 是 `Wechat Gateway to Qwen Agent`，id 是 `wechat-qwen-agent-template`。它已经包含 parent agent、天气 son agent、天气工具和 echo 测试工具。

## 目标流程

```text
Feishu -> feishu-adapter -> n8n chat webhook -> Parent Agent
Parent Agent -> after_sales_agent -> order_status_tool -> mock-api
Parent Agent -> Format Webhook Reply -> feishu-adapter -> Feishu
```

## Parent Agent Prompt

parent agent 只负责识别任务类型和分发，不编造业务数据。路由规则：

- 天气、温度、下雨、预报、是否带伞问题交给 `weather_agent`。
- 订单状态、物流、发货、退款、退货、换货、售后、投诉、物流延迟问题交给 `after_sales_agent`。
- 明确的测试、回显、链路验证请求交给 `echo_task_tool`。
- 普通闲聊可以简短回复。

## 售后 Son Agent

`after_sales_agent` 处理电商售后请求。本阶段支持订单和物流状态查询。用户提供 `ord_100` 这类订单号时必须调用 `order_status_tool`；缺少订单号时必须要求用户补充订单号。

回复使用中文，并包含：

- 订单号
- 订单状态
- 物流商
- 物流状态
- 延迟天数
- 行动建议

## 工具

`order_status_tool` 是 n8n LangChain code tool。它接收自然语言或 JSON，提取 `ord_*` 订单号，然后调用：

- `http://mock-api:8000/orders/{order_id}`
- `http://mock-api:8000/shipments/{shipment_id}`

工具向 son agent 返回 JSON。缺少订单号或后端调用失败时，返回结构化错误，不编造数据。

## 验证

- workflow JSON 必须能解析。
- workflow 必须包含 parent agent 对 `after_sales_agent` 的路由 prompt。
- workflow 必须包含 `after_sales_agent` 节点，并作为 tool 连接到 parent agent。
- workflow 必须包含 `order_status_tool` 节点，并作为 tool 连接到 `after_sales_agent`。
- 将 workflow 导入并发布到当前 n8n 容器。
- 用 `帮我查一下订单 ord_100` 做 webhook smoke test，返回内容应包含 `ord_100`。
