# 业务数据库表

客服相关持久化应优先通过 `ai-service`、`mock-api` 或未来专用 customer/customer-service API 访问，不要让 n8n 或 `feishu-adapter` 直接读写 Postgres 表。

- `session_state`
  - 短期可恢复状态表。
  - 客服重点字段：`session_id`、`source`、`chat_id`、`sender_id`、`state`、`updated_at`。
  - 典型状态：`state.last_order_id`。

- `user_profile`
  - 用户级精简事实表。
  - 客服只保存可复用的结构化事实和偏好，不保存完整聊天 transcript。

- `users`
  - 用户账号表，当前已在部署中的 Postgres 创建。
  - 关键字段：`id`、`phone_number`、`email`、`username`、`password`。
  - 当前 `password` 类型为 `varchar(20)`，只适合 demo/临时数据；如果后续做真实登录，需要重新设计密码 hash 字段和长度。

- `cart_items`
  - 购物车明细表，当前已在部署中的 Postgres 创建。
  - 关键字段：`id`、`item_id`、`item_name`、`user_id`、`price`、`quantity`。
  - 当前只使用逻辑外键：`user_id` 逻辑关联 `users.id`，`item_id` 逻辑关联 `items.item_id`；没有物理外键。
  - 约束：`price >= 0`，`quantity > 0`，`quantity` 默认值为 `1`。
  - 价格来源：`POST /cart` 写入时以后端 `items.price` 为准，前端传入价格只做非负校验。
