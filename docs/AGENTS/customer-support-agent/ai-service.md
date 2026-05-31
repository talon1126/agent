# ai-service

`ai-service` 承担客服/售后中需要可测试的确定性逻辑，不直接编排 Feishu 或 n8n。

- `POST /message/handle`
  - 用途：消息处理入口，支持订单号提取、文本/音频转写边界和订单状态工具调用。

- `POST /after-sales/fast-path`
  - 用途：客服售后快路径。可处理明确订单查询，以及在同一 session 已记住 `last_order_id` 时处理退款类追问；不能处理时应 decline，让 workflow 回退到 Agent。

- `POST /decide`
  - 用途：售后事件确定性决策，用于非聊天事件流。

`session_state` 用于保存短期、可恢复的会话状态，例如 `last_order_id`；`user_profile` 用于保存精简用户级事实，不应塞入完整聊天记录。客服 workflow 的 n8n Postgres Chat Memory 继续用于上下文问答，session key 应保持 `customer_support:<session_id>` 这类部门命名空间，避免和其他 agent 混用。
