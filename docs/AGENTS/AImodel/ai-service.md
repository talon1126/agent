# ai-service

`AImodel` 的后端入口位于 `services/ai-service/app/routers/AImodel/`，对外提供页面可调用的通用购物对话接口。

- `POST /AImodel/chat`
  - 用途：接收前端 AI 模式对话请求，使用 LangChain + 百炼 OpenAI 兼容接口根据用户意图流式回答。
  - 请求字段：`user_id` 必填，`conversation_id` 为可选数字 ID，`message` 必填，`links` 为用户输入或页面传递的商品链接列表。
  - 响应格式：`text/event-stream`。
  - 流式事件：`status` 返回可见处理状态，`delta` 返回回答增量文本，`done` 返回数字 `conversation_id`、完整 `answer` 和 `recommended_links`。
  - 前端契约：`done` 不返回 `tool_results`，工具调用结果只在后端内部用于约束 agent 回答和生成推荐链接。
  - 流式实现：后端使用 LangChain `messages` 流模式输出 token/chunk，不把 `values` 状态流当作前端正文。
  - 防泄漏规则：模型不得输出工具 JSON；后端流式层会过滤包含 `tool` 字段的工具结果 JSON。
  - 约束：不要新增 `/chat/stream`，流式响应直接复用 `/AImodel/chat`。

会话记忆：

- `conversation`：保存聊天窗口会话，主键为 `id` int 自增，`title` 为 `VARCHAR(40)`，不设置 `conversation_id` 文本字段。
- `message`：保存用户消息和 assistant 最终回答，主键为 `id` int 自增，`conversation_id` 只做逻辑外键，不创建物理外键约束。
- `user_memory`：按匿名 `user_id` 保存长期购物偏好，例如品牌偏好和高性价比偏好。
- 窗口记忆：每次请求只读取当前 `conversation_id` 最近 5 条 `message` 注入 LangChain。
- 长期记忆：从用户明确表达中规则型提取，后续请求按 `user_id` 注入上下文，不在 SSE 响应中直接暴露。
- 工具结果、工具 JSON、隐藏推理和 system prompt 不写入 `message`，也不作为长期偏好保存。

运行配置：

- `DASHSCOPE_API_KEY`：必填，百炼 API Key，未配置时接口返回 503。
- `DASHSCOPE_BASE_URL`：可选，默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- `AIMODEL_MODEL`：可选，默认 `deepseek-v4-flash`。
- `MOCK_API_URL`：商品详情和搜索工具调用的后端地址，默认 `http://mock-api:8000`。
- `FRONTEND_BASE_URL`：生成商品详情链接的前端基地址，未配置时返回 `/items/{item_id}` 相对路径。
- `DATABASE_URL`：可选，配置后 AImodel 会在启动时初始化 `conversation`、`message`、`user_memory` 三张表并持久化记忆；未配置时测试和本地轻量运行使用内存实现。

本地 `.env` 至少需要填写：

```dotenv
DASHSCOPE_API_KEY=你的百炼 API Key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AIMODEL_MODEL=deepseek-v4-flash
FRONTEND_BASE_URL=http://localhost:5173
```

修改 `.env` 后，如果通过 Docker Compose 运行，需要重建并重启 `ai-service` 容器。

新增业务代码必须附带中文注释，注释重点说明业务意图、工具边界和异常处理，不写无意义的逐行翻译。
