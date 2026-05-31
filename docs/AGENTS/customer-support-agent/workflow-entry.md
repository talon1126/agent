# workflow 入口

- Feishu 客服机器人消息进入 `feishu-adapter`，再转发到 n8n `Customer Support Workflow`。
- n8n webhook：`POST /webhook/customer-support-inbound`。
- workflow 文件：`n8n/workflows/customer-support-workflow.json`。
- Feishu bot 名称通常是 `customer_support`。
- 当前主要工具：
  - `order_status_tool`：查询订单和物流状态。用户提供 `ord_` 订单号时必须调用。
  - `policy_search_tool`：检索售后政策。涉及退款、退货、换货、审批、补偿、物流赔偿、差评处理时必须调用。
