# AImodel Agent 设计文档

## 背景

`AImodel` 是新增在 `ai-service` 内的通用对话 agent。它面向前端页面调用，用户可以直接向 agent 提问，也可以在问题中提供商品链接，让 agent 调用后端商品工具获取真实商品信息后再回答。

首版目标不是替代现有仓储、采购、物流、客服 agent，而是提供一个页面可用的商品问答与商品推荐入口。所有商品推荐必须来自后端真实商品库，不能由模型编造商品名称、商品 ID 或商品链接。

实现要求：

- 所有新增业务代码必须附带中文注释。
- 中文注释应解释业务意图、工具边界、关键异常处理和前后端契约，不写无意义的逐行翻译。
- 前端新增 AI 模式入口，用户可以在页面侧边栏打开 AImodel 对话面板。

## 目录位置

新增代码目录：

```text
services/ai-service/app/routers/AImodel/
```

建议文件结构：

```text
services/ai-service/app/routers/AImodel/
├── __init__.py
├── router.py
├── schemas.py
├── service.py
└── tools.py
```

职责划分：

- `router.py`：定义 FastAPI 路由，并把请求转交给 service。
- `schemas.py`：定义请求、响应、工具结果等 Pydantic schema。
- `service.py`：初始化 LangChain agent、组织模型流式调用、汇总响应。
- `tools.py`：封装商品链接解析、商品详情查询、商品搜索等工具。

`services/ai-service/app/main.py` 只负责挂载 router，不承载 AImodel 业务逻辑。

## 接口设计

接口路径：

```http
POST /AImodel/chat
```

请求体：

```json
{
  "conversation_id": "可选会话 ID",
  "message": "有推荐的解压玩具吗",
  "links": [
    "https://example.com/items/item_a",
    "https://example.com/items/item_b"
  ]
}
```

字段说明：

- `conversation_id`：可选。首版只透传返回，不做长期会话记忆。
- `message`：必填，用户原始问题。
- `links`：可选，用户提供的商品链接列表。

响应使用 `text/event-stream`，保留同一个 `/AImodel/chat` 路由，不新增单独 stream 接口。

事件格式：

```text
event: status
data: {"content":"正在理解问题"}

event: delta
data: {"content":"推荐"}

event: done
data: {"conversation_id":"可选会话 ID","answer":"agent 的完整自然语言回答","recommended_links":[],"tool_results":[]}
```

事件说明：

- `status`：展示可见处理状态，例如理解问题、识别商品链接、调用商品工具、生成回答。
- `delta`：展示最终回答的增量文本。
- `done`：返回完整回答、推荐链接和工具结果，供前端落最终状态。
- `error`：流式生成过程失败时返回错误内容。

`done` 事件数据结构：

```json
{
  "conversation_id": "可选会话 ID",
  "answer": "agent 的完整自然语言回答",
  "recommended_links": [
    {
      "item_id": "item_a",
      "item_name": "商品名称",
      "url": "/items/item_a"
    }
  ],
  "tool_results": [
    {
      "tool": "get_product_detail_from_link",
      "ok": true,
      "input": "https://example.com/items/item_a",
      "item_id": "item_a",
      "data": {}
    }
  ]
}
```

## 模型与依赖

agent 框架采用 LangChain。

模型配置：

- Provider：阿里云百炼 OpenAI 兼容接口。
- 默认模型：`deepseek-v4-flash`。
- API key 环境变量：`DASHSCOPE_API_KEY`。
- Base URL 环境变量：`DASHSCOPE_BASE_URL`，默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- 可选模型覆盖环境变量：`AIMODEL_MODEL`。

建议新增依赖：

```toml
"langchain",
"langchain-core",
"langchain-openai"
```

如果 `DASHSCOPE_API_KEY` 未配置，`/AImodel/chat` 返回 503，提示模型服务未配置。

## 前端入口设计

首版需要在前端页面增加侧边栏，并在侧边栏中新增 `AI模式` 选项。用户点击 `AI模式` 后，展开 AImodel 对话面板。

参考交互来自用户提供的对话展开预览：

