# AImodel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `ai-service` 新增 LangChain + DeepSeek 的 AImodel 对话 agent，并在 TalonMart 前端新增右侧 `AI模式` 对话入口。

**Architecture:** 后端以 `services/ai-service/app/routers/AImodel/` 独立 router 包承载接口、schema、service 和工具；前端以独立组件、类型和 API service 接入 `POST /AImodel/chat`。商品推荐和商品链接回答只基于 `mock-api` 已有 `/ip/{item_id}` 与 `/search` 返回的真实商品。

**Tech Stack:** FastAPI、Pydantic、httpx、LangChain、langchain-deepseek、Vue 3、TypeScript、Vitest。

---

### Task 1: 后端 AImodel 工具与接口

**Files:**
- Create: `services/ai-service/app/routers/AImodel/__init__.py`
- Create: `services/ai-service/app/routers/AImodel/schemas.py`
- Create: `services/ai-service/app/routers/AImodel/tools.py`
- Create: `services/ai-service/app/routers/AImodel/service.py`
- Create: `services/ai-service/app/routers/AImodel/router.py`
- Modify: `services/ai-service/app/main.py`
- Modify: `services/ai-service/pyproject.toml`
- Test: `services/ai-service/tests/test_aimodel_agent.py`

- [ ] **Step 1: Write failing backend tests**

覆盖 `/items/{item_id}` 解析、`FRONTEND_BASE_URL` 链接生成、工具调用路径、缺少 `DEEPSEEK_API_KEY` 的 503、以及 `/AImodel/chat` 路由响应结构。

- [ ] **Step 2: Run backend tests to verify RED**

Run: `pytest services\ai-service\tests\test_aimodel_agent.py -q`

Expected: FAIL because `app.routers.AImodel` does not exist.

- [ ] **Step 3: Implement backend router package**

按设计文档新增 schema、工具、service、router，并在 `main.py` 挂载。

- [ ] **Step 4: Run backend tests to verify GREEN**

Run: `pytest services\ai-service\tests\test_aimodel_agent.py -q`

Expected: PASS.

### Task 2: 前端 AI 模式侧边栏与对话面板

**Files:**
- Create: `apps/talonmart-web/src/types/aiModel.ts`
- Create: `apps/talonmart-web/src/services/aiModelApi.ts`
- Create: `apps/talonmart-web/src/services/aiModelApi.spec.ts`
- Create: `apps/talonmart-web/src/components/AiModeSidebar.vue`
- Create: `apps/talonmart-web/src/components/AiModeSidebar.spec.ts`
- Modify: `apps/talonmart-web/src/App.vue`

- [ ] **Step 1: Write failing frontend tests**

覆盖 API 请求体字段、侧边栏 `AI模式` 入口打开面板、关闭面板、快捷问题填入/发送入口。

- [ ] **Step 2: Run frontend tests to verify RED**

Run: `pnpm --dir apps\talonmart-web test:unit -- --run src/services/aiModelApi.spec.ts src/components/AiModeSidebar.spec.ts`

Expected: FAIL because files do not exist.

- [ ] **Step 3: Implement frontend components**

新增 API service、类型、右侧侧边栏和对话面板，所有新增业务逻辑附带中文注释。

- [ ] **Step 4: Run frontend tests to verify GREEN**

Run: `pnpm --dir apps\talonmart-web test:unit -- --run src/services/aiModelApi.spec.ts src/components/AiModeSidebar.spec.ts`

Expected: PASS.

### Task 3: 文档路由与全量验证

**Files:**
- Create: `docs/AGENTS/AImodel/README.md`
- Create: `docs/AGENTS/AImodel/ai-service.md`
- Create: `docs/AGENTS/AImodel/backend-tool-contracts.md`
- Create: `docs/AGENTS/AImodel/maintenance-principles.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Add AGENTS route docs**

根 `AGENTS.md` 只新增 `AImodel` 路由链接，业务说明写入 `docs/AGENTS/AImodel/`。

- [ ] **Step 2: Run focused verification**

Run:

```powershell
pytest services\ai-service\tests -q
pnpm --dir apps\talonmart-web test:unit -- --run src/services/aiModelApi.spec.ts src/components/AiModeSidebar.spec.ts
```

Expected: PASS.
