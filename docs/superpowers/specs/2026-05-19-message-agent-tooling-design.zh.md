# 消息 Agent 工具调用与音频转写设计

日期：2026-05-19

## 目标

扩展当前电商售后 workflow，让 n8n 可以接收一条用户消息，标准化后交给 AI service，以一个小型 agent workflow 的方式处理。

第一版需要立即支持文本消息，并为音频转文本定义清晰的 adapter 边界。Qwen 可以在后续补齐环境变量和具体请求/响应格式后接入，而不需要改动 agent 的外部契约。

## 范围

这是对现有项目的增量扩展，不替换当前售后事件 workflow。

包含：

- 在 `ai-service` 中新增消息处理 API。
- 设计一个简单的 agent 工具，用于查询订单和物流状态。
- 在 mock enterprise API 中提供该工具需要的数据接口。
- 为 n8n 增加消息 workflow 契约。
- 当前先支持文本输入。
- 当前先定义音频输入契约，并预留 Qwen 转写 adapter。
- 继续提供中英文双语文档。

第一版不包含：

- 在没有凭证和接口示例的情况下直接调用真实 Qwen API。
- 完整对话记忆。
- 多轮聊天 UI。
- 生产级认证。
- 真实付款、退款执行或真实 SaaS 集成。

## 推荐架构

n8n 继续作为编排层。

AI service 负责消息理解、工具选择、回复构造，以及模型或转写供应商的 adapter 边界。

mock-api 继续模拟企业内部系统。本功能中，它提供 agent 查询订单和物流状态所需的确定性数据。

第一个 agent 工具保持很窄：

- 工具名：`get_order_status`
- 输入：`order_id`
- 依赖：mock 订单 API 和 mock 物流 API
- 输出：订单状态、物流状态、预计送达时间，以及面向客户的简短说明

这个模式后续可以自然扩展为更多工具，例如退款资格查询、库存替代品查询、客户画像查询、工单创建等。

## 消息输入契约

n8n webhook 接收标准化消息 payload。

字段：

- `message_id`：唯一消息 ID。
- `source`：来源系统，例如 `webhook`、`wechat`、`whatsapp`、`internal_test`。
- `message_type`：`text` 或 `audio`。
- `text`：已有文本内容。
- `audio_url`：可选，音频 URL。
- `audio_base64`：可选，本地测试用的内联音频内容。
- `mime_type`：可选，音频 MIME 类型。
- `customer_id`：可选，客户 ID。
- `order_id`：可选，订单 ID。
- `created_at`：ISO 时间戳。

第一版可运行路径以文本消息为主。音频消息可以在已有 transcript 字段时处理；真实 Qwen 转写要等凭证和接口细节确认后再打开。

## 音频转写边界

AI service 内部提供一个与供应商无关的 transcription 函数。

内部结构：

- 输入：音频 URL 或 base64、MIME 类型、语言提示、供应商名称。
- 输出：转写文本、供应商名称、模型名称、可选置信度、原始供应商元数据。

初始 provider 模式：

- `mock`：用于测试和 demo 的确定性本地转写。
- `qwen`：预留 adapter，先校验必要配置；在你提供凭证和接口格式前，返回清晰的配置错误。

需要你后续提供的 Qwen 信息：

- API endpoint。
- API key 的环境变量名，建议默认使用 `QWEN_API_KEY`。
- 模型名，先用占位默认值 `qwen3.6plus`。
- API 期望的音频输入格式：URL、base64、multipart file，还是 n8n binary data。
- 返回 JSON 示例，尤其是转写文本字段的位置。

## Agent 处理 API

新增接口：

`POST /message/handle`

职责：

1. 校验消息 payload。
2. 如果是文本消息，直接使用文本。
3. 如果是音频消息，调用 transcription adapter，或使用已提供的 transcript。
4. 判断消息是否在查询订单状态。
5. 如果有订单 ID，或能从文本中提取订单 ID，则调用 `get_order_status`。
6. 返回结构化 agent 响应。
7. 带上工具调用元数据，便于审计。

响应字段：

- `message_id`
- `normalized_text`
- `intent`
- `tool_calls`
- `answer`
- `requires_human`
- `confidence`
- `transcription`
- `error`

## 工具设计

### get_order_status

输入：

- `order_id`

处理过程：

1. 从 `mock-api /orders/{order_id}` 获取订单。
2. 如果订单中存在 `shipment_id`，继续使用它。
3. 从 `mock-api /shipments/{shipment_id}` 获取物流。
4. 构造简洁的状态说明。

输出：

- `order_id`
- `order_status`
- `shipment_id`
- `shipment_status`
- `estimated_delivery`
- `summary`

实现保持确定性。agent 可以使用简单规则识别订单状态问题，并提取类似 `ord_100` 的订单 ID。

## n8n Workflow 契约

消息 workflow 应该与现有售后事件 workflow 分开。

Webhook path：

`/webhook/message-agent`

必要步骤：

1. 接收消息 payload。
2. 标准化文本或音频字段。
3. POST 到 `ai-service /message/handle`。
4. POST run log 到 `mock-api /run-logs`。
5. 把 agent 回复和工具调用元数据返回给调用方。

失败处理：

- 校验失败返回清晰的 4xx 响应。
- 音频场景缺少 Qwen 配置时返回清晰的配置错误。
- 工具调用失败时返回 `requires_human: true`，并写入 run log。

## 测试策略

AI service 测试：

- 查询订单状态的文本消息会触发 `get_order_status`。
- 没有订单 ID 的文本消息会要求用户补充信息。
- 带 transcript 的音频消息走与文本相同的路径。
- 未配置 provider 的音频消息返回可控错误。
- 工具输出 schema 稳定。

Mock API 测试：

- 订单和物流 fixture 数据可以支撑工具返回。

Workflow 验证：

- n8n workflow JSON 可以成功导入。
- 文本 demo 消息可以返回 agent answer。
- 音频契约 demo 可以返回基于 transcript 的成功结果，或清晰的配置错误。

## 实现说明

保持这个功能小而可检查：

- 暂时不引入完整 agent framework。
- 在你提供凭证和请求示例前，不对 Qwen 发起真实网络调用。
- 使用环境变量配置供应商信息。
- 保留 deterministic mock 模式，保证测试和 demo 稳定。

