<!-- synced-from: services\ai-service\rag\DEV_SPEC.md -->
<!-- reference: 开发与提交规范 -->

## 7. 开发规范

### 7.1 英文注释要求

所有新增业务代码必须使用英文注释。该要求覆盖 Python docstring、行内注释、配置文件注释和脚本注释。注释重点说明：

- 业务意图
- 工具边界
- 异常处理策略
- 配置开关影响
- 与 AImodel 前端契约相关的行为

避免无意义逐行翻译。

### 7.2 错误处理规范

RAG 子系统错误分为：

- 配置错误：启动阶段直接失败。
- Provider 错误：返回可读错误并写 trace。
- 检索空结果：返回 `ok=true`、`is_empty=true`，让 agent 自然说明没有知识命中。
- 数据库错误：写 trace 后抛出服务异常。
- MCP 参数错误：返回 MCP tool error content。

### 7.3 安全输出规范

- 不输出内部工具 JSON。
- 不输出隐藏 prompt。
- 不编造 citation。
- 不把 RAG 内容当作实时商品事实。
- 不把过期知识用于价格、库存、优惠券有效期判断。

### 7.4 环境变量

```dotenv
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/agent
OPENAI_API_KEY=你的 OpenAI API Key
RAG_SETTINGS_PATH=services/ai-service/rag/config/settings.yaml
RAG_DEFAULT_COLLECTION=shopping_guides
RAG_ENABLED=true
```

### 7.5 首版完成定义

首版完成需要同时满足：

- 能摄取 5 篇 `shopping_guides` Markdown 文档。
- 能写入 PostgreSQL 和 pgvector。
- Indexing Pipeline MVP 并入 `IngestionPipeline` 统一编排，并具备集成测试。
- 能通过 hybrid search 找到相关 chunk。
- 能返回 citation。
- 能通过 MCP tool 查询。
- 能被 AImodel 作为工具调用。
- pytest 单元测试和核心集成测试通过。
- Ingestion 和 Query 链路都接入 Trace 打点，并能在 trace 日志中回溯阶段耗时和候选变化。
- Dashboard 六大页面测试通过，能看到文档、chunk、trace 和评估结果。
- AImodel 集成前全链路 E2E 验收通过。

### 7.6 规格反馈同步规范

DEV_SPEC 是项目设计、实施和验收的**单一事实来源**。用户在开发过程中提出的更正、补充要求和质量约束不能只保留在对话上下文中，必须及时回写文档，使后续开发者和 AI 能继续遵循最新决策。

同步要求：

- **先更新规范，再继续开发**：用户明确修改架构、流程顺序、命名、文件位置、数据契约、测试要求、提交要求或验收标准时，应先修改 DEV_SPEC，再继续对应任务。
- **执行影响范围检查**：一次更正可能同时影响技术选型、目录结构、模块职责、数据流、测试方案、任务明细和进度统计。不能只修改用户直接指出的一行。
- **以最新明确要求为准**：新要求与旧文档冲突时，采用用户最新的明确要求，并删除或改写所有过期描述。
- **保持排期一致**：任务合并、删除、拆分或调整顺序后，必须同步更新阶段预览、任务表、实施明细、总任务数、完成数和进度百分比。
- **同步自动开发参考文件**：DEV_SPEC 修改完成后，必须执行 `sync_spec.py --force`，确保 auto-coder 使用最新规范。
- **记录可复用规则**：如果用户更正的是可长期复用的开发流程，例如注释语言、TDD、Git 提交格式或任务完成方式，应写入“开发规范”，不能只修改当前任务。
- **避免无依据扩展**：无法从代码、现有文档或用户说明确认的细节，应先询问用户，不能自行写入规范并当作已确认事实。

每次同步后至少检查：

- 是否仍存在旧名称、旧路径、旧阶段顺序或旧任务编号。
- 目录树与模块职责表是否和任务修改文件一致。
- 测试方法与验收标准是否能够实际执行。
- Trace 阶段、数据流和 Pipeline 顺序是否保持一致。
- 任务状态、完成日期和测试结果是否来源于真实执行。

### 7.7 Git 提交规范

项目采用**一个任务一个原子提交**的方式保存进度。提交只包含当前任务实现、对应测试、DEV_SPEC 进度更新和同步后的参考文件，不混入无关修改。

提交标题格式：

```text
<type>(<scope>): [TASK_ID] <summary>
```

示例：

```text
feat(rag): [A3] add unified RAG settings example
```

提交正文必须使用以下结构：

```text
Changes:
- add ...
- update ...

Testing:
- describe the TDD red-green evidence
- list the exact verification commands

Design Principles:
- list the architecture or design principles applied

Task: A3 - 阶段 A：配置与项目骨架
Spec: DEV_SPEC.md Section 6 (Project Schedule)
Tests: ✅ 5/5 passed in 0.10s
```

提交要求：

- **Changes**：具体列出新增、修改和删除内容，描述实际行为，不使用“update files”等模糊表述。
- **Testing**：记录测试命令、TDD 红灯原因、回归范围和必要的手动验证。
- **Design Principles**：记录本任务实际遵循的设计原则或模式，例如 TDD、配置驱动、工厂模式、接口隔离、优雅降级、单一事实来源。没有特殊模式时应明确写 `None beyond existing project conventions`，不能虚构。
- **Task**：必须包含任务编号和阶段标题。
- **Spec**：固定指向 `DEV_SPEC.md Section 6 (Project Schedule)`；若任务同时修改开发规范，可补充对应章节。
- **Tests**：必须引用提交前最后一次真实测试结果，包括通过数量和耗时，不得沿用旧执行结果或估算数据。
- **原子性**：暂存时使用精确文件路径；发现无关 dirty 文件时不纳入提交。
- **历史重写**：已经推送的提交只有在用户明确要求时才能重写，并使用 `git push --force-with-lease` 更新远程。
- **连续开发**：用户输入 `next` 时，先按本规范提交当前已完成任务，再开始下一个待执行任务。