- 页面右侧保留竖向侧边栏。
- 侧边栏每个入口由图标和文字组成，`AI模式` 是其中一个入口。
- 点击 `AI模式` 后，在页面右侧展开一个白色对话面板。
- 对话面板顶部展示 AI 助手名称、更多操作入口和关闭按钮。
- 面板初始状态展示问候语、能力说明和若干快捷问题。
- 面板底部固定输入框，用户输入问题后调用 `POST /AImodel/chat` 并读取 SSE 流式事件。
- 用户提供商品链接时，前端把链接放入 `links` 字段；普通问题只传 `message`。

建议首版前端文件边界：

```text
apps/talonmart-web/src/components/
├── AiModeSidebar.vue
└── AiModeChatPanel.vue

apps/talonmart-web/src/services/
└── aiModelApi.ts

apps/talonmart-web/src/types/
└── aiModel.ts
```

前端职责划分：

- `AiModeSidebar.vue`：展示右侧侧边栏和 `AI模式` 入口。
- `AiModeChatPanel.vue`：展示对话展开面板、快捷问题、消息列表和输入区。
- `aiModelApi.ts`：封装 `POST /AImodel/chat` 的 SSE 流式读取。
- `aiModel.ts`：定义请求、响应、推荐链接和工具结果类型。

接口代理：

- 普通商品接口继续使用 `VITE_API_BASE_URL=/api`，由 Vite 代理到 `mock-api`。
- AImodel 接口使用独立的 `VITE_AI_SERVICE_BASE_URL=/ai-service`，由 Vite 代理到 `ai-service`。
- 本地修改 `vite.config.ts` 代理后，需要重启前端 dev server，已运行的 Vite 进程不会可靠刷新代理配置。

前端样式原则：

- 侧边栏保持轻量，不遮挡商品浏览主流程。
- 对话面板宽度需要适配桌面和移动端，移动端可改为全屏抽屉。
- 输入区固定在面板底部，消息区域独立滚动。
- 快捷问题只作为填充输入或直接发起提问的入口，不硬编码商品推荐结果。
- 所有新增前端业务逻辑代码同样需要中文注释，说明 AI 模式入口、对话请求和链接传递规则。

## 后端工具设计

### 商品链接详情工具

工具名建议：`get_product_detail_from_link`

输入：商品详情页链接。

处理流程：

1. 从链接路径中解析 `/items/{item_id}`。
2. 调用 `MOCK_API_URL/ip/{item_id}` 获取商品详情。
3. 返回商品详情数据、解析出的 `item_id`、工具执行状态。

如果链接无法解析出 `item_id`，工具返回失败结果，不直接中断整个请求。

### 商品搜索推荐工具

工具名建议：`search_products`

输入：用户问题中提取的搜索关键词。

处理流程：

1. 根据用户意图选择关键词，例如“解压玩具”。
2. 调用 `MOCK_API_URL/search?q={keyword}` 查询真实商品。
3. 将搜索结果转换为可推荐商品列表。
4. 使用商品 `item_id` 生成前端详情链接。

推荐商品只能来自该工具返回的数据。agent 不能推荐后端商品库不存在的商品。

## 运行环境变量

本地开发时，在仓库根目录 `.env` 填写：

```dotenv
DASHSCOPE_API_KEY=你的百炼 API Key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AIMODEL_MODEL=deepseek-v4-flash
FRONTEND_BASE_URL=http://localhost:5173
```

Docker 运行时，`docker-compose.yml` 会把这些变量传给 `ai-service` 容器。修改 `.env` 后，需要重启 `ai-service`：

```powershell
docker compose -p after-sales-implementation up -d --build ai-service
```

## 商品链接生成规则

使用环境变量 `FRONTEND_BASE_URL` 控制前端链接基地址。

规则：

- 如果 `FRONTEND_BASE_URL` 已配置，返回 `${FRONTEND_BASE_URL}/items/{item_id}`。
- 如果未配置，返回相对路径 `/items/{item_id}`。

实现时需要去掉 `FRONTEND_BASE_URL` 末尾多余的 `/`，避免生成双斜杠。

## Agent 行为规则

系统提示词需要明确以下约束：

