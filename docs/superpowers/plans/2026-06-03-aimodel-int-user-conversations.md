# AImodel Int User Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 AImodel 的 `user_id` 统一为现有 `users.id` 数字用户，并在 AI 模式入口支持查询、选择、继续或新建会话。

**Architecture:** 后端只调整 AImodel 自建的 `conversation`、`message`、`user_memory` 三张表，把 `user_id` 改为 `INTEGER`，旧字符串数据按用户确认的 B 方案清空后迁移。前端复用 `CART_USER_ID = 1`，打开 AI 模式时查询会话列表，选择旧会话后加载消息，新对话在首次发送时由 `/AImodel/chat` 创建。

**Tech Stack:** FastAPI、Pydantic、psycopg、LangChain、Vue 3、Vitest、TypeScript。

---

### Task 1: 后端契约测试

**Files:**
- Modify: `services/ai-service/tests/test_aimodel_memory.py`
- Modify: `services/ai-service/tests/test_aimodel_agent.py`

- [ ] **Step 1: 写失败测试**

新增断言：

```python
assert "user_id INTEGER NOT NULL" in normalized_schema
assert "user_id VARCHAR" not in normalized_schema
assert any("TRUNCATE TABLE message, conversation, user_memory" in statement for statement in POSTGRES_AIMODEL_MEMORY_SCHEMA_SQL)
```

新增会话列表和消息读取测试：

```python
store = NoopAiModelMemoryStore()
first_id = store.ensure_conversation(None, user_id=1, first_message="第一轮")
store.append_user_message(first_id, user_id=1, content="第一轮", links=[])
conversations = store.list_conversations(user_id=1)
messages = store.list_messages(first_id, user_id=1)
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest services\ai-service\tests\test_aimodel_memory.py services\ai-service\tests\test_aimodel_agent.py -q`

Expected: FAIL，因为 schema 仍是 `VARCHAR(64)`，store 还没有会话列表/消息读取接口。

### Task 2: 后端实现

**Files:**
- Modify: `services/ai-service/app/routers/AImodel/schemas.py`
- Modify: `services/ai-service/app/routers/AImodel/memory.py`
- Modify: `services/ai-service/app/routers/AImodel/router.py`

- [ ] **Step 1: 修改 schema**

`AiModelChatRequest.user_id` 改为 `int`，新增 `AiModelConversationSummary`、`AiModelStoredMessage`。

- [ ] **Step 2: 修改 memory store**

`conversation`、`message`、`user_memory` 的 `user_id` 改为 `INTEGER NOT NULL`；初始化时增加清空旧表和字段类型迁移语句；新增 `list_conversations(user_id: int)` 与 `list_messages(conversation_id: int, user_id: int)`。

- [ ] **Step 3: 新增路由**

新增：

```http
GET /AImodel/conversations?user_id=1
GET /AImodel/conversations/{conversation_id}/messages?user_id=1
```

### Task 3: 前端测试

**Files:**
- Modify: `apps/talonmart-web/src/services/aiModelApi.spec.ts`
- Modify: `apps/talonmart-web/src/components/AiModeSidebar.spec.ts`

- [ ] **Step 1: 写失败测试**

断言 `streamAiModel` 请求使用 `user_id: 1`，并新增会话列表 API 测试。

- [ ] **Step 2: 运行失败测试**

Run: `pnpm --dir apps\talonmart-web test:unit -- --run src/services/aiModelApi.spec.ts src/components/AiModeSidebar.spec.ts`

Expected: FAIL，因为前端仍生成匿名字符串 user id，且没有会话列表 API。

### Task 4: 前端实现

**Files:**
- Modify: `apps/talonmart-web/src/types/aiModel.ts`
- Modify: `apps/talonmart-web/src/services/aiModelApi.ts`
- Modify: `apps/talonmart-web/src/components/AiModeChatPanel.vue`

- [ ] **Step 1: 类型与 API**

`AiModelChatRequest.user_id` 改为 `number`，删除 `getOrCreateAiModelUserId`，新增 `fetchAiModelConversations` 和 `fetchAiModelConversationMessages`。

- [ ] **Step 2: 面板交互**

面板使用 `CART_USER_ID`。打开后拉取会话列表；展示“新对话”和已有会话；选择会话后加载历史消息并设置当前 `conversationId`。

### Task 5: 文档和验证

**Files:**
- Modify: `docs/AImodel/design.md`
- Modify: `docs/AImodel/conversation-memory-plan.md`
- Modify: `docs/AGENTS/AImodel/ai-service.md`
- Modify: `docs/AGENTS/AImodel/maintenance-principles.md`

- [ ] **Step 1: 更新文档**

删除匿名 `localStorage.aimodel_user_id` 描述，改为首版复用 `CART_USER_ID = 1`；记录 B 方案：旧 AImodel 记忆数据清空后迁移 user_id 字段。

- [ ] **Step 2: 完整验证**

Run:

```powershell
pytest services\ai-service\tests -q
pytest tests\test_current_docs.py tests\test_department_workflows.py -q
ruff check services\ai-service apps\talonmart-web\src
pnpm --dir apps\talonmart-web test:unit -- --run
pnpm --dir apps\talonmart-web type-check
docker compose -p after-sales-implementation up -d --build ai-service
```

运行时验证：

```powershell
Invoke-RestMethod "http://localhost:8001/AImodel/conversations?user_id=1"
```

确认数据库：

```sql
select column_name, data_type from information_schema.columns where table_name in ('conversation','message','user_memory') and column_name = 'user_id';
```
