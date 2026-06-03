# ai-service

`AImodel` 的后端入口位于 `services/ai-service/app/routers/AImodel/`，对外提供页面可调用的通用购物对话接口。

- `POST /AImodel/chat`
  - 用途：接收前端 AI 模式对话请求，使用 LangChain + DeepSeek 根据用户意图回答。
  - 请求字段：`conversation_id` 可选，`message` 必填，`links` 为用户输入或页面传递的商品链接列表。
  - 响应字段：`answer` 返回自然语言回答，`recommended_links` 返回真实商品链接，`tool_results` 返回工具调用结果。

运行配置：

- `DASHSCOPE_API_KEY`：必填，百炼 API Key，未配置时接口返回 503。
- `DASHSCOPE_BASE_URL`：可选，默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- `AIMODEL_MODEL`：可选，默认 `deepseek-v4-flash`。
- `MOCK_API_URL`：商品详情和搜索工具调用的后端地址，默认 `http://mock-api:8000`。
- `FRONTEND_BASE_URL`：生成商品详情链接的前端基地址，未配置时返回 `/items/{item_id}` 相对路径。

本地 `.env` 至少需要填写：

```dotenv
DASHSCOPE_API_KEY=你的百炼 API Key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AIMODEL_MODEL=deepseek-v4-flash
FRONTEND_BASE_URL=http://localhost:5173
```

修改 `.env` 后，如果通过 Docker Compose 运行，需要重建并重启 `ai-service` 容器。

新增业务代码必须附带中文注释，注释重点说明业务意图、工具边界和异常处理，不写无意义的逐行翻译。