- 优先根据用户意图决定是否调用工具。
- 用户提供商品链接时，先调用商品详情工具获取真实信息，再回答对比、总结、优缺点等问题。
- 用户没有提供商品链接但提出推荐需求时，调用商品搜索工具获取真实商品，再给出推荐。
- 简单闲聊或不需要商品数据的问题，可以直接回答。
- 商品推荐必须基于工具返回结果，不能编造商品、库存、价格或链接。
- 如果工具没有找到合适商品，需要明确说明未找到，而不是虚构推荐。

示例场景：

- 用户说“帮我对比这两个商品”，并传入两个链接：agent 调用两次商品详情工具，然后输出对比结论。
- 用户说“有推荐的解压玩具吗”，没有传链接：agent 调用搜索工具，基于真实商品结果推荐，并附商品详情链接。
- 用户说“这个商品适合送人吗”，并传入一个链接：agent 调用商品详情工具，基于商品信息回答。

## 数据流

```text
前端页面
  -> 点击侧边栏 AI模式
  -> 打开 AImodel 对话面板
  -> POST /AImodel/chat
  -> ai-service AImodel router
  -> AImodel service 初始化 LangChain agent
  -> agent 根据意图调用 tools
  -> tools 调用 mock-api 商品详情或搜索接口
  -> agent 基于工具结果流式生成回答
  -> ai-service 通过 SSE 返回 status、delta、done
```

## 错误处理

- `message` 为空：返回 422，由 Pydantic 校验处理。
- `DASHSCOPE_API_KEY` 缺失：返回 503。
- 链接无法解析：记录失败工具结果，agent 根据可用上下文继续回答。
- `mock-api` 返回 404：记录对应商品未找到，不中断其他商品查询。
- `mock-api` 返回 503：提示后端商品服务暂不可用。
- 百炼模型调用失败：通过 SSE `error` 事件返回错误内容，并保留已成功获取的工具上下文，便于前端排查。

## 测试计划

新增测试文件：

```text
services/ai-service/tests/test_aimodel_agent.py
```

重点覆盖：

- `/AImodel/chat` 路由已挂载。
- 商品链接可从 `/items/{item_id}` 解析出 `item_id`。
- 商品详情工具会调用 `MOCK_API_URL/ip/{item_id}`。
- 商品搜索工具会调用 `MOCK_API_URL/search?q={keyword}`。
- `FRONTEND_BASE_URL` 配置和未配置时的链接生成。
- 缺少 `DASHSCOPE_API_KEY` 时返回 503。
- 使用 fake LLM 或 mock agent 验证 SSE 响应结构，避免单元测试依赖真实百炼网络调用。
- 前端 `aiModelApi.ts` 会按约定提交 `message` 和 `links`，并解析 `status`、`delta`、`done` 事件。
- 前端 `AI模式` 入口可以打开和关闭对话面板。

建议验证命令：

```powershell
pytest services\ai-service\tests -q
ruff check services\ai-service
pnpm --dir apps\talonmart-web test:unit
```

## 首版不做的内容

- 不做长期会话记忆。
- 不新增数据库表。
- 不让模型自由生成不存在的商品链接。
- 不直接修改 `mock-api` 商品数据结构，优先复用现有 `/ip/{item_id}` 和 `/search` 接口。

## AGENTS.md 文档路由

根 `AGENTS.md` 需要新增 `AImodel` 路由入口。业务细节应放入 `docs/AGENTS/AImodel/` 下的具体文档，根 `AGENTS.md` 只维护链接。

建议最小路由：

```markdown
## AImodel

- 总览：[AImodel/README.md](docs/AGENTS/AImodel/README.md)
- ai-service：[AImodel/ai-service.md](docs/AGENTS/AImodel/ai-service.md)
- 后端工具契约：[AImodel/backend-tool-contracts.md](docs/AGENTS/AImodel/backend-tool-contracts.md)
- 维护原则：[AImodel/maintenance-principles.md](docs/AGENTS/AImodel/maintenance-principles.md)
```

后续实现时，如果确认需要更细的业务边界、前端接口或测试说明，再补充对应文档。
