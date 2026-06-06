# AImodel Modular RAG

`AImodel Modular RAG` 是 `ai-service` 下可独立开发和部署的检索增强生成子系统，用于为购物对话 Agent 提供可引用、可追踪、可评估的知识检索能力。

## 项目定位

- 使用 PostgreSQL 和 pgvector 持久化文档、Chunk、向量与运行数据。
- 使用 Dense Embedding 与 BM25 构建双路混合检索。
- 通过统一抽象接口和工厂模式切换 LLM、Embedding、Splitter、VectorStore、Reranker 与 Evaluator。
- Transform 使用 `BaseTransform` 抽象契约，并由 ingestion pipeline 按 `settings.transform.steps` 串行执行。
- 使用 MCP tools 向 AImodel 或其他 MCP Client 暴露知识检索能力。
- 使用结构化 Trace、Streamlit Dashboard 和评估指标降低 RAG 黑盒程度。

商品价格、库存、详情和商品链接等实时事实仍由现有商品 API 工具负责，RAG 仅补充选购指南、政策 FAQ、商品说明和品牌知识。

## 开发状态

项目按照 [DEV_SPEC.md](DEV_SPEC.md) 中的阶段任务进行开发。当前已完成独立模块骨架、配置、Prompt、核心类型、PostgreSQL/pgvector 持久化、Repository、可插拔组件基础、文档去重、Loader、DocumentChunker 和 Transform 串行处理，正在推进 Ingestion & Indexing Pipeline。

## 环境与依赖

项目统一使用 [uv](https://docs.astral.sh/uv/) 管理 Python 3.12、`.venv`、依赖解析和 `uv.lock`。所有命令均在本目录执行：

```powershell
uv sync --extra dev --frozen
```

依赖声明变更后先更新锁文件，再同步环境：

```powershell
uv lock
uv sync --extra dev
```

不要使用系统 Python 或手工 `pip install` 修改项目环境。CI 和 Docker 使用 `--frozen`，确保依赖声明与锁文件不一致时直接失败。

## 本地配置

仓库提交 `config/settings.example.yaml` 作为完整配置模板，本地运行配置
`config/settings.yaml` 被 Git 忽略。首次运行先创建本地副本：

```powershell
Copy-Item config/settings.example.yaml config/settings.yaml
```

Provider、模型和运行参数在本地 `settings.yaml` 中选择；API Key 和数据库
连接仍通过配置中声明的环境变量提供，不应直接写入 YAML。

## 常用命令

```powershell
# Run the standalone health entry point.
uv run python main.py

# Run all tests.
uv run pytest

# Run static analysis.
uv run ruff check src tests
```

需要本地 PostgreSQL 的集成测试：

```powershell
$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'
uv run pytest tests -q
```

## 开发约束

- Python 版本为 3.12。
- 使用 uv 管理依赖和执行项目命令，提交 `uv.lock`。
- 使用 pytest 执行单元、集成和端到端测试。
- 不使用 LlamaIndex 或 LangChain RAG 框架，仅允许使用 `langchain-text-splitters`。
- 外部服务在单元测试中必须使用 Fake 或 Mock 隔离。
- 配置模板来自 `config/settings.example.yaml`，运行值来自被 Git 忽略的
  `config/settings.yaml` 和环境变量，不在业务代码中硬编码。
