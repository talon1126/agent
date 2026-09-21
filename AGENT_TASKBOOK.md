# TalonMart 购物决策 Agent 任务书

> 文档定位：面向“京言式购物决策 Agent”的可执行建设清单。
>
> 实施范围：仅建设 `services/ai-service` 的 Agent 后端、API/SSE 契约、数据与模型管道、评测和必要的 mock-api 适配；继续使用现有商品库、评论、购物车和 RPA 商品数据，不实施任何前端页面、组件、样式或前端测试。

## 1. 建设目标

当前系统已经具备 LangChain Agent、Intent Router、工具白名单、LangGraph Checkpoint、用户记忆、RAG MCP、Self-RAG、多知识库并行检索、SSE 流式回答和 Agent Trace。本任务书不重复建设这些基础能力。

本轮建设要补齐的核心链路是：

```text
用户与页面上下文
  -> 购物目标和约束建模
  -> 候选商品召回与硬过滤
  -> 可解释排序、比较与评论总结
  -> 分层规划和逐步工具授权
  -> 结构化回答与可确认动作
  -> 行为反馈、实时特征与可解释画像
  -> 商品知识图谱与统一实时事实
  -> 多路召回、个性化排序与持续优化
```

最终目标不是增加更多 Prompt 或 Agent 数量，而是让系统稳定完成以下任务：

1. 从自然语言和页面上下文中识别购物目标、预算、场景及硬性约束。
2. 信息不足时提出最少且最有价值的澄清问题。
3. 基于确定性商品事实过滤、排序和比较候选商品。
4. 每个关键推荐结论都能追溯到商品、评论或知识库证据。
5. 在用户确认前不执行加购等有副作用的动作。
6. 通过离线场景集和线上行为事件持续衡量决策质量。
7. 在用户授权范围内把行为事件转换为可追溯、可过期的实时画像特征。
8. 用统一商品语义和带新鲜度的事实服务支撑跨品类推荐。
9. 在硬约束不被破坏的前提下，通过多路召回和个性化排序提升候选相关性。

## 2. 范围边界

### 2.1 本任务书包含

- 购物任务状态、需求槽位、约束冲突和多轮状态迁移。
- 页面、搜索、商品、购物车等上下文接入。
- 商品候选过滤、特征归一化、确定性排序、多样性和比较。
- 评论观点聚合、证据绑定、事实与时效校验。
- 分层任务规划、逐步工具授权、并行执行、失败恢复。
- 结构化响应协议、OpenAPI/JSON Schema 和后端兼容性验证。
- 主动导购触发、加购预览与二次确认。
- 用户行为反馈、任务级评测、安全和性能门禁。
- 用户授权、事件流、在线/离线特征一致性和可解释画像。
- 商品本体、实体治理、关系图谱和统一实时事实服务。
- 多路候选召回、训练样本构造、个性化排序和受控回退。

### 2.2 暂不实施

- 不重写 RAG 内部的 Self-RAG、切分、混合检索、重排和多 collection 并行检索。
- 不接入真实京东私有 API；继续使用现有 mock-api 和 RPA 数据契约。
- 不进行 SFT、DPO 或端到端生成式推荐模型训练；阶段 I 只允许训练输入、标签和输出均可审计的排序模型。
- 不建设多 Agent 群组；优先完成单 Agent 的确定性决策链路。
- 不建设完整人工客服工作台和人工转接体系。
- 不在当前版本实现图片输入、商品图理解和评论图片理解。
- 不建设通用线上实验平台；I5 只实现个性化排序所需的影子对照、受控开关和自动回退。
- 不修改 `apps/talonmart-web`，不实现 Vue/TypeScript 类型、页面渲染、交互组件、响应式布局或前端自动化测试。
- 不以点击率或转化率作为当前离线验收的唯一目标。

### 2.3 设计约束

- 商品价格、库存、规格、评论和购物车必须来自确定性 API，LLM 不得生成业务事实。
- LLM 负责需求理解、规划和解释；硬约束过滤及商品排序由可测试代码负责。
- 保留现有 Intent Router 和工具白名单，升级为“计划级编排 + 步骤级授权”，不向模型一次性开放全部工具。
- `rag_tool` 继续绑定本轮原始问题；业务查询拆解用于商品、评论等工具，RAG 查询扩展仍由 RAG 子系统负责。
- 新协议必须兼容现有 `answer + recommended_links` API 消费方，采用版本化渐进迁移。
- 所有带副作用的工具必须经过用户确认、幂等校验和审计记录。
- 用户画像和个性化特征只能在有效授权目的内读取；撤回授权、删除和过期必须能阻断后续使用。
- 学习排序只能改变通过硬过滤后的候选顺序，不能修改商品事实、放宽硬约束或绕过动作确认。

### 2.4 代码与数据所有权边界

- `services/ai-service/app/routers/AImodel/` 继续承载现有 A-F 的 HTTP/SSE schema、路由和兼容编排；从 G 阶段起不得再向该目录加入事件 worker、特征计算、图谱构建、训练数据集或模型注册实现，A-F 旧模块迁移不作为本任务书前置条件。
- G-H-I 新增在线领域逻辑分别放入 `services/ai-service/app/domains/personalization/`、`catalog/` 和 `recommendation/`，通过稳定接口被现有 Router 调用。
- PostgreSQL、缓存、mock-api、RPA、图存储和模型制品适配放入 `services/ai-service/app/infrastructure/`；领域模块不得直接依赖具体数据库客户端。
- 异步消费与回填入口放入 `services/ai-service/app/workers/`，离线数据集、训练和评测入口放入 `services/ai-service/ml/` 与根目录 `scripts/`，并与在线 Web 进程独立启动和扩缩容。
- 账号身份、商品价格、库存、订单和交易事实仍由各自权威服务拥有；ai-service 只保存授权快照、派生特征、决策快照和必要审计数据，不成为这些业务事实的系统记录源。

## 3. 目标架构

```text
API Consumer
  |  AiModelChatRequest / page_context / candidate refs
  v
HTTP + SSE Adapter
  v
Identity Context + Consent Policy Gate
  |  无画像授权时仍允许非个性化购物问答
  v
Context Assembler <-------------------- Explainable User Profile
  v                                            ^
Shopping Goal Manager <-> Goal Repository      |
  v                                            |
Planner + Step Policy Gate                     |
  |-- RAG MCP（保留 Self-RAG 与内部并行检索）   |
  |-- Product / Review / Cart / Web Tools       |
  v                                            |
Multi-route Recall <----- Product Relation Graph
  v                         ^
Commerce Fact Gateway <- Entity Resolution <- Product Ontology
  v
Hard Filter -> Personalized Ranker -> Deterministic Fallback
  v
Comparator / Review Insights
  v
Grounding Verifier + Action Guard
  v
Structured Response Composer -> SSE / API Response
  |
  `-> Feedback API -> Consent Gate -> Append-only Event Pipeline
                                      |
                                      v
                         Feature Registry + Online/Offline Store
                                      |
                                      `-> User Profile / Training Dataset

Agent Trace + Offline Evaluation 贯穿全部在线与离线步骤
```

## 4. 验收指标

以下 Agent 基础指标在阶段 A 建立基线并由阶段 F 执行 M3 发布门禁；画像、商品知识和个性化指标分别由 G5、H5、I5 执行后续里程碑门禁。不能达到时必须保留失败样本、Trace 和原因，不允许只报告平均值。

| ID | 适用门禁 | 维度 | 发布目标 | 计算口径 |
| --- | --- | --- | ---: | --- |
| M3-01 | F5 | 购物任务成功率 | `>= 85%` | 100 条冻结场景中完成正确追问、过滤、比较或推荐的比例 |
| M3-02 | F5 | 硬约束违反率 | `0%` | 预算、品牌排除、库存、配送时限等硬条件不得被推荐结果违反 |
| M3-03 | F5 | 商品事实准确率 | `100%` | 商品 ID、价格、库存、规格必须与本轮工具快照一致 |
| M3-04 | F5 | 关键结论证据覆盖率 | `>= 95%` | 推荐理由、商品差异和评论结论具有可定位证据 |
| M3-05 | F5 | 必要澄清召回率 | `>= 90%` | 缺少关键槽位且无法可靠决策的样本能够触发澄清 |
| M3-06 | F5 | 结构化响应合法率 | `100%` | 所有完成事件通过 Pydantic 与生成的 OpenAPI/JSON Schema 校验 |
| M3-07 | F5 | 越权工具调用 | `0` | 不得调用当前计划步骤未授权的工具 |
| M3-08 | F5 | 副作用操作确认覆盖率 | `100%` | 加购等操作必须存在有效确认令牌 |
| M3-09 | F5 | 故障可恢复率 | `>= 95%` | 单工具超时或失败时返回可理解降级结果，不产生错误事实 |
| M3-10 | F5 | Agent 完整响应 P95 | `<= 25s` | 固定测试环境中的端到端请求；同时单独记录 TTFT、工具和模型耗时 |
| M4-01 | G5 | 未授权画像读取 | `0` | 无有效 consent scope、已撤回或已过期的目的不得返回个性化特征 |
| M4-02 | G5 | 在线/离线特征一致率 | `100%` | 冻结事件回放中同一 as_of 时间的特征键、值和版本完全一致 |
| M4-03 | G5 | 事件处理时效与坏消息率 | `P95 <= 60s`、`dead-letter <= 0.1%` | 排除主动注入的非法事件，按 received_at 到派生特征提交时间计算 |
| M4-04 | G5 | 画像读取性能 | `P95 <= 150ms`、deadline `200ms` | 100 并发、缓存冷暖各一轮；超时必须回退非个性化结果 |
| M5-01 | H5 | 商品实体自动合并质量 | 冻结冲突集误合并 `0`，抽样 precision `>= 99.5%` | 每次发布随机复核至少 1,000 个 auto_match，且每个核心品类不少于 200 个；不确定项进入待审核 |
| M5-02 | H5 | 实时事实新鲜度合规率 | `100%` | 返回的已知事实必须在对应 freshness budget 内，超时值只能标记 stale/unknown |
| M5-03 | H5 | 可行动事实覆盖率 | `>= 95%` | 至少 1,000 个、覆盖 5 个核心品类的冻结可售 offer 中，价格、库存和配送均为 fresh 的比例；stale/unknown 不计入分子 |
| M5-04 | H5 | 图谱与事实读取性能 | 图查询 `P95 <= 200ms`，50 商品事实批量读取 `P95 <= 800ms` | 固定数据规模、100 并发且无外网随机依赖 |
| M6-01 | I5 | 多路召回目标覆盖率 | `Recall@50 >= 95%` | 冻结个性化场景中包含标注为可接受的目标商品 |
| M6-02 | I5 | 个性化排序离线增益 | `NDCG@10 >= 基线 1.05 倍` | baseline NDCG@10 必须 `> 0`；至少 10,000 个评测 request，95% bootstrap CI 下界必须 `> 1.00 倍`，关键分群各不少于 500 个 request |
| M6-03 | I5 | 排序服务性能 | 100 候选 `P95 <= 150ms` | 超时、非法输出或版本不兼容必须回退 C4 baseline |

所有非业务事实阈值统一存放在 `config/agent_quality_gates.yaml`，由发布脚本读取，禁止在测试代码和运行代码中各自维护。首版同时冻结以下默认值：授权撤回后立即阻止新读取，派生数据清理 `<= 24h`；图查询 `max_hops=2`、`max_nodes=100`；catalog/price/promotion/inventory/delivery freshness budget 分别为 `24h/5min/5min/60s/5min`；多路召回总 deadline `1000ms`；排序自动回退触发后 `60s` 内停止向新请求提供候选模型结果。

M3-M4 只要求点击、加购、购买、取消和退货事件完整采集、可信分级与可归因；只有达到 I-DATA-READY 后才允许把真实反馈用于 M6-B 模型发布，禁止用模拟数据制造业务结论。

## 5. 阶段总览

| 阶段 | 名称 | 目标 | 任务数 | 里程碑 |
| --- | --- | --- | ---: | --- |
| A | 基线与协议 | 冻结现状、建立场景集并完成兼容型 API 协议 | 5 | 可比较、可演进 |
| B | 购物目标状态 | 让 Agent 持续理解用户目标、约束和待确认信息 | 5 | 状态驱动对话 |
| C | 商品决策引擎 | 实现确定性过滤、排序、比较和评论洞察 | 5 | 可解释推荐 |
| D | Agent 编排与校验 | 实现多步规划、逐步授权、并行工具和结果校验 | 5 | 核心导购 MVP（M1） |
| E | 主动服务与反馈 | 接入页面上下文、主动触发、确认式加购和反馈事件 | 5 | 试运行版（M2） |
| F | 安全、评测与发布 | 完成攻防、任务评测、性能优化和发布门禁 | 5 | 发布候选版（M3） |
| G | 电商数据与实时画像 | 建立授权边界、事件管道、特征存储和可解释画像 | 5 | 数据闭环版（M4） |
| H | 商品知识与实时事实 | 建立商品本体、实体治理、关系图谱和事实网关 | 5 | 商品知识版（M5） |
| I | 个性化召回与排序 | 建立多路召回、训练数据、个性化排序和安全混排 | 5 | 架构就绪（M6-A）/ 模型发布（M6-B） |
| **总计** |  |  | **45** |  |

建议先按 A -> B -> C -> D -> E -> F 完成 M3。M3 稳定后，G 与 H 可以并行建设；I 必须等待 G、H 的阶段退出条件同时满足。阶段内部允许并行编写测试和数据契约，但不得跳过明确的前置依赖和退出条件。

每个子任务都按同一结构描述：目标说明“为什么做”，前置依赖规定启动条件，实施内容限定工作范围，交付物规定必须形成的代码或文档，完成标准用于判定是否真正结束，验证命令提供可重复的检查入口，任务边界用于阻止顺手扩项。任何一项完成标准未满足，均不能把该任务视为完成。

## 6. 阶段 A：基线与协议

阶段目标：不改变现有行为，先建立可回归的质量基线，并为上下文和结构化结果提供兼容协议。

### A1：冻结现有 Agent 基线 ✔️

**目标**

在引入购物目标、规划器和结构化响应前，固定当前 Agent 的公开行为和关键内部路由结果。后续任务必须能区分“预期升级”与“意外回归”，不能以新架构为理由破坏现有商品查询、知识问答、联网搜索、订单查询、会话续接和 SSE 输出。

**前置依赖**

- 无。该任务是其余任务的共同起点。
- 使用当前分支、当前配置和固定 fixtures 运行，报告中记录 commit、模型配置是否可用以及外部服务是否被 mock。

**实施内容**

1. 盘点 `intent_routes.yaml` 中所有 route、每个 route 允许的工具和对应 fast path。
2. 为 `route_aimodel_intent_with_candidates()`、`_agent_tools_for_intent_route()`、`handle_chat()` 和 `stream_chat_events()` 建立稳定回归样本。
3. 覆盖商品搜索、商品链接详情、RAG、多 collection 路由、Tavily 未配置降级、订单状态、直接回复和拒答路径。
4. 固定会话创建、历史消息读取、推荐链接生成、SSE `status/delta/done` 顺序和错误事件格式。
5. 选取至少 10 次代表性请求保存脱敏 Trace 摘要，记录当前端到端耗时、工具耗时和失败类型，形成后续对比基线。

**交付物**

- 扩充后的 `services/ai-service/tests/test_aimodel_agent.py`。
- `fixtures/evals/shopping_agent_baseline.json`，保存固定输入与预期 route、allowed_tools、response 基本形态。
- `docs/agent_baseline.md`，记录环境、样本数、现有能力、已知失败和性能数据，不写无法复现的宣传指标。

**完成标准**

- 每个现有 route 至少有一个正向样本，商品、RAG、联网、订单、直接回复和拒答均有独立回归测试。
- 相同固定输入连续执行时，route 和 allowed_tools 结果一致。
- SSE 测试能证明 `done` 只出现一次，且工具内部 JSON 不会泄露给用户。
- 未配置 Tavily、RAG 不可用或 mock-api 返回错误时，测试明确记录当前降级行为。
- 基线报告能够区分模型耗时、工具耗时和总耗时，并附原始测试命令。
- 本任务不改变生产行为；如为可测试性增加 hook，默认运行路径必须与修改前一致。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_aimodel_agent.py services/ai-service/tests/test_aimodel_rag_tool.py services/ai-service/tests/test_aimodel_memory.py -q
```

**任务边界**

不新增购物能力，不调整 Prompt，不改变路由阈值，不优化性能；发现的问题只记录为基线缺陷，除非它导致测试无法建立。

### A2：建立购物任务 Golden Set v1 ✔️

**目标**

建立一套不依赖主观印象的购物任务样本，使后续目标抽取、过滤、排序、比较和澄清策略都能在同一批场景上验收。v1 用于开发和建立基线，不作为最终发布集。

**前置依赖**

- A1 已产出基线 route、工具和响应能力清单。
- 商品、评论、库存和购物车 fixtures 中使用的 ID 已确认存在，禁止在期望结果里引用不存在的商品。

**实施内容**

1. 定义场景 schema，至少包含 `scenario_id`、`category`、`turns`、`page_context`、`expected_goal`、`required_tools`、`forbidden_tools`、`expected_response_type`、`hard_assertions` 和 `tags`。
2. 首批构造不少于 40 条场景，至少覆盖：需求模糊、预算边界、品牌偏好、品牌排除、规格要求、配送时限、商品比较、评论总结、约束冲突、无候选、工具超时和多轮修改需求。
3. 每条场景使用确定性断言表达硬结果，例如“推荐结果价格不得超过 100”，不把某段自然语言答案写成唯一正确答案。
4. 为场景文件实现 Pydantic 校验模型和唯一 ID、商品引用、断言字段完整性检查。
5. 生成分类覆盖报告，显示每种标签、响应类型和失败模式的样本数量。

**交付物**

- `fixtures/evals/shopping_agent_scenarios.json`。
- `services/ai-service/tests/test_shopping_agent_scenarios.py`。
- `scripts/validate_shopping_agent_scenarios.py`。
- `docs/shopping_agent_scenario_coverage.md`。

**完成标准**

- 有效场景不少于 40 条，`scenario_id` 全局唯一，所有商品引用均能在 fixtures 或 mock-api 中解析。
- 每条场景至少声明一个可机器判断的硬断言；不能只有“回答合理”之类主观描述。
- 模糊需求、硬约束、比较、评论、冲突、无结果和故障七类场景均不少于 4 条。
- 多轮场景至少 10 条，并包含用户修改预算、撤销偏好和增加排除条件。
- 校验脚本对重复 ID、缺失字段、非法响应类型和不存在商品返回非零退出码。
- 覆盖报告能由脚本重新生成，不能手工维护统计数字。

**验证命令**

```powershell
uv run --project services/ai-service python scripts/validate_shopping_agent_scenarios.py
uv run --project services/ai-service pytest services/ai-service/tests/test_shopping_agent_scenarios.py -q
```

**任务边界**

v1 不使用 LLM Judge 决定对错，不包含真实用户隐私数据，不为了提高分数删除困难样本。

### A3：扩展请求上下文协议 ✔️

**目标**

让 API 消费方能够显式传入用户当前所在页面、搜索词和候选商品引用，同时保持旧版聊天请求完全可用。调用方上下文只用于表达“用户正在看什么”，价格、库存等业务事实仍由服务端读取。

**前置依赖**

- A1 的旧请求和 SSE 回归测试已固定。
- 页面上下文首版字段清单已在 A3 任务评审时确认；A2 可并行使用同一字段清单编写场景。

**实施内容**

1. 在 `schemas.py` 中新增版本化 `AiModelPageContext` 和 `AiModelCandidateRef`。
2. 页面上下文只允许受控字段：页面类型、路由、搜索词、当前商品 ID、候选商品 ID、来源事件和客户端时间；禁止客户端提交可信价格或库存。
3. 为搜索词长度、候选商品数量、链接数量、单字段长度和整个请求体设置上限。
4. 增加 `request_version`，未提供时按 v1 旧请求处理；不允许客户端自行指定服务端策略版本。
5. 将上下文作为独立结构传到服务层，不直接拼接未清洗 JSON 到 System Prompt。
6. 生成并冻结 OpenAPI/JSON Schema 快照，使调用方能够从后端契约生成类型，不在本任务维护任何前端类型文件。

**交付物**

- 更新 `services/ai-service/app/routers/AImodel/schemas.py`。
- 新增 `services/ai-service/tests/test_aimodel_context.py`。
- 请求 OpenAPI/JSON Schema 快照及兼容性测试。

**完成标准**

- 当前只有 `user_id/conversation_id/message/links` 的请求无需修改即可通过并保持原行为。
- 合法页面上下文可完成 JSON 往返，字段类型、默认值和 OpenAPI/JSON Schema 一致。
- 超长搜索词、超过上限的候选列表、非法页面类型、未知嵌套字段和非正数商品引用被 422 拒绝。
- 服务端不会信任或转发客户端伪造的价格、库存、评分和配送事实。
- 上下文缺失、部分提供或页面类型未知时，Agent 仍可退回普通聊天路径。
- OpenAPI/JSON Schema 快照只发生预期兼容变更，未知字段和破坏性变更测试会失败。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_aimodel_context.py services/ai-service/tests/test_api.py -q
```

**任务边界**

本任务只建立传输协议，不读取购物车、不决定上下文优先级、不实现主动触发；这些行为属于 E1 和 E2。

### A4：建立结构化响应协议 ✔️

**目标**

把“Markdown 答案 + 链接”升级为可被任意 API 消费方稳定解析的响应契约，同时为旧调用方保留文本答案和推荐链接。结构化数据必须来源于已校验的领域对象，消费方不需要解析 Markdown 猜测商品和比较关系。

**前置依赖**

- A3 请求版本策略已确定。
- 现有 `AiModelChatResponse`、SSE 事件和历史消息持久化回归测试可运行。

**实施内容**

1. 定义 `response_type` 判别联合，至少支持 `answer`、`clarification`、`product_list`、`comparison`、`recommendation`、`action_preview`、`action_result` 和 `fallback`。
2. 为澄清选项、商品引用、比较列/行、推荐理由、证据引用和动作预览分别建立 Pydantic 模型。
3. 规定跨类型不变量：商品 ID 唯一、比较列与单元格对应、推荐商品必须出现在候选列表、证据引用不得为空指针。
4. 保留顶层 `answer` 和 `recommended_links`，明确它们如何从结构化 payload 派生，防止两套内容互相矛盾。
5. 版本化 SSE `done` payload；旧客户端忽略新增字段仍能完成对话。
6. 为历史消息存储新增结构化 payload 字段或版本化 JSON，读取旧消息时自动生成文本 fallback。

**交付物**

- 更新 `schemas.py`、`service.py` 和 `memory.py` 中的响应及持久化契约。
- 新增 `services/ai-service/tests/test_aimodel_response_schema.py`。
- 生成版本化 OpenAPI/JSON Schema fixture 和兼容性差异测试。

**完成标准**

- 八种响应类型均有合法样本和至少一个非法样本测试。
- 旧版请求仍收到非空 `answer` 与 `recommended_links`，旧消息记录能够正常读取。
- `response_type` 与 payload 类型不匹配、重复商品 ID、比较矩阵维度错误或空 action token 时，后端拒绝生成最终响应。
- SSE `done` 事件只携带通过 schema 校验的响应；序列化失败时发送受控 `error`，不能输出半个 JSON。
- Pydantic schema、OpenAPI 和 JSON Schema fixture 的判别字段及必填项一致，契约测试不解析正文。
- 数据库初始化和升级保持幂等，不清空已有会话消息。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_aimodel_response_schema.py services/ai-service/tests/test_aimodel_agent.py services/ai-service/tests/test_aimodel_memory.py -q
```

**任务边界**

不实现任何前端渲染，不实现排序、比较算法或加购执行；只定义并验证后端协议。

### A5：扩展 Trace 与指标字典 ✔️

**目标**

为后续每个决策阶段建立统一可观测契约，使一次推荐可以追溯“识别了什么目标、执行了什么计划、淘汰了哪些候选、为什么排序、验证是否通过”，而不是只看到最终模型回答。

**前置依赖**

- A1 已记录当前 Trace 格式和持久化行为。
- A3/A4 已确定请求版本和响应类型字段。

**实施内容**

1. 扩展事件类型字典：`context`、`goal`、`plan`、`step`、`tool_call`、`filter`、`rank`、`verify`、`response` 和 `error`。
2. 每个事件统一包含 `trace_id`、`event_id`、`stage`、`status`、`started_at`、`duration_ms`、`summary` 和可选关联 ID。
3. 明确事件开始、成功、失败和跳过语义；异常路径也必须关闭已开始事件。
4. 为目标和候选只记录脱敏摘要、数量、hash 或稳定 ID，不写入完整用户隐私、工具密钥和整段外部内容。
5. Trace 写入失败不得中断用户回答，但必须进入应用错误日志并产生可统计计数。
6. 保持现有 `agent_trace` 和 `agent_trace_event` 查询兼容，数据库变更采用幂等迁移。
7. 定义 `config/agent_quality_gates.yaml` schema，先冻结 M3-01 至 M3-10 的目标、分母、窗口和适用里程碑；G5/H5/I5 只能以版本化方式追加各自门禁。

**交付物**

- 更新 `services/ai-service/app/routers/AImodel/agent_trace.py`、`memory.py` 和 `service.py`。
- 新增 `services/ai-service/tests/test_aimodel_trace.py`。
- `docs/agent_trace_dictionary.md`，列出每种事件的必填字段、允许内容和脱敏规则。
- `config/agent_quality_gates.yaml`、Pydantic 校验模型和门禁配置测试。

**完成标准**

- 每次聊天请求只有一个顶层 trace_id，并贯穿 intent、工具、RAG query trace 和最终响应。
- 成功、工具失败、模型失败、客户端取消四条路径都有完整事件序列测试。
- 每个已开始事件都有终态，`duration_ms` 为非负数，事件顺序可稳定重建。
- Trace payload 中不出现 API Key、Authorization header、完整购物车地址或未经截断的用户长文本。
- Trace 存储不可用时聊天仍返回结果，且错误可以通过日志定位。
- 旧 Trace 记录仍能读取，新增字段缺失时使用兼容默认值。
- 门禁配置拒绝重复 ID、未知里程碑、缺少计算窗口和运行期放宽冻结阈值；报告必须记录配置版本与 hash。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_aimodel_trace.py services/ai-service/tests/test_aimodel_agent.py -q
```

**任务边界**

本任务只建立事件契约和采集能力，不建设独立 Dashboard，不把原始 Prompt、完整工具返回或隐私数据作为 Trace 详情保存。

计划修改文件：

- `services/ai-service/app/routers/AImodel/schemas.py`
- `services/ai-service/app/routers/AImodel/agent_trace.py`
- `services/ai-service/app/routers/AImodel/service.py`
- `fixtures/evals/shopping_agent_scenarios.json`

阶段退出条件：旧 API 契约兼容测试全部通过；Golden Set 能运行并产出基线报告；新旧响应均可从同一 SSE 流解析。

## 7. 阶段 B：购物目标状态

阶段目标：把“聊天历史”升级为明确、可持久化、可解释的购物任务状态。

### B1：定义购物目标模型 ✔️

**目标**

建立独立于聊天文本的购物目标领域模型，明确区分用户必须满足的条件、可权衡偏好、明确排除项、尚未回答的问题和当前决策阶段。后续过滤、排序和澄清只能依赖该模型的受控字段。

**前置依赖**

- A3 已定义页面上下文可提供的信息。
- A4 已定义澄清和推荐响应需要的数据。

**实施内容**

1. 定义 `ShoppingGoal`、`Constraint`、`Preference`、`OpenSlot`、`GoalEvidence` 和 `DecisionStage`。
2. 最低支持品类、使用场景、预算上下限、品牌包含/排除、规格、配送截止时间、数量和自由偏好。
3. 每个目标字段保存来源类型、来源 turn、原文片段、置信度、创建时间和最后更新时间。
4. 区分 `hard`、`soft`、`exclude` 和 `unknown`，禁止使用单一字符串列表混合表达。
5. 定义阶段迁移：`discovering -> clarifying -> searching -> comparing -> decided`，以及回退到澄清的条件。
6. 增加 schema_version 和向后兼容解析器，为后续字段演进保留空间。

**交付物**

- `services/ai-service/app/routers/AImodel/shopping_goal.py`。
- `services/ai-service/tests/test_shopping_goal.py`。
- `docs/shopping_goal_schema.md`，给出每个字段的语义、示例和禁止用法。

**完成标准**

- 硬约束、软偏好、排除项和未知槽位具有不同类型，不能通过非法枚举值混淆。
- 预算上下限、数量、时间和置信度具有边界校验；非法范围无法构造模型。
- 模型 JSON 序列化后可无损恢复，schema_version 缺失的旧对象能够按已定义策略读取。
- 每项约束都能追溯到 turn 或页面上下文来源；没有来源的字段不能被标为用户硬约束。
- 决策阶段迁移有明确允许表，不能从 `discovering` 无条件跳到 `decided`。
- 至少覆盖 20 个模型测试，包括空目标、完整目标、非法预算、互斥品牌字段和旧版本兼容。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_shopping_goal.py -q
```

**任务边界**

不在本任务从自然语言抽取字段，不持久化数据库，不实现商品过滤；只定义领域模型及其不变量。

### B2：实现目标抽取器 ✔️

**目标**

将当前 turn 的自然语言和受控页面上下文转换成 `ShoppingGoal` 增量，并保留证据与不确定性。抽取器只描述“本轮新增或修改了什么”，不直接覆盖完整会话状态。

**前置依赖**

- B1 模型和字段语义已冻结。
- A2 场景集中已包含抽取样本和预期目标字段。

**实施内容**

1. 先用确定性规则处理价格区间、数量、日期、否定词、品牌包含/排除和显式品类。
2. 对规则无法覆盖的场景偏好使用模型结构化输出，结果必须经过 B1 schema 校验。
3. 为每个抽取字段保留原文 span、来源 turn 和 confidence；模型推断不得伪装成用户原话。
4. 正确处理“不要苹果”“预算不是 300，是 500”“品牌无所谓”“越便宜越好”等否定、纠正和偏好撤销表达。
5. 模型不可用、输出非法或超时时退回规则结果，不能让整个聊天失败。
6. 记录抽取耗时、规则命中字段、模型补充字段和被 schema 拒绝字段到 Trace 摘要。

**交付物**

- `services/ai-service/app/routers/AImodel/goal_extractor.py`。
- `services/ai-service/tests/test_goal_extractor.py`。
- 抽取测试 fixtures，覆盖中文口语、省略表达、否定和纠正。

**完成标准**

- 预算、场景、品类、品牌、规格、配送时限和排除项均有正向、否定和边界测试。
- 明确表达的硬条件才能生成 `hard`；推测或低置信结果只能生成 `soft` 或 `unknown`。
- 当前 turn 没有提及的字段不出现在增量中，避免模型重写历史目标。
- 用户纠正语句输出明确的 replace/remove 操作，而不是同时保留新旧冲突值。
- 模型异常时规则抽取仍返回合法增量，错误进入 Trace 且不泄露内部响应。
- 在 A2 相关样本上输出机器可比较的字段级准确率报告，而非只展示案例。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_goal_extractor.py -q
```

**任务边界**

不修改历史状态，不决定是否追问，不把抽取结果直接写入长期用户偏好。

### B3：实现状态合并与冲突检测 ✔️

**目标**

把 B2 的本轮增量以确定性规则合并到会话目标中，处理覆盖、撤销和冲突，并输出可供澄清策略消费的冲突对象。相同输入必须得到相同状态。

**前置依赖**

- B1 目标模型与阶段迁移规则完成。
- B2 能输出带操作类型和证据的合法增量。

**实施内容**

1. 实现 `merge_goal_delta(current, delta)`，禁止在函数内部调用 LLM。
2. 定义优先级：当前 turn 明确表达 > 当前页面上下文 > 历史会话字段 > 长期用户偏好。
3. 支持 add、replace、remove 和 confirm 操作；保留变更前值和来源，形成可审计事件。
4. 检测预算上下限反转、品牌同时包含与排除、互斥规格、配送时间早于当前时间等冲突。
5. 冲突不得静默选边，必须生成 `GoalConflict`，包含字段、冲突值、来源和建议澄清主题。
6. 根据必填槽位、冲突数量和候选状态更新 `DecisionStage`，非法迁移必须被拒绝。

**交付物**

- 在 `shopping_goal.py` 中实现合并、冲突和迁移逻辑，或拆分为 `goal_state.py`。
- `services/ai-service/tests/test_goal_state_transition.py`。
- `docs/shopping_goal_merge_rules.md`。

**完成标准**

- 同一状态和增量重复执行不会产生重复约束或不同结果。
- 用户本轮明确纠正预算时旧预算失效但仍保留审计来源；软偏好不会覆盖硬约束。
- 至少覆盖预算、品牌、规格、时间四类冲突，并返回稳定 conflict code。
- remove 操作只影响指定字段，不清空无关目标。
- 非法阶段跳转和缺少证据的硬约束会被明确拒绝。
- 合并函数为纯逻辑，可在不启动模型、数据库和 HTTP 服务时完成全部单元测试。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_goal_state_transition.py -q
```

**任务边界**

不生成面向用户的提问文本，不访问商品 API，不持久化数据库。

### B4：实现澄清策略 ✔️

**目标**

在信息不足或目标冲突时，只询问当前最能改变候选集合或推荐结论的问题；信息已经足够时立即进入搜索，避免为了收集完整画像而连续盘问用户。

**前置依赖**

- B3 能输出 open slots、冲突和当前决策阶段。
- A4 已定义 `clarification` 响应及选项结构。

**实施内容**

1. 为预算、品类、核心场景、关键规格、品牌冲突和配送期限定义决策价值规则。
2. 按“冲突阻断 > 无法确定品类 > 会大幅缩小候选 > 一般偏好”选择一个主题。
3. 每轮最多提出一个问题，但可以提供 2-5 个结构化选项和“都可以/跳过”。
4. 已回答、已跳过或近期重复询问的槽位不得再次询问，除非用户新输入造成冲突。
5. 问题选项必须来自配置或已知商品属性，不得由模型编造不存在的规格。
6. 无法从回答消除不确定性时允许带置信度推荐，并在响应中说明关键未知项。

**交付物**

- `services/ai-service/app/routers/AImodel/clarification.py`。
- `services/ai-service/tests/test_clarification_policy.py`。
- 澄清优先级配置及字段说明。

**完成标准**

- 相同目标状态总是选择相同澄清主题和稳定 option value。
- 有阻断冲突时不得进入商品推荐；无阻断缺口且候选可排序时不得继续追问。
- 单次响应只包含一个 `slot_key`，选项数量和文本长度符合 A4 schema。
- 用户选择“跳过”后，同一槽位在当前任务中不会循环出现。
- A2 中所有“需要澄清/不需要澄清”样本可自动计算必要澄清召回率和无意义追问率。
- 问题生成失败时提供固定模板 fallback，不暴露内部字段名。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_clarification_policy.py -q
```

**任务边界**

不负责把选项写成复杂营销话术，不调用商品排序器，不一次询问多个主题。

### B5：持久化目标状态并隔离长期偏好 ✔️

**目标**

让购物目标在同一会话的多轮请求和服务重启后保持一致，同时与“用户长期喜欢某品牌”这类长期记忆严格隔离，避免一次购物的临时预算污染未来会话。

**前置依赖**

- B1-B3 的模型、序列化版本和合并规则稳定。
- 当前 `AiModelMemoryStore` 的 PostgreSQL 与内存 fallback 行为已由 A1 固定。

**实施内容**

1. 定义 `ShoppingGoalRepository` 协议，提供 load、create、compare-and-save 和 append-event。
2. 新增 `shopping_goal_state` 与必要的事件表，至少保存 goal_id、conversation_id、user_id、schema_version、revision、payload、created_at 和 updated_at。
3. 使用 revision 做乐观并发控制，两个并发 turn 不得静默覆盖对方更新。
4. 提供与 PostgreSQL 行为一致的内存实现，供单元测试和无数据库环境使用。
5. 校验 conversation 所属 user，禁止通过 conversation_id 读取或更新其他用户目标。
6. 只把被用户明确表达、可跨场景复用且有证据的字段同步到 `user_memory`；预算、当前候选和配送期限默认不进入长期记忆。
7. 数据库初始化必须幂等，升级不得 truncate 已有 conversation、message 或 user_memory。

**交付物**

- `services/ai-service/app/routers/AImodel/goal_repository.py`。
- `memory.py` 中的幂等表结构或独立迁移文件。
- `services/ai-service/tests/test_shopping_goal_repository.py`。
- repository 并发、权限和重启恢复测试 fixtures。

**完成标准**

- 同一 conversation 的后续请求可以加载上一轮完整目标和 revision。
- 不同 user 访问同一 conversation_id 时返回不存在或权限错误，不能泄露目标摘要。
- revision 冲突返回明确错误并允许调用方重新加载合并，不能 last-write-wins。
- PostgreSQL 与内存实现通过同一组契约测试。
- 临时预算、当前商品和配送期限不会出现在新会话；明确品牌偏好按证据和过期规则复用。
- 重复运行初始化不会报错、重复建表或清空已有消息。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_shopping_goal_repository.py services/ai-service/tests/test_aimodel_memory.py -q
```

**任务边界**

不实现跨设备同步 UI，不引入新的缓存服务，不把完整对话或页面行为复制进长期记忆。

建议新增模块：

- `services/ai-service/app/routers/AImodel/shopping_goal.py`
- `services/ai-service/app/routers/AImodel/goal_extractor.py`
- `services/ai-service/app/routers/AImodel/clarification.py`
- `services/ai-service/app/routers/AImodel/goal_repository.py`

阶段退出条件：多轮场景中，用户修改预算、补充场景或排除品牌后，状态能正确迁移；需要澄清的样本不能提前给出伪确定推荐。

## 8. 阶段 C：商品决策引擎

阶段目标：把商品推荐从 LLM 主观选择改造成可测试、可解释的确定性决策流水线。

### C1：建立商品事实快照 ✔️

**目标**

为一次购物决策建立不可变的商品事实快照，确保过滤、排序、比较和最终回答使用同一版本的价格、库存、规格、评分与配送信息，不在同一 turn 中混用不同时间读取的事实。

**前置依赖**

- A3 已定义客户端只能提交商品引用，不能提交可信业务事实。
- mock-api 商品详情、搜索、评论和库存接口的现状已在 A1 固定。

**实施内容**

1. 定义不可变 `ProductSnapshot`、`ProductFact`、`FactSource` 和 `Freshness` 模型。
2. 快照至少包含 item_id、名称、品类、品牌、当前价格、货币、库存、规格、评分、评论数、配送能力、事实来源和读取时间。
3. 实现批量快照读取适配器；mock-api 没有批量接口时增加只读批量端点，避免 Agent 对大量候选逐条串行读取。
4. 为价格、库存和配送设置单独的新鲜度规则；过期事实保留值但必须标记 stale，不能伪装成当前事实。
5. 缺失字段显式表示为 unknown，禁止使用 `0`、空字符串或通用占位文案冒充真实事实。
6. 一次 Agent turn 生成唯一 snapshot_id，后续模块只能按该 ID 消费，不允许自行重新请求并替换局部字段。

**交付物**

- `services/ai-service/app/routers/AImodel/product_models.py`。
- `services/ai-service/app/routers/AImodel/product_snapshot.py`。
- 必要的 mock-api 只读批量快照端点及其 schema。
- `services/ai-service/tests/test_product_snapshot.py` 和对应 mock-api 测试。

**完成标准**

- 同一 turn 内相同 item_id 只产生一个权威快照版本，所有消费者看到相同值。
- 价格、库存、配送、评分缺失或过期时分别具有明确状态，不能自动填充虚构默认值。
- 批量请求能按输入 item_id 稳定返回，并对不存在商品逐项报告错误，不因单个失败丢弃其他商品。
- 客户端传入的价格、库存和评分不会覆盖服务端快照。
- snapshot_id、source_version 和 captured_at 被写入 Agent Trace，可从最终推荐追溯。
- 快照模型序列化稳定，乱序 API 返回不会改变按 item_id 组织的事实结果。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_product_snapshot.py -q
uv run --project services/mock-api pytest services/mock-api/tests -q
```

**任务边界**

不进行商品过滤、评分和评论总结；不在 ai-service 建立第二份商品主数据。

### C2：实现候选召回与硬过滤 ✔️

**目标**

根据购物目标召回足量候选，并在任何排序之前执行不可妥协的硬条件过滤。被硬条件淘汰的商品不能通过模型偏好或高评分重新进入推荐列表。

**前置依赖**

- B3 能提供无冲突的硬约束集合。
- C1 能批量生成同一 turn 的商品快照。

**实施内容**

1. 实现 `CandidateService`，根据品类、关键词和页面候选构造受控搜索请求，限制查询长度和候选数量。
2. 合并商品搜索、页面候选和用户显式商品引用，按 item_id 去重并记录每个候选的召回来源。
3. 实现纯函数 `apply_hard_filters(goal, snapshots)`，至少支持预算上下限、品牌包含/排除、品类、库存、配送截止时间和配置化必需规格。
4. 为每个淘汰商品返回稳定 reason code、字段、期望值和实际值；不得只返回“条件不符”。
5. 明确边界语义，例如价格等于预算上限是否保留、库存为 unknown 时是淘汰还是请求澄清。
6. 无候选时汇总主要淘汰原因，并给出“放宽哪个条件”的结构化建议，但不能擅自放宽用户硬约束。

**交付物**

- `services/ai-service/app/routers/AImodel/candidate_service.py`。
- `CandidateSet`、`FilteredCandidate` 和 `ExclusionReason` 模型。
- `services/ai-service/tests/test_candidate_filter.py`。
- 候选上限、unknown 处理和过滤策略配置。

**完成标准**

- 同一输入的候选顺序、保留集合和淘汰原因完全一致。
- 预算、品牌排除、品类、库存、配送和必需规格各有等于边界、刚好越界、unknown 三类测试。
- 所有进入下一阶段的候选均通过每一项硬约束；测试中硬约束违反率为 0。
- 多来源候选正确去重且保留来源集合，不因页面候选优先而绕过硬过滤。
- 无候选时返回 `no_candidate` 结果和原因统计，不调用排序器生成“最接近”推荐。
- 搜索或部分快照失败时，失败商品与不匹配商品被区分记录。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_candidate_filter.py -q
```

**任务边界**

不计算软偏好得分，不自动修改预算，不把 RAG 文档中的商品描述作为库存或价格事实。

### C3：建立品类特征归一化 ✔️

**目标**

把不同商品的规格字段转换为同品类可比较的统一特征，明确单位、数值方向和缺失值语义，使排序器不需要理解任意商品文案。

**前置依赖**

- C1 快照能够提供原始规格与品类。
- C2 已产生通过硬过滤的候选集合。

**实施内容**

1. 定义 category profile 配置 schema：字段 key、展示名、数据类型、单位、别名、是否越大越好、允许范围、缺失策略和可用于硬过滤/排序/展示的标记。
2. 为当前 fixtures 中的主要品类提供 profile；未知品类使用只包含通用字段的 fallback profile。
3. 实现数值、布尔、枚举和文本字段归一化，支持常见单位转换并保留原始值。
4. 对冲突单位、无法解析值和超出合理范围的值返回明确 normalization error，不静默转换。
5. 缺失值保持 unknown，并由 profile 决定“不计分、降低置信度或禁止比较”，禁止全局当作零分。
6. 启动或配置加载时校验重复别名、非法范围、未知单位和权重引用字段。

**交付物**

- `services/ai-service/app/routers/AImodel/feature_profiles.yaml`。
- `services/ai-service/app/routers/AImodel/feature_normalizer.py`。
- profile Pydantic 模型和配置加载器。
- `services/ai-service/tests/test_feature_normalizer.py`。

**完成标准**

- 当前主要品类至少各有一个完整 profile 和两个商品归一化 fixtures。
- 等价单位转换后数值一致，原始值和规范值均可追溯。
- unknown、解析失败和真实零值在类型上可区分。
- 新增合法 profile 只需修改 YAML 和测试数据，不修改归一化核心代码。
- 非法 profile 在服务启动或测试加载阶段直接失败，并指出具体字段路径。
- 归一化结果只包含 profile 允许字段，不把任意商品描述自动提升为结构化事实。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_feature_normalizer.py -q
```

**任务边界**

不定义最终排序权重，不使用 LLM 猜测缺失规格，不处理跨品类商品的强行参数对齐。

### C4：实现可解释排序与多样性 ✔️

**目标**

使用确定性、配置化算法对通过硬过滤的候选进行软偏好排序，并输出可解释分项得分。LLM 只能解释排序结果，不能私自改变商品顺序。

**前置依赖**

- C2 已保证输入候选全部满足硬约束。
- C3 已提供归一化特征和缺失语义。

**实施内容**

1. 定义 `RankingPolicy`，包含特征权重、归一化方式、缺失处理、tie-break、多样性规则和策略版本。
2. 依据用户软偏好、价格位置、评分可信度、评论量、配送匹配和品类特征计算分项得分。
3. 权重仅引用 category profile 中允许排序的字段；加载时校验权重范围与总和。
4. 为每个候选输出总分、分项得分、使用/缺失特征和解释代码，不直接生成自然语言营销文案。
5. 定义稳定 tie-break，例如总分、证据完整度、item_id；禁止依赖字典遍历或 API 返回顺序。
6. 实现品牌/型号重复抑制和 top-N 多样性，且多样性调整不得引入硬过滤淘汰商品。
7. 策略版本写入响应和 Trace，支持后续离线对比。

**交付物**

- `services/ai-service/app/routers/AImodel/ranking.py`。
- 排序策略配置及 schema。
- `RankedCandidate`、`ScoreComponent` 模型。
- `services/ai-service/tests/test_product_ranker.py`。

**完成标准**

- 固定输入运行 100 次结果顺序完全一致。
- 排序器拒绝包含未通过硬过滤标记的候选，硬条件不能被任何加权得分抵消。
- 每个推荐结果都有总分和可重算的分项得分，分项总和与总分在定义误差内一致。
- 缺失特征按 profile 策略处理，不能被默认当作 0 或最佳值。
- tie、同品牌集中、候选不足、全部低置信四类场景有独立测试。
- 修改策略配置能够改变预期排序并更新 policy_version，不需修改算法代码。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_product_ranker.py -q
```

**任务边界**

阶段 C 不训练 Learning-to-Rank，不接入商业竞价，不用 LLM 直接输出分数或重排候选；学习排序只允许在阶段 I 的数据、模型和回退门禁下引入。

### C5：实现商品比较和评论洞察 ✔️

**目标**

将 2-5 个候选转换成结构化比较矩阵，并从真实评论中提取有证据的优点、缺点和争议点。最终结论必须区分商品事实、评论观点和模型解释。

**前置依赖**

- C1 提供同一 turn 的商品和评论事实来源。
- C3 提供可比较特征。
- C4 提供候选排序与策略版本。

**实施内容**

1. 构建比较矩阵：行是 profile 允许展示的特征，列是商品，单元格保留规范值、展示值、来源和新鲜度。
2. 只比较同品类或 profile 明确声明兼容的字段；无法比较时输出 unknown/not_applicable。
3. 获取评论列表与统计，按可配置最小样本数提取高频优点、缺点和分歧观点。
4. 评论总结可以使用模型归纳，但输入必须去除指令性内容，输出必须绑定 review_id 列表并通过 schema 校验。
5. 区分“多数评论观点”“少量评论提及”“评论存在明显分歧”，不得把单条评论写成普遍事实。
6. 生成面向 A4 `comparison` 和 `recommendation` payload 的领域对象，不直接拼 Markdown 表格。

**交付物**

- `services/ai-service/app/routers/AImodel/comparison.py`。
- `ComparisonMatrix`、`ReviewInsight`、`EvidenceRef` 模型。
- mock-api 评论只读摘要/批量读取的必要扩展。
- `services/ai-service/tests/test_product_comparison.py`。

**完成标准**

- 2、3、5 个候选均能生成维度正确、列顺序稳定的比较矩阵。
- 每个事实单元格可追溯到 snapshot_id；每条评论洞察至少绑定一个真实 review_id。
- 评论少于配置阈值时明确输出低置信度或不总结，不能补写常见评价。
- 正反观点并存时输出争议标记和双方样本数，不只保留符合推荐结论的评论。
- 评论中的 Prompt Injection 文本被当作数据，不改变总结指令或工具权限。
- 最终推荐理由只能引用比较矩阵、score breakdown 或评论洞察中的证据 ID。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_product_comparison.py -q
uv run --project services/mock-api pytest services/mock-api/tests/test_api.py -q
```

**任务边界**

不生成虚假评论，不做跨品类无意义排名，不把 RAG 文章观点当成商品用户评论。

建议新增模块：

- `services/ai-service/app/routers/AImodel/product_models.py`
- `services/ai-service/app/routers/AImodel/candidate_service.py`
- `services/ai-service/app/routers/AImodel/feature_profiles.yaml`
- `services/ai-service/app/routers/AImodel/ranking.py`
- `services/ai-service/app/routers/AImodel/comparison.py`

必要的 mock-api 扩展：提供批量商品详情、评论只读摘要和事实更新时间；不在 ai-service 内复制商品事实。

阶段退出条件：给定相同目标和商品快照，过滤、排序、比较结果完全可复现；所有推荐商品满足硬约束。

## 9. 阶段 D：Agent 编排与校验

阶段目标：让 Agent 能完成跨商品搜索、详情、评论和知识检索的多步任务，同时保留严格工具边界。

### D1：定义任务计划协议

**目标**

用类型化计划替代模型隐式自由推理，明确一次请求要完成哪些步骤、每步依赖什么输入、允许调用什么能力、产出什么结果以及何时必须停止。

**前置依赖**

- B 阶段已提供购物目标和澄清结果。
- C 阶段已提供候选、过滤、排序和比较领域能力。
- A5 Trace 可以记录计划与步骤事件。

**实施内容**

1. 定义 `AgentPlan`、`PlanStep`、`StepType`、`StepDependency`、`ExecutionBudget` 和 `StopReason`。
2. 步骤类型限制为已实现能力，例如 clarify、product_search、snapshot、review_fetch、rag_lookup、filter、rank、compare、compose 和 action_preview。
3. 每个步骤声明 step_id、依赖、输入引用、预期输出类型、风险级别、允许工具和超时。
4. 校验计划为无环 DAG，所有依赖存在，输出引用类型匹配，写操作必须位于 action 类型步骤。
5. 限制最大步骤数、最大候选数、最大并发、单步超时、总超时、模型调用次数和受控重试次数。
6. 计划验证失败时返回明确错误，不能降级为向模型开放全部工具。

**交付物**

- `services/ai-service/app/routers/AImodel/planner.py` 中的计划模型或独立 `plan_models.py`。
- 计划校验器和预算配置。
- `services/ai-service/tests/test_agent_plan.py`。
- `docs/agent_plan_contract.md`。

**完成标准**

- 合法的搜索、比较、知识问答和动作预览计划均能通过 schema 与 DAG 校验。
- 循环依赖、缺失依赖、重复 step_id、未知 step type、越界预算和不匹配输出引用均被拒绝。
- 任何计划都不能通过自然语言字段新增工具名或绕过风险级别。
- 计划及预算摘要完整写入 Trace，但不记录模型隐藏思维链。
- 计划序列化与恢复稳定，输入顺序不影响拓扑结果。
- 所有预算都有安全默认值和配置上限，客户端不能覆盖服务端上限。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_agent_plan.py -q
```

**任务边界**

不执行计划，不决定商品排序，不暴露 chain-of-thought；只定义计划契约和静态校验。

### D2：实现分层规划器

**目标**

根据 Intent Router、购物目标、页面上下文和缺失槽位生成 D1 计划。简单请求使用确定性模板，复杂请求允许模型在受控步骤集合中选择组合，避免所有请求都进入昂贵的通用 Agent 循环。

**前置依赖**

- D1 计划 schema 和预算校验完成。
- B4 能明确返回“需要澄清”或“可进入决策”。
- C 阶段公开的领域能力具有稳定输入输出。

**实施内容**

1. 先分类为 direct、clarify、knowledge、product_detail、product_search、compare、recommend 和 action_preview。
2. 为 direct、clarify、单商品详情和纯 RAG 问答提供固定 fast path 模板。
3. 为比较和推荐任务生成搜索 -> 快照/评论 -> 过滤 -> 排序/比较 -> 校验 -> 组合响应步骤。
4. 模型只返回 D1 允许的结构化步骤，解析失败最多修复一次，仍失败时进入受控 fallback。
5. 计划生成必须参考已解析目标，不允许模型重新解释并覆盖硬约束。
6. 在 Trace 中记录任务类型、模板/模型来源、计划摘要、预算和 fallback 原因。

**交付物**

- `services/ai-service/app/routers/AImodel/planner.py` 的规划服务。
- fast path 计划模板。
- `services/ai-service/tests/test_aimodel_planner.py`。
- 至少 30 个规划 fixtures，覆盖单轮、多轮和错误输入。

**完成标准**

- 缺少关键槽位时只生成 clarify 计划，不提前生成搜索与推荐步骤。
- 单商品详情和纯知识问答不出现无关搜索、排序或评论步骤。
- 标准比较任务的依赖顺序正确，详情和评论步骤可以并行，过滤先于排序。
- 非法模型计划最多修复一次，不形成循环，不退回“全部工具可用”。
- 相同结构化输入在确定性模板路径上生成相同计划。
- 规划 fixtures 能统计任务分类准确率、平均步骤数和 fallback 率。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_aimodel_planner.py -q
```

**任务边界**

不执行工具，不生成最终回答，不为了展示复杂度给简单任务增加步骤。

### D3：实现步骤级工具授权

**目标**

在每个计划步骤执行前重新验证工具、参数、用户范围、风险和确认条件。Intent Router 继续作为入口门禁，StepPolicyGate 负责更细粒度的运行时授权。

**前置依赖**

- D1 每个 PlanStep 已声明允许工具和风险级别。
- 现有 `_agent_tools_for_intent_route()` 行为已由 A1 回归保护。

**实施内容**

1. 定义 `StepPolicyGate.authorize(plan, step, tool_call, context)` 和结构化 allow/deny 结果。
2. 建立 step type 到允许工具的服务端映射，计划中出现工具名不代表自动获权。
3. 校验工具参数 schema、字符串长度、item_id 来源、user_id/conversation_id 绑定和 URL 安全规则。
4. 区分只读与写操作；写操作除计划授权外还必须通过 E4 确认令牌验证。
5. 用户消息、商品描述、评论、RAG 文档和模型输出均不能修改授权映射或风险级别。
6. 所有拒绝记录稳定 reason code 和脱敏参数摘要，进入 Trace。

**交付物**

- `services/ai-service/app/routers/AImodel/tool_policy.py`。
- 所有 Agent 工具的类型化入参与公共结果 schema。
- `services/ai-service/tests/test_tool_policy_gate.py`。
- 工具权限矩阵文档。

**完成标准**

- 当前步骤未授权的工具即使被模型请求也不会执行，HTTP fake client 能证明没有外部调用。
- 工具名正确但参数越权、item_id 不在候选集、user_id 不匹配或 URL 指向内网时被拒绝。
- direct/refuse 计划无法调用任何业务工具；RAG 步骤不能调用购物车写接口。
- Prompt Injection 样本无法扩大 allowed_tools 或改变 read/write 分类。
- deny 结果包含稳定 code、step_id 和 tool_name，但不向用户泄露内部策略细节。
- 现有 Intent Router 工具白名单测试继续通过。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_tool_policy_gate.py services/ai-service/tests/test_aimodel_agent.py -q
```

**任务边界**

不在策略层实现工具业务逻辑，不允许“记录后继续执行”的软拒绝。

### D4：实现有界并行执行

**目标**

按 D1 依赖图执行计划，并对商品详情、评论等互不依赖的只读步骤进行有界并发。执行器必须统一管理超时、取消、重试和部分失败，避免无限 Agent 循环。

**前置依赖**

- D2 能生成通过校验的计划。
- D3 能在每次调用前给出授权结果。

**实施内容**

1. 实现 DAG executor，只调度依赖已成功或允许降级的步骤。
2. 使用服务端配置限制最大并发、单步超时和总 deadline；客户端断开时取消尚未开始和可取消步骤。
3. 只对幂等只读工具进行有限重试，并使用退避；写工具和 schema 错误不得自动重试。
4. 并行结果按 plan step 顺序归并，不能因完成先后改变后续排序输入。
5. 为 success、failed、timed_out、cancelled、skipped 定义明确结果；部分失败保留成功结果及降级原因。
6. `rag_tool` 仍只调用一次，其多 collection 并行由 RAG 内部负责，执行器不拆分成多个 RAG 请求。

**交付物**

- `services/ai-service/app/routers/AImodel/tool_executor.py`。
- `StepExecutionResult` 和执行上下文模型。
- `services/ai-service/tests/test_parallel_tool_executor.py`。
- 并发、超时和重试配置示例。

**完成标准**

- fake tools 证明两个无依赖步骤真实并发执行，存在依赖的步骤严格等待。
- 最大并发永远不超过配置值，总 deadline 到达后没有新步骤启动。
- 一个详情工具失败不会删除其他成功商品；下游只接收类型正确的成功结果和显式失败摘要。
- 结果归并顺序在不同 fake 延迟下保持一致。
- 客户端取消、单步超时、总超时和一次安全重试都有确定性测试，无残留后台任务。
- 单次计划中 RAG client 调用次数不超过 1，重复引用复用本轮缓存。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_parallel_tool_executor.py services/ai-service/tests/test_aimodel_rag_tool.py -q
```

**任务边界**

不修改 RAG 内部并行策略，不并发执行带副作用步骤，不无限重试恢复外部服务。

### D5：实现事实校验与恢复策略

**目标**

在最终响应发给用户前，验证推荐商品、硬约束、价格库存、比较结论和证据引用是否与本轮领域结果一致。校验失败时只允许一次有边界的重新组合，仍失败则返回安全降级结果。

**前置依赖**

- C1-C5 能提供快照、过滤、排序、比较和证据对象。
- D4 能提供每步成功、失败和超时结果。
- A4 已定义 fallback 响应。

**实施内容**

1. 实现 `GroundingVerifier`，输入结构化响应草稿、ShoppingGoal、ProductSnapshot、FilterResult、RankResult 和 EvidenceRef。
2. 校验所有商品 ID 存在于本轮候选，推荐结果未被硬过滤淘汰，展示价格/库存与快照一致。
3. 校验推荐理由中的结构化 claim 引用有效 evidence_id，比较结论与矩阵单元格一致。
4. 禁止模型输出工具未返回的优惠、库存、配送承诺、销量和绝对化结论。
5. 第一次校验失败时把错误 code 反馈给 response composer 重新生成一次；第二次失败直接输出受控 fallback。
6. 记录校验项、失败原因、修复次数和最终结论到 Trace，不保存隐藏思维链。

**交付物**

- `services/ai-service/app/routers/AImodel/verifier.py`。
- `VerificationResult`、`ClaimCheck` 和稳定错误码。
- `services/ai-service/tests/test_agent_verifier.py`。
- 校验规则文档和允许 claim 类型清单。

**完成标准**

- 不存在商品、被过滤商品、旧价格、虚构库存、无证据结论和矩阵矛盾分别被检测。
- verifier 为确定性代码；相同结构化输入得到相同结果，不依赖 LLM Judge。
- 修复最多执行一次，测试能证明不会递归调用或形成 Agent 循环。
- 无法验证的关键信息从最终回答删除或标为未知，不能被语言润色掩盖。
- fallback 仍保留已验证的部分结果和下一步建议，不输出内部错误堆栈。
- A2 中所有硬约束场景经过 verifier 后违反率为 0。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_agent_verifier.py services/ai-service/tests/test_candidate_filter.py services/ai-service/tests/test_product_comparison.py -q
```

**任务边界**

不重新搜索商品，不放宽硬约束，不把 LLM 自我评价作为事实验证依据。

建议新增模块：

- `services/ai-service/app/routers/AImodel/planner.py`
- `services/ai-service/app/routers/AImodel/tool_policy.py`
- `services/ai-service/app/routers/AImodel/tool_executor.py`
- `services/ai-service/app/routers/AImodel/verifier.py`

重点修改：

- 将 `service.py` 中单一意图到单一工具族的映射改为入口门禁，后续工具必须由 `AgentPlan + StepPolicyGate` 授权。
- 保持 `rag_tool` 使用原始用户问题和本轮缓存；商品检索、批量详情及评论步骤可以使用有来源记录的派生查询。

阶段退出条件（M1）：用户可通过自然语言完成“提出需求 -> 必要追问 -> 候选过滤 -> 商品比较 -> 推荐解释”的完整闭环，且无前端页面上下文也能工作。

## 10. 阶段 E：主动服务与反馈

阶段目标：从被动聊天升级为理解用户所处购物页面、支持受控动作并能采集效果反馈的试运行版本。

### E1：实现上下文组装器

**目标**

把 API 调用方提供的页面引用、服务端商品事实、购物车、会话目标和近期交互组合成一个受控 `AgentContext`。上下文必须有明确来源、优先级和 token 预算，不能把调用方 JSON 或整页内容直接塞入 Prompt。

**前置依赖**

- A3 请求上下文协议已经上线并兼容旧 API 调用方。
- B5 可以加载会话购物目标。
- C1 可以根据商品 ID 重新获取服务端事实快照。

**实施内容**

1. 定义 `AgentContext`、`ContextSource` 和 `ContextFreshness`，区分调用方引用、服务端事实、会话状态和长期偏好。
2. 根据 page_type 处理搜索页、商品详情页、购物车页和普通页面；未知页面只保留安全通用字段。
3. 对当前商品和候选 item_id 重新读取服务端快照；调用方提交的名称、价格、库存、评分只可用于日志对比，不能作为事实。
4. 按明确优先级合并：本轮用户明确表达 > 服务端当前事实 > 当前页面引用 > 会话目标 > 长期偏好。
5. 对候选数量、购物车条目、历史事件、文本长度和估算 token 设置预算，超限时按规则裁剪并记录 dropped counts。
6. 删除认证信息、地址、手机号等非决策必需数据，Trace 只记录上下文类型和引用 ID。

**交付物**

- `services/ai-service/app/routers/AImodel/context.py`。
- `AgentContext` schema 和上下文预算配置。
- `services/ai-service/tests/test_context_assembler.py`。
- 页面上下文 OpenAPI/JSON Schema fixture 和后端契约测试。

**完成标准**

- 搜索页、详情页、购物车页和无页面上下文四种路径均能生成合法 AgentContext。
- 调用方伪造价格、库存或评分不会进入权威事实字段，测试能证明服务端重新读取。
- 上下文合并优先级在冲突样本中稳定，用户本轮明确条件不会被长期偏好覆盖。
- 超过候选和 token 预算时按确定性顺序裁剪，并在 Trace 中记录裁剪数量。
- AgentContext 不包含地址、手机号、Authorization header 或未清洗整页 HTML。
- 任一可选上下文源不可用时仍能使用其余来源完成普通聊天或给出明确降级。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_context_assembler.py -q
```

**任务边界**

不在本任务决定何时主动弹出 Agent，不生成推荐，不保存完整页面浏览历史。

### E2：实现主动导购触发策略

**目标**

在用户明显需要决策帮助时提供一次轻量建议，同时严格控制打扰频率。主动触发由可解释规则决定，不允许模型持续监听并自行弹窗。

**前置依赖**

- E1 能生成受控页面和会话上下文。
- E5 的事件 schema 可以先定义接口或由本任务提供最小事件记录，完整持久化在 E5 完成。

**实施内容**

1. 定义可配置触发场景，例如 API 事件表明同品类反复查看、比较两个以上商品、搜索无结果、长时间停留且未加购、购物车存在相似候选。
2. 每条规则声明 rule_id、必要信号、排除条件、冷却时间、单会话上限和建议模板类型。
3. 明确不打扰条件：用户关闭、当前正在输入、已进入结算、近期已触发、页面上下文不足或用户设置禁用。
4. 服务端只返回 `proactive_suggestion` 决策和轻量模板，不在未打开对话时启动完整推荐链路。
5. 定义调用方聚合事件协议，不接收整段浏览历史；当前任务只提供后端接收与策略测试，不实现任何消费端上报代码。
6. 同一 rule + context fingerprint 在冷却期内幂等，重复调用不能重复触发。

**交付物**

- `services/ai-service/app/routers/AImodel/proactive_policy.py`。
- 主动触发规则配置和 schema。
- `services/ai-service/tests/test_proactive_policy.py`。
- 主动触发输入/输出 OpenAPI fixture 和调用方模拟器测试。

**完成标准**

- 每条主动规则都有至少一个触发样本和两个不触发样本。
- 用户 dismiss 后当前会话不再主动出现；新会话是否恢复由明确配置决定。
- 同一上下文重复请求、重复事件和并发判断不会产生多次 suggestion。
- 结算页、输入中、上下文不足和冷却期内始终不触发。
- 策略结果包含 rule_id、context fingerprint、原因和过期时间，可在 Trace/反馈事件中关联。
- 关闭主动导购配置后，服务端不返回主动提示，但被动聊天仍正常。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_proactive_policy.py -q
```

**任务边界**

不做基于转化率的在线学习，不自动把商品加入购物车，不在后台持续调用大模型。

### E3：接通结构化 SSE 后端链路

**目标**

把 A4 的结构化响应接入 SSE 与历史消息恢复，定义澄清选项回传、幂等消费和断流恢复协议，同时保留文本 fallback。本任务只保证后端协议可被消费，不实现任何页面渲染或交互组件。

**前置依赖**

- A4 结构化响应 schema 已冻结。
- D5 保证进入最终响应的内容已经过事实校验。
- E1 能随请求提供页面上下文。

**实施内容**

1. 统一 SSE 事件：`status`、`delta`、`structured_delta`（如确有必要）、`done` 和 `error`，每个事件包含 request_id 和递增 sequence。
2. `done` 事件携带唯一、完整且通过 schema 校验的结构化响应；文本 delta 仅用于 `answer` 展示。
3. 定义 `ClarificationSelectionRequest`，包含 conversation_id、goal_id、slot_key、option_value、response_version 和幂等键；后端只接受 A4 响应中实际签发的稳定 option value。
4. 定义断流、重复事件、乱序 sequence、调用取消和历史消息恢复语义；同一 request_id 的 done 在服务端只落库一次。
5. 对商品链接和证据 URL 执行允许协议与域名校验，不允许响应 payload 注入脚本、data/file 协议或任意内部地址。
6. 生成核心 response type、澄清回传和 SSE 事件的 OpenAPI/JSON Schema fixture，并用无 UI 的消费方模拟器验证解析与恢复。

**交付物**

- 更新 `service.py` 的 SSE 组装与错误处理。
- 澄清选项回传 API、幂等存储和 schema。
- `services/ai-service/tests/test_aimodel_structured_sse.py`。
- OpenAPI/JSON Schema fixture 与无 UI 消费方模拟器。

**完成标准**

- A4 规定的澄清、商品列表、比较、推荐和 fallback 均能由无 UI 消费方模拟器按判别字段解析。
- 旧版只有 answer/recommended_links 的响应保持兼容；未知 response_type 可由协议定义的文本 fallback 处理。
- 重复 done、乱序 sequence、断线重连和调用取消不会重复落库、重复反馈或重复触发动作。
- 结构化 payload 校验失败时 SSE 返回受控 error 或完整文本 fallback，不发送半成品结构化对象。
- 合法澄清回传能够继续同一 goal；伪造、过期、跨用户或重复 option value 被拒绝。
- URL 安全测试覆盖 javascript/data/file、回环地址、内网地址、超长 URL 和未知协议。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_aimodel_agent.py services/ai-service/tests/test_aimodel_response_schema.py services/ai-service/tests/test_aimodel_structured_sse.py -q
```

**任务边界**

不实现任何前端页面或组件，不把 Markdown 解析作为结构化协议，不在 API 适配层实现业务排序。

### E4：实现确认式加购

**目标**

允许 Agent 在用户明确要求时生成加购预览，并且只有收到有效二次确认后才调用购物车写接口。任何模型输出、重复请求或过期令牌都不能绕过确认。

**前置依赖**

- D3 已把购物车写工具标记为高风险、默认拒绝。
- E3 能通过 SSE 返回合法 `action_preview`，并已定义独立确认请求契约。
- C1 可在执行前重新读取商品价格和库存。

**实施内容**

1. 定义 `ActionPreview`，包含 action_type、item_id、商品名、数量、当前价格摘要、预计影响、expires_at 和 confirmation_token。
2. confirmation token 由服务端生成并签名或保存在服务端，绑定 user_id、conversation_id、item_id、数量、action_type、snapshot_id 和有效期。
3. 提供确认入口；验证 token、用户、会话、动作、有效期和幂等键后才调用 mock-api `/cart`。
4. 执行前重新检查商品存在、库存和价格；发生变化时旧 token 失效并返回新的预览，不能静默使用旧条件。
5. 使用幂等键保证相同确认请求最多产生一次业务写入；网络重试返回第一次结果。
6. 记录 preview、confirm、expired、rejected 和 executed 事件，但不在日志中保存完整 token。

**交付物**

- `services/ai-service/app/routers/AImodel/action_guard.py`。
- 购物车只读/写工具适配及确认 API。
- 安全配置示例，例如 token secret、TTL 和幂等保留时间。
- `services/ai-service/tests/test_cart_action_guard.py`。

**完成标准**

- 没有 token、token 被篡改、已过期、user/conversation/item/quantity 不匹配时，mock-api 写接口调用次数为 0。
- 有效确认恰好执行一次；相同幂等键重复提交返回同一结果且购物车数量不重复增加。
- 价格或库存从预览后发生变化时不执行旧动作，并返回明确 change code 和新预览要求。
- 模型无法自行构造 confirmation token；token 不出现在 Trace summary、应用日志或历史消息正文。
- 执行结果使用 mock-api 返回的权威购物车内容，不根据模型预计结果生成。
- 确认失败不破坏会话目标，用户可以取消或重新预览。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_cart_action_guard.py -q
uv run --project services/mock-api pytest services/mock-api/tests/test_api.py -q
```

**任务边界**

当前只实现加购，不实现下单、付款、退款或删除购物车；不允许通过自然语言“我确认了”跳过结构化 token 验证。

### E5：建立行为反馈与归因

**目标**

建立从 Agent 决策到用户行为的可追溯事件链，为后续策略评估提供真实数据。当前阶段只采集和校验，不直接用事件自动调整排序权重。

**前置依赖**

- A5 提供 trace_id 和请求阶段事件。
- B5 提供 goal_id。
- C4 提供 ranking policy version。
- E2-E4 已定义主动提示、结构化交互和加购事件。

**实施内容**

1. 定义事件 schema：event_id、event_type、occurred_at、received_at、user_id、conversation_id、goal_id、trace_id、request_id、candidate_set_id、item_id、position、surface_id、strategy_mode、policy_version、model_version、feature_version、fact_snapshot_id、metadata_version 和 source/trust_level。
2. 区分服务端 `recommendation_served` 与调用方确认的 `impression_rendered`；served 只表示已发送响应，不能作为训练曝光。服务端在最终响应中签发绑定 request/candidate/item/position/surface/expiry 的 render_token，API 消费方实际呈现后通过反馈接口回执。
3. 支持 clarification_shown、clarification_selected、product_click、compare_open、suggestion_dismiss、action_preview、add_to_cart 和 purchase_proxy；行为事件必须引用有效的 impression_rendered 或明确标记 orphan。
4. 反馈 API 必须同时校验调用方认证/会话权限、事件必填字段、render_token、主体、候选集合、位置和版本；render_token 只证明服务端签发过候选，不替代调用方身份校验。当前任务只实现反馈 API、签名和无 UI 调用方模拟测试，不实现任何前端发送代码。
5. 使用 event_id 或业务幂等键去重，调用方重发不能重复计数；同一 render_token 的相同 item/position 只能确认一次。
6. 存储事件并提供只读聚合：按 scenario/trace/policy/model/candidate_set 计算 served、rendered、点击、澄清、加购和 dismiss 数量。
7. metadata 采用白名单，不接收任意用户原文、地址、设备指纹或完整页面内容；purchase_proxy 明确标记为代理指标，不能在报告中表述为真实支付转化。

**交付物**

- `services/ai-service/app/routers/AImodel/feedback.py`。
- feedback API、存储表和内存测试实现。
- `services/ai-service/tests/test_agent_feedback.py`。
- render_token 签发/校验器、反馈 OpenAPI fixture 和无 UI 调用方模拟器。
- `docs/agent_feedback_dictionary.md`。

**完成标准**

- 每种事件均有合法、缺字段、非法关联和重复上报测试。
- 相同 event_id 重复发送只保留一条记录，聚合结果不重复计数。
- recommendation_served 不计入 impression；只有携带有效 render_token 的 impression_rendered 才能成为 I3 曝光样本。
- product_click、compare_open 和 add_to_cart 能追溯到同一 trace、goal、candidate_set、展示位置和策略/模型/特征/事实版本。
- 不存在对应 impression_rendered 的点击事件按明确规则拒绝或标记 orphan，不能静默正常归因。
- render_token 被篡改、过期、跨用户、跨 request、item/position 不匹配或重复确认时不会生成有效曝光。
- 未认证或无对应用户会话权限的调用方即使持有 render_token 也不能确认曝光或行为事件。
- 存储内容不包含聊天全文、地址、手机号、token 或不受控 metadata。
- 聚合接口明确区分真实事件和 purchase_proxy，不输出模拟转化率结论。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_agent_feedback.py -q
```

**任务边界**

不训练在线模型，不自动修改排序策略，不接入第三方埋点平台或真实支付数据。

建议新增后端模块：

- `services/ai-service/app/routers/AImodel/context.py`
- `services/ai-service/app/routers/AImodel/proactive_policy.py`
- `services/ai-service/app/routers/AImodel/action_guard.py`
- `services/ai-service/app/routers/AImodel/feedback.py`

阶段退出条件（M2）：API 调用方提供页面引用后无需在消息正文重复描述当前商品；主动建议可由服务端配置关闭；加购必须通过预览与确认两个后端请求；所有关键交互和有效曝光可追溯到同一 Agent Trace。

## 11. 阶段 F：安全、评测与发布

阶段目标：用任务级指标、攻击样本、故障注入和性能预算约束 Agent，形成可重复的发布流程。

### F1：加固内容与工具安全

**目标**

明确系统指令、用户输入、商品/评论/RAG 内容和工具结果的信任边界，确保外部文本只能作为数据使用，不能改变计划、授权、确认和最终事实校验规则。

**前置依赖**

- D3 工具授权和 D5 事实校验已经接入主链路。
- E1 上下文和 E5 反馈字段已经定义数据最小化边界。

**实施内容**

1. 编写 Agent threat model，覆盖 Prompt Injection、工具参数注入、SSRF、越权 user_id、恶意链接、数据外泄和确认绕过。
2. 对商品描述、评论、网页摘要和 RAG 文档标记 untrusted content，使用结构化字段传递而非拼入系统规则段。
3. 为所有工具参数和结果建立字段白名单、长度限制、URL/协议限制和 Pydantic 校验。
4. 保留并扩展 Tavily URL 安全、商品链接 item_id 提取和内部地址拒绝规则。
5. 对日志、Trace、错误响应和历史消息执行敏感信息清洗，避免 token、密钥和用户隐私落盘。
6. 安全检测失败时 fail closed：拒绝危险工具调用或降级回答，不能只打日志后继续。

**交付物**

- `docs/agent_threat_model.md`。
- `services/ai-service/app/routers/AImodel/safety.py` 或现有边界模块的集中校验逻辑。
- `services/ai-service/tests/test_agent_security.py`。
- `fixtures/evals/shopping_agent_adversarial.json` 的首批攻击样本。

**完成标准**

- 商品描述、评论、RAG 内容和网页摘要中的“忽略系统规则/调用某工具/输出密钥”均无法改变 allowed_tools 和计划。
- 内网 URL、file 协议、回环地址、超长参数、未知字段和跨用户 conversation_id 均被拒绝。
- 加购确认无法通过自然语言、伪造 token 或重放其他用户 token 绕过。
- 测试日志和 Trace 扫描不出现配置密钥、Authorization header、完整 token、手机号或地址。
- 安全拒绝返回稳定公开错误，不暴露策略实现、堆栈和敏感参数。
- threat model 中每个高风险威胁至少映射一个自动化测试或明确的剩余风险说明。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_agent_security.py services/ai-service/tests/test_tool_policy_gate.py services/ai-service/tests/test_cart_action_guard.py -q
```

**任务边界**

不宣称通过关键词过滤即可消除所有 Prompt Injection；不引入与当前 Agent 无关的全平台 IAM 重构。

### F2：建立故障与对抗场景

**目标**

系统化验证模型、RAG、商品 API、数据库、SSE 和用户输入在异常情况下的行为，使故障表现可预测、可恢复且不产生错误事实。

**前置依赖**

- D4 已统一步骤状态、超时和重试。
- D5 已提供失败后的事实校验与 fallback。
- F1 已定义安全威胁和拒绝规则。

**实施内容**

1. 建立 failure matrix，维度至少包含依赖、故障类型、注入位置、预期步骤状态、用户响应、是否重试和 Trace 事件。
2. 覆盖模型超时/非法 JSON、RAG 超时/空结果、商品搜索 503、部分详情失败、评论冲突、数据库写失败、旧价格、无候选、约束无解、SSE 中断和客户端取消。
3. 使用 fake client、mock transport 和可控时钟实现确定性故障注入，不依赖真实外部服务随机失败。
4. 为每种故障规定降级行为：继续部分结果、请求澄清、返回 fallback 或安全终止。
5. 验证错误预算和重试次数，任何故障都不能形成无限循环或重复写操作。
6. 保存失败 scenario_id、trace_id、公开错误和内部根因映射，便于复现。

**交付物**

- `services/ai-service/tests/test_agent_failure_matrix.py`。
- `fixtures/evals/shopping_agent_failures.json`。
- fault injection helpers。
- `docs/agent_failure_policy.md`。

**完成标准**

- failure matrix 中每一行都有自动化测试，不能只有文档描述。
- 单个只读依赖失败时，已验证的其他结果被保留；关键事实源失败时不生成推荐事实。
- 写操作超时不能盲目重试，必须通过幂等查询确认结果或返回待确认状态。
- 所有重试次数和总耗时受 D1 预算限制，测试能证明没有无限循环。
- 用户响应不包含内部堆栈和工具 JSON，Trace 能定位具体失败步骤和根因类型。
- 连续执行测试结果稳定，不依赖 sleeps 或真实网络时序。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_agent_failure_matrix.py -q
```

**任务边界**

不要求在单机 mock 环境模拟所有生产基础设施事故，不通过放宽正确性规则提高“恢复率”。

### F3：扩充并冻结 Golden Set v2

**目标**

把 A2 开发集扩充为可用于发布门禁的冻结评测集，建立确定性指标计算、失败样本归档和有限人工复核流程。评测结果必须能复现，不能只给总体平均分。

**前置依赖**

- A2 v1 schema 已稳定。
- B-F2 的新响应、错误码和安全场景已经可表达。
- 第 4 节 M3-01 至 M3-10 已有明确计算输入；M4-M6 指标不属于 F3/F5 门禁。

**实施内容**

1. 扩充到不少于 100 条，按需求抽取、澄清、过滤、排序、比较、评论、知识、主动服务、动作确认、安全和故障分类。
2. 划分 development 与 frozen release 集；release 集一旦冻结只能新增版本，不能为适配实现直接修改期望。
3. 实现确定性评分：任务成功、硬约束违反、商品事实准确、证据覆盖、澄清召回、schema 合法、越权调用和故障恢复。
4. LLM Judge 只用于语言质量等补充维度，必须固定模型/Prompt/版本并与硬指标分开报告。
5. 对自动评分无法判断的样本建立人工复核表，至少双人或两次独立复核不一致时记录争议。
6. 生成总体、分类、失败原因和版本对比报告，保留 scenario_id 与 trace_id。

**交付物**

- 冻结版 `fixtures/evals/shopping_agent_scenarios.json` 和版本元数据。
- `scripts/evaluate_shopping_agent.py`。
- 评分器单元测试与人工复核模板。
- 机器可读 JSON 报告和 Markdown 摘要。

**完成标准**

- 有效场景不少于 100 条；需求抽取、澄清、过滤、排序、比较、评论、知识、主动服务、动作确认、安全和故障 11 个一级分类各不少于 8 条，frozen 集有版本和内容 hash。
- M3-01 至 M3-10 均由代码计算，公式、分母、跳过条件和失败判定有单元测试。
- 同一输出文件重复评分结果一致；评分不需要访问未固定的外部服务。
- 报告同时展示总体和分类型结果，并列出每个失败 scenario_id、断言、实际值和 trace_id。
- 修改评测期望必须提升数据集版本并留下变更说明，不能覆盖冻结文件后继续沿用旧版本号。
- LLM Judge 失败不会影响硬指标计算，且其分数不用于掩盖硬约束失败。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_shopping_agent_evaluation.py -q
uv run --project services/ai-service python scripts/evaluate_shopping_agent.py --dataset fixtures/evals/shopping_agent_scenarios.json
```

**任务边界**

不使用训练数据作为冻结发布集，不以删除失败样本的方式提高指标，不把模拟 purchase_proxy 当真实转化。

### F4：优化延迟、成本与稳定性

**目标**

在不降低第 4 节 M3 指标的前提下，把端到端响应时间控制到可试运行范围，并能明确判断时间和成本消耗发生在规划、工具、RAG、模型还是响应组合阶段。

**前置依赖**

- A1 已记录可比较的原始基线环境。
- A5、D4 已提供阶段耗时和总 deadline。
- F3 能在优化前后运行同一冻结评测集。

**实施内容**

1. 实现固定环境 benchmark，记录 TTFT、完整响应、规划、每类工具、RAG、模型、校验和持久化耗时。
2. 报告 P50、P95、最大值、超时率、失败率、模型调用数、输入输出 token 和单请求估算成本。
3. 优先采用批量商品快照、有界并发、同 turn 缓存、连接复用和 fast path；每项优化需有前后对比。
4. 为外部依赖设置单步超时、总 deadline、熔断和降级规则，配置写入示例配置而非散落硬编码。
5. 缓存 key 必须包含用户隔离、事实版本和策略版本；价格、库存等短 TTL 事实不能使用无限缓存。
6. 优化后重新执行 F3，硬约束、事实准确和证据覆盖不得下降。

**交付物**

- `scripts/benchmark_shopping_agent.py`。
- 性能与预算配置。
- 优化实现及针对缓存、批量、并发、超时的测试。
- `docs/agent_performance_report.md` 和机器可读原始结果。

**完成标准**

- 在文档固定的机器、数据集、并发和模型配置下，完整响应 P95 不超过 25 秒；环境不一致时报告不可直接比较。
- 报告同时包含端到端和各阶段耗时，不能用 RAG 子链路耗时代替用户体验耗时。
- benchmark 至少预热一次并完成 3 轮，报告每轮结果和聚合值，保留失败请求。
- 超时、熔断和缓存行为有确定性测试；跨用户请求不会命中包含私人状态的缓存。
- 优化前后使用同一 F3 数据集版本，所有硬质量指标不下降。
- 未达到目标时报告瓶颈和剩余差距，不能调整计时范围或删除慢样本。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests -q
uv run --project services/ai-service python scripts/benchmark_shopping_agent.py --runs 3
```

**任务边界**

不以关闭校验、减少必要证据、跳过工具或缩小冻结数据集来换取性能数字。

### F5：建立发布门禁与运行手册

**目标**

把代码测试、协议兼容、离线评测、安全、性能和部署配置组合成不可跳过的发布检查，并提供故障定位与回滚步骤。发布结论必须由命令结果和证据产生，而不是根据任务文档中的文字声明。

**前置依赖**

- A1-F4 的交付物、测试和报告均已存在。
- 第 4 节 M3-01 至 M3-10 及固定评测版本已经冻结，不能在发布脚本中临时调整。

**实施内容**

1. 编写一键验证脚本，依次执行 ai-service、mock-api、OpenAPI/JSON Schema 契约测试、后端 API E2E、Docker Compose 配置、F3 评测和 F4 性能检查。
2. 每个门禁返回明确退出码；任一强制项失败时整体失败，脚本不能吞掉错误继续报告成功。
3. 校验必需配置存在且无示例密钥，列出可选依赖缺失时的明确降级影响。
4. 为发布产物记录 commit、配置版本、数据集版本、策略版本、模型配置和报告路径。
5. 编写回滚条件与步骤，覆盖 schema 兼容、Agent 开关、主动导购关闭、结构化响应回退和动作工具禁用。
6. 编写试运行演示剧本和已知限制，包含成功路径与至少两个故障降级路径。

**交付物**

- `scripts/verify_shopping_agent_release.ps1`。
- `docs/agent_release_runbook.md`。
- `docs/agent_known_limitations.md`。
- 统一发布报告目录和证据索引。

**完成标准**

- 一条命令可以从干净环境执行全部强制门禁，并以退出码准确反映成败。
- 人为制造单测失败、评测不达标、配置缺失和性能超限时，发布脚本分别失败且指出具体门禁。
- A-F 共 30 个任务均能在 M3 证据索引中找到对应交付文件、测试命令和最近一次通过结果，不依赖人工勾选声明。
- M3-01 至 M3-10 全部达到目标，失败样本可通过 scenario_id 和 trace_id 定位；F5 不检查尚未建设的 M4-M6 指标。
- 回滚步骤在测试环境至少演练一次，能够关闭新编排链路并恢复旧文本响应而不丢失会话数据。
- 运行手册写明环境前提、启动顺序、验证命令、告警信号、常见故障和责任边界。

**验证命令**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_shopping_agent_release.ps1
```

**任务边界**

不自动发布到真实生产环境，不自动合并分支，不把存在失败门禁的版本标记为可发布。

建议新增资产：

- `fixtures/evals/shopping_agent_scenarios.json`
- `fixtures/evals/shopping_agent_adversarial.json`
- `scripts/evaluate_shopping_agent.py`
- `scripts/benchmark_shopping_agent.py`
- `docs/agent_release_runbook.md`

阶段退出条件（M3）：M3-01 至 M3-10 全部达标；全量后端与 API 契约回归通过；评测报告可复现；所有失败请求能通过 trace_id 定位到目标状态、计划步骤、工具结果和验证结论。M4-M6 指标不阻塞 M3。

## 12. 阶段 G：电商数据与实时画像

阶段目标：在 E5 行为反馈之上建立受授权约束的数据闭环，把可信行为转换成可回放、可过期、可解释的特征与画像；不得把聊天全文、单次点击或匿名设备直接当作永久用户偏好。

### G1：建立身份、授权与数据生命周期边界

**目标**

先定义“谁的数据、因为什么目的、可以使用多久”，为事件、特征和画像提供统一访问边界。任何个性化能力都必须在有效授权内工作，并支持撤回、过期、导出和删除后的确定性行为。

**前置依赖**

- E5 已定义行为事件及 user_id、conversation_id、goal_id 和 trace_id 的归因关系。
- F1 已定义敏感数据、日志清洗和跨用户访问的安全规则。
- F5 的 M3 发布门禁已经通过，现有非个性化链路可作为降级基线。

**实施内容**

1. 定义 `IdentityContext`，区分 authenticated_user、anonymous_session 和 device_session；服务端负责生成主体标识，客户端不得指定其他用户的 subject_id。
2. 定义版本化 `ConsentGrant`：consent_id、subject_id、purpose、data_scopes、source、policy_version、granted_at、expires_at、revoked_at 和 retention_until。
3. 首批 purpose 固定为 shopping_assistance、personalized_ranking、model_training 和 service_quality_analytics；每个 purpose 显式绑定可读事件、特征和输出范围，禁止使用笼统的 all/service_improvement scope，model_training 不得由其他 purpose 隐式推导。
4. 在事件写入、特征读取、画像读取和训练样本导出前统一调用 `ConsentPolicyGate`，拒绝缺失、过期、已撤回或目的不匹配的访问。
5. 制定匿名会话升级规则：只有完成受信身份绑定后才能迁移允许迁移的数据；共享设备和不同登录用户之间默认不合并。
6. 实现导出、撤回和删除流程；删除采用可审计 tombstone，异步清理在线特征、离线事件派生物和训练候选记录，首版最大完成时限为 `24h`。
7. 建立保留期任务和审计事件，记录主体、purpose、scope、策略版本和结果，但不得记录原始聊天、地址、手机号或未脱敏设备标识。
8. 明确已训练模型的删除传播策略：registry 标记受影响 dataset lineage，相关数据不得用于后续训练或继续晋级候选模型；若适用策略要求机器遗忘，则退役受影响模型并从已清理数据重新训练，不能宣称删除原始行等同于模型已遗忘。

**交付物**

- `services/ai-service/app/domains/personalization/identity.py`。
- `services/ai-service/app/domains/personalization/consent.py`。
- `services/ai-service/app/infrastructure/personalization/consent_repository.py`、数据表、迁移和内存测试仓储。
- `services/ai-service/tests/test_consent_policy.py`。
- `docs/agent_data_consent_policy.md`。

**完成标准**

- 每个个性化读取接口在无授权、错误 purpose、过期和撤回四种情况下均 fail closed，且返回稳定错误码。
- 用户 A 的 token、conversation_id 或匿名 session 不能读取、撤回或删除用户 B 的授权和画像。
- 撤回授权后新请求立即停止读取个性化特征；在线/离线派生数据在 `24h` 内完成清理或不可再用于训练，并产生审计证据。
- 匿名转登录仅迁移允许的事件；共享设备切换用户不会自动合并历史偏好。
- 导出结果只包含该主体允许导出的数据和来源说明；删除后重复导出为空或只返回法律要求保留的 tombstone 摘要。
- 删除主体后，受影响 dataset/model lineage 可查询，后续训练不会再次读取该主体数据；配置为需要机器遗忘的场景会阻止受影响模型继续晋级。
- 日志和 Trace 扫描不出现 consent token、原始身份凭证和未脱敏个人信息。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_consent_policy.py -q
```

**任务边界**

不建设全公司的账号中心、SSO 或法律合规平台；不得以“内部优化”为由绕过 purpose 和 retention 校验。

### G2：建立可信行为事件管道

**目标**

把 E5 的同步埋点扩展为可去重、可回放、可处理乱序和坏消息的事件管道，为实时特征和离线训练提供同一份权威输入，同时区分客户端信号与服务端业务事实的可信等级。

**前置依赖**

- G1 已提供主体标识、授权 scope 和数据保留规则。
- E5 的事件字典、幂等键和归因字段已经稳定。
- mock-api 能为加购等服务端行为返回权威结果。

**实施内容**

1. 定义版本化 `BehaviorEventEnvelope`：event_id、event_type、schema_version、subject_id、session_id、occurred_at、received_at、source、trust_level、consent_snapshot_id、purpose、retention_policy_version、trace_id、goal_id、candidate_set_id、item_id、position、surface_id、policy/model/feature/fact version 和 allowlisted payload。
2. 扩展事件类型为 recommendation_served、impression_rendered、click、compare、dismiss、cart、purchase、cancel、return 和 satisfaction；只有有效 render_token 的 impression_rendered 可作为曝光，purchase/return 等高可信事件只能由服务端适配器产生，调用方同名事件不得提升信任等级。
3. 采用 append-only event store 和 outbox/consumer 接口；本地实现允许使用 PostgreSQL worker，但生产接口必须保留替换消息队列的边界，测试不得依赖真实外部 broker。
4. 以 event_id 去重，以 subject_id/session_id 作为有序处理键；明确重复、乱序、迟到、未来时间和未知 schema 的处理规则。
5. 对暂时失败执行有界重试，对不可解析或超过重试上限的事件进入 dead-letter store，并提供重放命令和原失败原因。
6. 在入站和消费阶段使用事件携带的 consent_snapshot_id/purpose 再次执行 consent gate；授权撤回后的迟到事件不得生成新画像或训练特征。
7. 对弱信号实施主体/IP 速率限制、异常突增检测和 quarantine，防止自动点击、事件重放和批量伪造污染画像；被隔离事件不得进入特征或训练集。
8. 输出 lag、throughput、duplicate、late、rejected、quarantined、dead-letter 和 replay 指标，并能通过 trace_id 定位到来源请求。

**交付物**

- `services/ai-service/app/domains/personalization/events.py`。
- `services/ai-service/app/workers/personalization_event_worker.py`。
- `services/ai-service/app/infrastructure/personalization/event_repository.py` 及 event/outbox/quarantine/dead-letter 数据表和迁移。
- `services/ai-service/tests/test_behavior_event_pipeline.py`。
- `scripts/replay_behavior_events.py` 和 `docs/behavior_event_runbook.md`。

**完成标准**

- 同一事件至少投递两次只产生一次有效消费；重启 worker 后不会丢失已提交但未确认的 outbox 事件。
- 冻结乱序事件集在重复执行后得到相同的有序逻辑结果，迟到事件按配置更新或拒绝且记录原因。
- 客户端伪造 purchase、return、价格或用户 ID 会被拒绝，不能形成高可信标签。
- 缺失或不匹配 consent_snapshot_id/purpose 的事件无法生成特征；model_training 样本只能来自具有 model_training purpose 的授权快照。
- 超过速率阈值、签名无效或异常突增的弱信号进入 quarantine，重放相同批次不会改变有效聚合。
- 未知 schema、非法字段和消费异常进入 dead-letter；修复后可按 event_id 范围重放且不重复计数。
- 授权撤回后的事件不会产生新特征，已到期数据不会被重放重新激活。
- 测试能报告输入总数及有效、重复、拒绝、quarantined、dead-letter 五类终态，五类终态之和与输入总数按定义对账一致。
- 冻结负载下事件处理延迟 P95 `<= 60s`，排除主动注入非法事件后的 dead-letter 比例 `<= 0.1%`。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_behavior_event_pipeline.py -q
uv run --project services/ai-service python scripts/replay_behavior_events.py --fixture fixtures/personalization/behavior_events.json --dry-run
```

**任务边界**

不要求当前部署 Kafka/Flink；但不得用进程内队列作为唯一持久化，也不得把客户端上报直接当成支付或退货事实。

### G3：建立特征注册表与在线/离线特征存储

**目标**

用同一份特征定义生成在线推理值和离线训练值，消除训练/服务偏差；所有特征必须声明来源、窗口、时间语义、默认值、过期策略和可用目的。

**前置依赖**

- G2 已提供可回放的权威事件序列和可控测试时钟。
- G1 已提供 purpose、retention 和删除规则。
- B1 与 C3 已定义购物目标和品类特征的字段语义。

**实施内容**

1. 定义版本化 `FeatureDefinition`：feature_key、entity_type、value_type、event_sources、aggregation、window、event_time_policy、freshness_sla、ttl、default_semantics、purpose 和 owner；派生值必须保留 consent_lineage_ref 与 source_event_range。
2. 首批实现品类浏览次数、品牌点击占比、价格带分布、比较频率、加购/退货倾向和最近活跃时间；明确这些都是软信号，不能直接成为硬约束。
3. 建立共享计算函数，同时服务事件增量更新和离线 as_of 回放；离线构造只能看到 as_of 之前已发生且允许使用的事件，防止未来信息泄漏。
4. 实现 online store 与 offline store 适配器；在线侧面向低延迟读取，离线侧保留事件时间、定义版本和计算批次，二者不得复制不同计算公式。
5. 支持幂等 backfill、窗口过期、迟到事件修正和用户删除传播；回填必须按 feature version 生成新批次，不能覆盖旧训练快照。
6. 为每个特征输出 value、computed_at、fresh_until、definition_version、consent_lineage_ref 和 evidence_count；过期、缺失、撤回与真实零值必须可区分。
7. 建立 point-in-time correctness、在线/离线一致性、数据漂移和新鲜度报告。

**交付物**

- `services/ai-service/app/domains/personalization/features.py`。
- `services/ai-service/app/infrastructure/personalization/feature_store.py`。
- `config/agent_feature_registry.yaml`。
- `services/ai-service/tests/test_feature_store_consistency.py`。
- `scripts/backfill_agent_features.py` 和冻结事件夹具。

**完成标准**

- 注册表拒绝重复 key、不兼容类型变更、缺失 owner、无限 retention 和未声明 purpose 的定义。
- 对同一冻结事件和 as_of 时间，在线增量计算与离线回放的键、值、版本和缺失语义 `100%` 一致。
- 测试证明 T+1 的购买/退货事件不会进入 T 时刻训练样本，迟到事件按定义重算受影响窗口。
- 缺失、过期、撤回和真实值 0 在 API 中具有不同状态，调用方不能用同一个数值默认掩盖。
- backfill 重复运行结果幂等；定义升级创建新版本，旧模型仍能读取其绑定的历史版本。
- 删除主体后，在线特征立即不可读，离线派生记录在 `24h` 内清除或不可再用于训练。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_feature_store_consistency.py -q
uv run --project services/ai-service python scripts/backfill_agent_features.py --fixture fixtures/personalization/behavior_events.json --as-of 2026-09-01T00:00:00Z --dry-run
```

**任务边界**

不引入未经使用的通用 Feature Store 平台；不允许训练脚本和在线服务各自维护一套同名特征 SQL 或计算代码。

### G4：建立可解释用户画像服务

**目标**

把授权范围内的长期行为特征转为可解释的购物偏好，并与当前 ShoppingGoal、用户明确声明和匿名会话信号严格分层，避免一次点击永久改变画像或长期偏好覆盖本轮硬约束。

**前置依赖**

- G3 已提供带版本、新鲜度和证据量的特征快照。
- B5 已隔离当前购物目标与长期记忆。
- G1 已定义画像读取目的和删除规则。

**实施内容**

1. 定义 `UserPreference` 和 `UserProfileSnapshot`，每项包含 category_scope、preference_type、value、source_type、confidence、support_count、first_seen_at、last_seen_at、expires_at、reason_code 和 consent_lineage_ref。
2. 分离 explicit preference、inferred preference、negative signal 和 temporary session signal；用户明确设置只能由明确操作修改，不能被行为模型覆盖。
3. 建立置信度、最小证据量、时间衰减和冲突规则；单次浏览、误点和一次退货不得直接形成高置信长期结论。
4. 首版默认规则写入 `agent_profile_policy.yaml`：浏览/点击推断达到高置信至少需要 `5` 个可信信号、跨 `3` 个 session 且覆盖 `>= 7d`；单一来源信号上限为中置信；浏览型推断 TTL `30d`、加购型 `60d`、服务端确认购买型 `180d`，用户明确偏好在用户修改、撤回授权或策略最大保留期到达时失效。
5. 固定决策优先级：本轮硬约束 > 本轮明确偏好 > 用户长期明确偏好 > 高置信推断偏好 > 群体默认；画像只能提供软特征。
6. 提供按 purpose 和 category 裁剪的只读 profile API；返回原因代码和聚合证据数量，不返回原始事件、聊天文本或其他会话内容。
7. 支持用户查看、纠正和删除明确偏好；纠正后旧推断进入 suppressed 状态，不能在下一次特征刷新中立即恢复。
8. 记录 profile_version、feature_versions 和规则版本，使任一次排序可以重建当时使用的画像快照。

**交付物**

- `services/ai-service/app/domains/personalization/profile.py`。
- `services/ai-service/app/infrastructure/personalization/profile_repository.py`。
- `config/agent_profile_policy.yaml`。
- `services/ai-service/tests/test_user_profile_service.py`。
- `docs/agent_profile_semantics.md`。

**完成标准**

- 单次点击和低证据特征不会产生高置信长期偏好；时间推进后推断偏好按规则衰减并最终过期。
- 本轮“预算 3000 元以内”等硬约束不会被长期高价偏好覆盖，冲突时 Trace 显示采用的优先级和 reason_code。
- 用户明确纠正品牌偏好后，被抑制推断不会因旧事件重放自动复活。
- 不同 category_scope 的偏好不会无规则外溢，例如手机尺寸偏好不会应用到家电。
- profile API 在 consent purpose 不匹配时拒绝读取，响应中不包含原始事件或完整聊天内容。
- 给定相同 feature snapshot 和 policy version，画像结果确定且可重复生成。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_user_profile_service.py -q
```

**任务边界**

不推断敏感身份、健康、政治、宗教或家庭成员关系；不把模型生成的性格标签作为推荐特征。

### G5：将实时画像接入 Agent 并建立数据阶段门禁

**目标**

把画像作为受控、可降级的软信号接入 AgentContext、召回和排序输入，同时证明个性化基础设施不会破坏非个性化链路、硬约束、隐私和响应性能。

**前置依赖**

- G1-G4 已通过各自测试并冻结首个 schema/policy version。
- E1 的 AgentContext、C4 的确定性排序和 A5 的 Trace 可扩展。
- F3/F4 提供固定评测与性能基线。

**实施内容**

1. 在 `AgentContext` 增加可选 `profile_snapshot`，只包含当前 category、purpose 和 token/字段预算允许的偏好摘要。
2. 在上下文组装前执行 consent gate，画像读取 deadline 固定为 `200ms`；超时、不可用、过期或无授权时返回 `personalization_unavailable` 并继续非个性化流程。
3. 将画像映射为 C4 可识别的软特征和解释代码；当前 ShoppingGoal 的硬约束与明确偏好保持更高优先级。
4. 在 Trace 中记录 profile_version、feature_versions、freshness、使用/忽略字段和 fallback reason，不记录原始行为明细。
5. 扩展评测集，覆盖新用户、匿名用户、低活跃用户、偏好冲突、授权撤回、画像过期、服务超时和跨品类误用。
6. 增加画像服务 P50/P95、命中率、过期率、fallback 率、授权拒绝数和分群质量报告；无真实数据时不得宣称转化提升。
7. 扩展 threat model，覆盖 consent 伪造、事件重放、机器人点击、画像投毒、跨用户特征读取和删除后重建，并为每项高风险建立自动化测试或明确剩余风险。
8. 将 consent、事件对账、特征一致性、画像确定性、安全增量和降级测试加入 M4 发布脚本。

**交付物**

- E1 Context Assembler 和 C4 RankingPolicy 的画像适配器。
- `services/ai-service/tests/test_agent_personalization_context.py`。
- `fixtures/evals/shopping_agent_personalization.json`。
- `scripts/verify_personalization_data_release.ps1`。
- `docs/personalization_data_release_report.md` 模板。
- `config/agent_quality_gates.yaml` 的版本化 M4 门禁及配置校验测试。
- `docs/personalization_compatibility_manifest.md`，版本化列出相对 M3 唯一允许的响应差异。

**完成标准**

- 无授权、新用户、画像服务超时和 online store 故障时，原有非个性化场景结果与 M3 基线一致；允许的解释文本差异必须预先写入版本化 compatibility manifest，未列入的差异均按回归处理。
- 画像任何字段都不能使硬过滤淘汰项重新进入候选，也不能改变价格、库存、规格等事实。
- 冻结冲突场景全部遵循 G4 优先级，并能从 Trace 重建采用或忽略画像的原因。
- 画像读取 P95 `<= 150ms` 且由 `200ms` deadline 截断，不阻塞完整回答。
- M4-01 至 M4-04 全部达到第 4 节门禁，删除、过期、事件投毒和跨用户测试全部通过。
- 发布报告按新/老用户、授权状态和品类分组，不用总体平均值掩盖某一群体的明显回归。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_agent_personalization_context.py services/ai-service/tests/test_consent_policy.py services/ai-service/tests/test_feature_store_consistency.py -q
powershell -ExecutionPolicy Bypass -File scripts/verify_personalization_data_release.ps1
```

**任务边界**

当前只接入可解释画像软特征，不自动学习排序权重，不开展线上 A/B，不因个性化服务失败而拒绝普通购物问答。

建议新增模块：

- `services/ai-service/app/domains/personalization/identity.py`
- `services/ai-service/app/domains/personalization/consent.py`
- `services/ai-service/app/domains/personalization/events.py`
- `services/ai-service/app/domains/personalization/features.py`
- `services/ai-service/app/domains/personalization/profile.py`
- `services/ai-service/app/infrastructure/personalization/`
- `services/ai-service/app/workers/personalization_event_worker.py`

阶段退出条件（M4）：M4-01 至 M4-04 全部达标；授权撤回立即阻断读取且派生数据 `24h` 内清理；事件可对账、隔离与重放；冻结数据的在线/离线特征完全一致；画像具有授权血缘、来源、置信度和过期时间；画像不可用时 M3 非个性化链路保持可用。

## 13. 阶段 H：商品知识与实时事实

阶段目标：建立跨品类可治理的商品语义和关系层，并把价格、促销、库存、配送等高时效数据统一为带来源与新鲜度的事实快照；知识图谱只能辅助召回和解释，不能取代实时业务事实。

### H1：建立商品本体与版本化属性规范

**目标**

统一商品、SPU、SKU、销售 offer、品牌、品类、属性、单位和关系的语义，使不同数据源及不同品类能够被稳定比较、校验和演进，避免每增加一个品类就在 Prompt 或排序代码中硬编码字段。

**前置依赖**

- C1 已定义 `ProductSnapshot` 的事实边界。
- C3 已存在首批品类 profile、单位归一化和缺失值语义。
- F5 的 M3 schema 兼容规则已经稳定。

**实施内容**

1. 定义 `ProductOntologyVersion`、`CategoryDefinition`、`AttributeDefinition`、`UnitDefinition` 和 `RelationDefinition`，所有对象具有稳定 ID、schema version、owner、有效期和变更说明。
2. 明确 product/SPU、SKU 和 offer 的边界：共享型号属性属于 SPU，颜色/容量等可售变体属于 SKU，价格/促销/卖家/履约属于 offer。
3. 属性规范包含 value_type、unit_dimension、allowed_values、cardinality、required_level、missing_semantics、comparable、filterable 和 display rule。
4. 建立品类继承和属性覆盖规则；子品类只能以兼容方式收窄约束，不能静默改变父属性单位、类型或方向语义。
5. 将 C3 现有 profile 迁移为本体适配器，保持现有规范值和排序行为；旧 schema 仍可按版本读取。
6. 提供本体 lint 和版本差异工具，识别 ID 复用、单位不兼容、枚举删除、必填升级和循环继承等破坏性变更。
7. 为首批核心品类提供真实示例和反例，包括单位转换、未知值、不可比较属性和多值属性。

**交付物**

- `services/ai-service/app/domains/catalog/ontology.py`。
- `config/product_ontology/` 下的版本化品类、属性、单位和关系定义。
- C3 FeatureNormalizer 的本体适配器。
- `services/ai-service/tests/test_product_ontology.py`。
- `scripts/lint_product_ontology.py` 和 `docs/product_ontology_governance.md`。
- 版本化 ontology migration manifest 模板与兼容性校验脚本。

**完成标准**

- 首批核心品类的 C3 规范化回归结果与 M3 冻结基线一致；排序输入变化必须预先写入版本化 ontology migration manifest，未列入的变化均按回归处理。
- lint 能拒绝重复稳定 ID、循环继承、类型/单位不兼容覆盖和无迁移说明的破坏性变更。
- 相同属性在不同来源名称下映射到同一规范 ID；不同语义的同名字段不会被误合并。
- SKU、SPU 和 offer 属性放置错误具有自动校验或明确审核报告，实时价格不允许写入静态 SPU 属性。
- 旧本体版本可重放历史 ProductSnapshot，新版本发布不会修改旧版本内容 hash。
- 新增一个符合模板的品类不需要修改 Agent Prompt 或排序器核心代码。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_product_ontology.py services/ai-service/tests/test_product_feature_normalizer.py -q
uv run --project services/ai-service python scripts/lint_product_ontology.py --root config/product_ontology
```

**任务边界**

不追求一次覆盖全部电商品类，不把营销文案直接当结构化属性，不允许 LLM 在运行时自行创造本体 ID 或单位规则。

### H2：建立商品实体消歧与数据质量治理

**目标**

把 mock-api、RPA、商品库和后续外部来源映射到稳定的 canonical product/SPU/SKU/offer 标识，能够识别重复、冲突和疑似匹配，并对自动合并与人工待审建立可逆审计链。

**前置依赖**

- H1 已冻结首版本体、稳定 ID 和关键属性语义。
- C1 已定义来源、采集时间和 ProductSnapshot 标识。
- 现有商品源能够提供 source、source_item_id 和基础品牌/型号/规格字段。

**实施内容**

1. 定义 `SourceEntityRef`、`CanonicalProductEntity`、`EntityMatchDecision` 和 `MergeAudit`，保留 source ID 与 canonical ID 的双向映射。
2. 建立品牌、型号、条码、容量、颜色和单位的确定性标准化；原始值与规范值并存，不能覆盖来源原文。
3. 先执行稳定键和强规则匹配，再对候选执行可解释相似度评分；每个特征贡献、阈值、规则版本和冲突原因必须可查看。
4. 设置 auto_match、review_required 和 no_match 三段阈值；关键属性冲突时即使总体分高也不得自动合并。
5. 提供 merge、split、alias 和 reject 操作及审计记录；修正映射后重建受影响的图谱边和索引，不修改历史决策快照。
6. 建立数据质量规则：必填缺失、单位异常、枚举越界、品牌/型号冲突、重复 source ID、孤立 SKU 和 offer 无归属。
7. 生成按来源、品类和规则版本分组的匹配精度、待审率、冲突率和质量报告。

**交付物**

- `services/ai-service/app/domains/catalog/entity_resolution.py`。
- canonical entity、source mapping、review queue 和 audit 数据表。
- `services/ai-service/tests/test_product_entity_resolution.py`。
- `fixtures/catalog/entity_resolution_cases.json`。
- `scripts/audit_catalog_entities.py` 和实体审核接口契约。

**完成标准**

- 冻结数据集中相同商品的多来源记录映射到同一 canonical ID，不同容量/颜色 SKU 不被错误合并。
- 含关键冲突、低置信或信息不足的记录全部进入 review_required，不因追求覆盖率自动合并。
- 冻结冲突集的自动误合并为 0，人工抽样 auto_match precision `>= 99.5%`；无法达到时必须收紧阈值并报告待审增长，不能放宽真值定义。
- merge 和 split 可重复执行且结果幂等；split 后旧别名、图谱边和检索索引按新版本更新。
- 任一 canonical entity 可追溯全部来源、匹配规则、审核动作和当前有效版本。
- 数据质量报告的输入总数等于通过、待审、拒绝和错误记录之和。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_product_entity_resolution.py -q
uv run --project services/ai-service python scripts/audit_catalog_entities.py --fixture fixtures/catalog/entity_resolution_cases.json
```

**任务边界**

不让 LLM 直接执行不可逆实体合并；不以标题相似作为 SKU 唯一匹配依据；不删除来源记录来消除冲突。

### H3：建立商品关系图谱与受控查询服务

**目标**

在 canonical entity 之上表达变体、兼容、替代、配件、互补、品牌和品类关系，为候选扩展、比较和解释提供结构化依据，并限制图查询的关系类型、深度、规模和证据要求。

**前置依赖**

- H1 已定义节点类型和关系语义。
- H2 已提供稳定 canonical ID 和可审计映射。
- C5 已定义比较、评论和 EvidenceRef 契约。

**实施内容**

1. 定义 `ProductGraphNode` 和 `ProductGraphEdge`，边包含 relation_type、direction、source_ref、confidence、valid_from、valid_to、ontology_version 和 status。
2. 首批关系限定为 VARIANT_OF、BELONGS_TO_CATEGORY、MADE_BY、COMPATIBLE_WITH、ACCESSORY_FOR、SUBSTITUTE_FOR 和 COMPLEMENTS；每种关系明确方向、对称性和可传递性。
3. 建立确定性图构建器，从本体、canonical mapping 和经审核的兼容数据生成边；低可信 LLM 抽取只能进入 review queue。
4. 实现受控查询 API：按起点、允许关系、方向、最大 hop、最大节点数和 as_of 时间查询，默认一跳，服务端上限固定为 `max_hops=2`、`max_nodes=100`，禁止无界遍历。
5. 为每个返回节点和路径附带边证据与有效期；失效、待审和来源缺失的边不能进入正式推荐解释。
6. 支持增量重建和版本快照；实体 split、关系撤销和本体升级后只更新受影响子图。
7. 建立图质量指标：孤立率、重复边、非法类型组合、无证据边、过期边、查询截断率和关系覆盖率。

**交付物**

- `services/ai-service/app/domains/catalog/product_graph.py`。
- `services/ai-service/app/infrastructure/catalog/product_graph_repository.py` 及 PostgreSQL 邻接表测试实现。
- `services/ai-service/tests/test_product_graph.py`。
- `fixtures/catalog/product_graph_cases.json`。
- `docs/product_graph_contract.md` 和图质量报告脚本。

**完成标准**

- 每种关系的方向、对称和传递规则均有正反测试，非法节点组合无法写入。
- 查询强制执行 allowed_relations、max_hops 和 max_nodes；恶意或超大请求返回稳定截断/拒绝结果。
- 兼容、替代和配件结论均可定位到有效边证据，待审或过期边不会出现在 Agent 响应。
- 同一图版本和 as_of 时间返回确定结果；增量重建不改变未受影响子图的版本 hash。
- 实体 split 后不存在指向已失效 canonical ID 的活跃边。
- PostgreSQL 实现在固定数据规模与 100 并发下查询 P95 `<= 200ms`；没有基准证据时不强制引入独立图数据库。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_product_graph.py -q
```

**任务边界**

不把向量相似度自动声明为替代或兼容关系，不允许 Agent 发出任意图查询语言，不因“知识图谱”名义提前引入未经容量验证的新数据库。

### H4：建立统一电商实时事实网关

**目标**

用一个类型化网关统一读取商品详情、价格、促销、库存、配送和可售状态，为一次决策生成一致的 point-in-time 事实快照，并对来源优先级、新鲜度、部分失败和陈旧数据给出确定性处理。

**前置依赖**

- H2 已提供 canonical SKU/offer 与各来源 ID 的映射。
- C1 已定义不可变 ProductSnapshot 和事实引用。
- mock-api、RPA 及现有商品工具已有可适配的数据契约。

**实施内容**

1. 定义 `CommerceFactRequest`、`CommerceFactContext`、`CommerceFactValue` 和 `CommerceFactSnapshot`。请求显式包含 canonical SKU/offer、seller、channel、region_code、quantity、currency、受控 membership_scope、as_of 和 deadline；客户端不得直接声明会员价、库存或配送承诺。
2. 每个事实值包含 canonical ID、value、source、observed_at、fetched_at、fresh_until、quality_status 和 evidence_ref；为 catalog、price、promotion、inventory 和 delivery 建立独立 source adapter 与权威来源优先级。
3. freshness budget 首版固定为 catalog `24h`、price `5min`、promotion `5min`、inventory `60s`、delivery `5min`；adapter timeout、是否允许 stale fallback 和冲突规则写入版本化配置。
4. 同一请求绑定 fact_snapshot_id 和 as_of；并行来源的 fetched_at 最大偏差为 `5s`，超出时重新读取或把快照标记 conflict。排序、比较和回答只能读取同一快照，不能在步骤间静默刷新部分字段。
5. 区分 fresh、stale、unknown、conflict 和 unavailable；关键事实过期或冲突时触发刷新、降级或停止推荐，不能沿用无标记旧值。
6. 促销计算采用结构化条件、适用 SKU、门槛、数量、会员范围、时间窗和互斥规则；展示到手价必须保留计算明细，不由 LLM 心算或补全。
7. 支持批量读取、并发上限、deadline、熔断和只读缓存；缓存键包含来源、offer、seller、channel、region、quantity、currency、membership scope 和 freshness version，不能跨上下文复用配送或促销结果。
8. 记录 adapter 耗时、命中率、fresh/stale/unknown 比例、冲突率和部分失败；Trace 只保留安全摘要及 EvidenceRef。

**交付物**

- `services/ai-service/app/domains/catalog/fact_gateway.py`。
- `services/ai-service/app/infrastructure/catalog/` 下的 catalog/price/promotion/inventory/delivery adapters。
- `config/commerce_fact_sources.yaml`。
- `services/ai-service/tests/test_commerce_fact_gateway.py`。
- `fixtures/catalog/commerce_fact_cases.json` 和事实新鲜度说明。

**完成标准**

- 一个决策内所有消费者使用同一 fact_snapshot_id；测试中的中途源更新不会造成新旧价格或库存混用。
- fetched_at 偏差超过 `5s` 的来源不能组成 fresh 快照；不同 seller/channel/region/quantity/membership scope 的缓存值不会串用。
- fresh/stale/unknown/conflict/unavailable 五种状态均有确定性分支，关键事实不可用时不生成确定购买建议。
- 促销门槛、有效期、适用范围、叠加/互斥和区域条件均有边界测试，到手价与结构化规则一致。
- adapter 部分失败时保留其他已验证事实，并明确缺失项；不得用商品描述或模型常识补全。
- 缓存不会跨 SKU、offer、seller、channel、区域、数量、币种、membership scope、授权上下文或 freshness version 串值。
- M5-02 新鲜度合规率为 100%，M5-03 可行动事实覆盖率 `>= 95%`；不能通过把全部结果标记 unknown 达标。
- 固定数据规模、50 商品批量读取和 100 并发下事实网关 P95 `<= 800ms`。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_commerce_fact_gateway.py -q
```

**任务边界**

不实现真实京东交易接口，不用知识图谱保存高频变化的库存和价格，不把缓存命中当作事实仍然新鲜的证明。

### H5：将商品知识与事实网关接入决策链路

**目标**

让 C 阶段的快照、归一化、过滤、比较和 D5 事实校验统一消费 H 阶段契约，并证明商品图谱提供的候选扩展不会绕过硬过滤或引入无证据关系。

**前置依赖**

- H1-H4 已冻结首个 ontology、entity、graph 和 fact schema version。
- C1-C5 和 D5 已有冻结回归基线。
- F3/F4 可运行同一场景集和性能基准。

**实施内容**

1. 将 C1 ProductSnapshot 构建切换到 Commerce Fact Gateway，并保留兼容适配层和可关闭开关。
2. 将 C3 FeatureNormalizer 绑定 ontology version；未知属性、单位冲突和本体缺失进入明确的缺失/错误分支。
3. 在候选与比较流程中接入受控关系查询，用于变体、兼容、替代和配件信息；图扩展候选必须重新经过 C2 硬过滤。
4. 扩展 D5 GroundingVerifier，校验 canonical ID、ontology version、fact_snapshot_id、关系边 EvidenceRef 和 freshness status。
5. 扩展结构化响应，为事实和关系分别提供来源、观测时间、是否陈旧及不可用说明，不向用户暴露内部图结构。
6. 建立跨源冲突、实体误匹配、过期价格、库存变化、促销冲突、非法兼容边和图服务超时场景。
7. 扩展 threat model，覆盖恶意商品标题/属性、商家伪造兼容关系、来源降级、图边篡改和缓存投毒；不可信来源只能进入待审核或低信任隔离区。
8. 将本体 lint、实体质量、图质量、事实新鲜度、安全增量、M3 回归和性能预算加入 M5 发布门禁。

**交付物**

- C1-C5、D5 和结构化响应的 catalog intelligence 适配器。
- `services/ai-service/tests/test_agent_catalog_intelligence.py`。
- `fixtures/evals/shopping_agent_catalog_intelligence.json`。
- `scripts/verify_catalog_intelligence_release.ps1`。
- `docs/catalog_intelligence_release_report.md` 模板。
- `config/agent_quality_gates.yaml` 的版本化 M5 门禁及配置校验测试。
- 版本化 catalog compatibility manifest，列出相对 M3 唯一允许的行为变化。

**完成标准**

- M3 冻结场景在新链路下保持硬约束违反率 0、商品事实准确率 100%；响应变化必须存在于版本化 catalog compatibility manifest，未列入的变化均按回归处理。
- 所有排序、比较和最终回答使用同一 fact_snapshot_id，Trace 可还原 ontology/entity/graph/fact 版本。
- 图谱扩展出的每个商品重新通过硬过滤；无有效边证据的兼容、配件和替代关系不会输出。
- 实体不确定、事实冲突、关键事实过期和图服务失败均有测试，系统按规则降级而非生成确定性结论。
- 新增测试品类仅增加本体配置和夹具，不修改 Agent Prompt、硬过滤框架或排序器核心流程。
- M5 门禁失败时能够关闭图扩展和新事实适配器，恢复 M3 商品链路且保留审计数据。
- M5-01 至 M5-04 全部达标，商品源/图谱投毒和缓存串值测试全部通过。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_agent_catalog_intelligence.py services/ai-service/tests/test_commerce_fact_gateway.py services/ai-service/tests/test_product_graph.py -q
powershell -ExecutionPolicy Bypass -File scripts/verify_catalog_intelligence_release.ps1
```

**任务边界**

不在该任务实现协同过滤或个性化排序，不允许因图谱关系放宽预算、库存、配送和排除品牌等硬约束。

建议新增模块：

- `services/ai-service/app/domains/catalog/ontology.py`
- `services/ai-service/app/domains/catalog/entity_resolution.py`
- `services/ai-service/app/domains/catalog/product_graph.py`
- `services/ai-service/app/domains/catalog/fact_gateway.py`
- `services/ai-service/app/infrastructure/catalog/`

阶段退出条件（M5）：M5-01 至 M5-04 全部达标；首批品类本体可版本化演进；冻结冲突集无自动实体误合并且抽样 precision 达标；正式图关系均有有效证据；商品决策使用上下文完整且时间偏差受限的一致事实快照；图谱和事实网关故障时能够回退到 M3 安全链路。

## 14. 阶段 I：个性化召回与排序

阶段目标：在 G 的授权画像和 H 的商品知识/事实基础上建立多路候选召回与可审计个性化排序。任何学习模型都位于硬过滤之后，并保留 C4 确定性排序作为基线、解释参照和故障回退。

阶段 I 分为两个独立退出结果：I1-I3 的契约、索引、数据管道和影子回放通过后可标记 M6-A“架构就绪”；只有真实数据满足 `I-DATA-READY` 且 I4-I5 全部门禁通过，才能标记 M6-B“模型发布”。数据不足不会阻止 M6-A，但不得用模拟数据、purchase_proxy 或 Golden Set 宣称模型收益。

由于本任务书不实施前端，后端 fixtures 和无 UI 消费方模拟器只能验证 render_token 与曝光协议，不能产生真实 `impression_rendered`。M6-B 必须等待实际 API 消费方按 E5 契约回传真实呈现与行为事件；在此之前最高只能达到 M6-A。

`I-DATA-READY` 默认门禁写入 `config/agent_quality_gates.yaml` 并在采集前冻结：连续观测不少于 `28` 天；有效 `impression_rendered >= 100,000`；具有 model_training 授权的独立主体 `>= 5,000`；服务端可信购买/明确满意强正标签 `>= 5,000`；退货/取消/明确不满意强负标签 `>= 1,000`；至少 `5` 个核心品类分别具有 `>= 10,000` 曝光和 `>= 300` 强标签；orphan 行为比例 `<= 1%`；consent、candidate_set、position、policy/model/feature/fact version 完整率 `100%`。任一条件不满足时 I3 输出 `insufficient_for_training`，I4-I5 保持阻塞。

### I1：定义推荐请求、候选与目标契约

**目标**

冻结召回和排序各阶段的数据边界、优化目标与禁止条件，使搜索分数、图关系、行为特征和排序结果不会被混为一谈，并为基线对照、模型版本和回退行为提供稳定协议。

**前置依赖**

- G5 已能按授权提供 UserProfileSnapshot 和版本化特征。
- H5 已能提供 canonical 商品、关系证据和一致事实快照。
- C2/C4 已提供硬过滤和确定性排序基线。

**实施内容**

1. 定义版本化 `RecommendationRequest`：request_id、subject/context、ShoppingGoal、profile_version、feature_as_of、fact_snapshot_id、allowed_lanes、candidate_budget、deadline 和 policy_version。
2. 定义 `RecallCandidate`：canonical_item_id、lane、lane_rank、lane_score、reason_code、evidence_refs 和 retrieved_at；明确不同 lane 的原始 score 不可直接横向比较。
3. 定义 `RankCandidate` 和 `RankResult`，区分 eligibility、hard_filter_result、feature_vector_ref、baseline_score、model_score、final_score、rank、explanation_codes 和 fallback_reason。
4. 冻结排序目标层级：首先保持硬约束与事实正确，其次优化标注相关性和决策效率，再观察加购/购买；退货、取消、投诉和不满意作为反向指标，点击不得作为唯一目标。
5. 建立候选生命周期状态：recalled、hydrated、ineligible、eligible、ranked、dropped 和 returned；每次状态变化记录责任组件和 reason code。
6. 规定 policy/model/feature/ontology/fact 版本组合及兼容矩阵；版本不兼容时直接使用确定性基线，不现场猜测映射。
7. 冻结 M3 的 C4 baseline 输出与评分脚本，后续所有离线增益必须在同一候选真值、数据切分和指标实现上比较。

**交付物**

- `services/ai-service/app/domains/recommendation/contracts.py`。
- `services/ai-service/app/domains/recommendation/objectives.py`。
- `config/recommendation_policy.yaml`。
- `services/ai-service/tests/test_recommendation_contracts.py`。
- `docs/recommendation_objectives.md` 和版本兼容矩阵。

**完成标准**

- schema 拒绝缺少 canonical ID、lane、fact snapshot、policy version 或超出 candidate budget 的请求/候选。
- 测试证明 lane_score 不会被直接当作跨 lane final_score，相同输入和版本得到确定的候选状态序列。
- hard_filter_result 为失败的候选无法进入 ranked/returned，即使模型分数最高也会被拒绝。
- 目标文档同时定义正向、反向和护栏指标，不使用“提升转化”这类无数据口径的目标。
- 不兼容 feature/model/ontology version 触发稳定 fallback code，C4 baseline 仍可完成请求。
- baseline 数据、实现版本和内容 hash 已冻结，可被 I3-I5 重复调用。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_recommendation_contracts.py -q
```

**任务边界**

不在此任务实现召回或训练模型，不把搜索相关性、点击率和最终业务价值定义为同一个指标。

### I2：实现有界多路候选召回与融合

**目标**

根据购物目标、商品关系和授权画像并行执行多种互补召回，在总 deadline 和候选预算内获得有来源、有原因、可去重的候选集合；任何召回通道都不能绕过商品事实补全与硬过滤。

**前置依赖**

- I1 已冻结请求、候选状态和 lane 契约。
- H3/H4 提供关系查询与事实批量补全。
- G5 提供可选画像，D4 提供有界并行执行能力。

**实施内容**

1. 首批实现 lexical_search、semantic_search、graph_relation、behavior_affinity 和 category_popular 五类 lane；总召回 deadline 固定为 `1000ms`，单路 timeout 依次为 `300/500/200/200/100ms`，单路 top_k 上限依次为 `100/100/50/50/50`，融合前总 candidate budget 为 `200`。
2. 用 ShoppingGoal、当前页面和授权画像选择 lane 及参数；无画像时关闭 behavior_affinity，但其余 lane 保持可用。
3. 复用 D4 有界并行模型，在总 deadline 内执行；单 lane 失败只影响自身候选并写入 partial failure，不取消已成功结果。
4. 按 canonical_item_id 去重，保留多 lane 命中列表、各自 rank/score/reason/evidence，不覆盖为一个不可解释融合分。
5. 实现确定性融合：归一化仅在声明的方法和 lane 内完成，使用配额、RRF 或配置策略合并；策略版本、tie-break 和截断原因必须记录。
6. 定义版本化 `RecallIndexManifest`。semantic_search 使用 H2 canonical 商品生成独立商品 embedding/index，禁止复用 RAG chunk 索引；manifest 记录 embedding model、ontology、source snapshot、build time、artifact hash 和 active/rollback version。
7. 从 G2 可信且授权的事件生成 behavior affinity/co-visitation 索引，固定 `7d/30d/90d` 窗口；一条共现边至少需要 `20` 个不同授权主体支持。category_popular 按品类、区域和时间窗物化，7 天窗口少于 `100` 个有效 impression_rendered 时回退 30 天窗口，不能使用全站单一热榜；quarantine 事件不参与计算。
8. 支持索引全量构建、增量更新、原子版本切换、新鲜度检查和一键回滚；构建失败不得覆盖当前 active manifest。
9. 对融合候选批量调用 H4 补全实时事实，再执行 C2 硬过滤；无法映射 canonical ID、关键事实不可用或不满足约束的候选不得返回。
10. 记录 candidate_set_id 及每个候选的 lane provenance，但不把“被召回/被服务端发送”记作 impression；输出 Recall@K、lane coverage、unique coverage、overlap、过滤前后数量、索引版本、超时率、无结果率和候选来源分布。

**交付物**

- `services/ai-service/app/domains/recommendation/recall.py`。
- `services/ai-service/app/infrastructure/recommendation/` 下的 lane/index adapters 和 manifest repository。
- 各 lane adapter 和 `config/recommendation_recall.yaml`。
- `services/ai-service/ml/indexes/` 与 `scripts/build_recommendation_indexes.py`。
- `services/ai-service/tests/test_multi_lane_recall.py`。
- `fixtures/evals/recommendation_recall_cases.json`。
- `scripts/evaluate_recommendation_recall.py`。

**完成标准**

- 五类 lane 均有正常、空结果、超时、非法候选和部分失败测试，整体执行时间受总 deadline 限制。
- 同一 canonical 商品被多个 lane 命中后只保留一个候选，且所有 lane provenance 与 reason code 完整保留。
- graph 和 behavior lane 的候选全部经过事实补全与硬过滤，不满足预算/库存/品牌排除项的商品不会进入 eligible。
- 无授权或新用户场景不读取画像，使用搜索/图谱/热门 fallback 并返回明确 lane plan。
- 冻结场景 Recall@50 达到 M6-01；报告同时展示各 lane 独立贡献和重叠，不只给融合总数。
- 关闭任一 lane、任一 lane 熔断或全部个性化 lane 失败时，确定性搜索基线仍能完成请求。
- embedding、behavior affinity 和 popular index 均有 manifest/hash、增量更新、陈旧检测、原子切换和回滚测试；失败构建不会改变 active version。
- 召回候选只产生 candidate_set 记录，不产生 impression；训练曝光只能来自 E5 验证过的 impression_rendered。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_multi_lane_recall.py -q
uv run --project services/ai-service python scripts/build_recommendation_indexes.py --fixture fixtures/recommendation/index_sources.json --dry-run
uv run --project services/ai-service python scripts/evaluate_recommendation_recall.py --dataset fixtures/evals/recommendation_recall_cases.json
```

**任务边界**

不允许某个 lane 通过无限扩大 top_k 掩盖低质量，不把 RAG 文档 chunk 当作商品候选，不在召回阶段执行加购或其他副作用动作。

### I3：建立 point-in-time 排序训练与评测数据集

**目标**

把曝光、行为标签、用户特征、商品特征和候选位置按事件发生时间正确关联，生成无未来泄漏、可删除、可复现的数据集；明确真实反馈不足时只能进行影子验证，不能用模拟行为宣称个性化收益。

**前置依赖**

- G2/G3 已提供可信事件、授权状态和 as_of 特征回放。
- H2/H4 已提供 canonical ID 和历史事实版本。
- I1 已定义目标、候选状态和冻结 baseline。
- I2 已记录 candidate_set 与 lane provenance，E5 已能接收带签名 render_token 的 impression_rendered。

**实施内容**

1. 定义 `RankingExample`：dataset_version、request_id、subject_partition、consent_snapshot_id、purpose、occurred_at、candidate_set_id、candidate_id、surface_id、position、policy/model/feature/fact versions、lane features、user/item/context features、eligibility、label、label_source、label_window 和 sample_weight。
2. 只从通过 render_token 校验的真实 impression_rendered 集合构造监督样本；未呈现商品不能简单作为负样本，召回失败与排序失败必须分别评估。
3. 建立标签规则：明确偏好/满意度、购买和有效加购为不同强度正信号，dismiss/cancel/return/不满意为不同负信号；click 是弱信号，purchase_proxy 和模拟事件不得作为真实购买标签。
4. 所有特征以 impression_rendered.occurred_at 为 as_of 进行 point-in-time join；标签只允许来自定义 label window 内的后续事件，不能回写为输入特征。
5. 记录曝光位置和策略，提供位置分层或倾向校正接口；首版无法可靠校正时必须在报告中分别展示位置段，不能忽略 exposure bias。
6. 按时间和主体进行 train/validation/test 划分，防止同一会话、重复请求或未来商品事实跨集合泄漏；冻结 release test 并保存内容 hash。
7. 生成 dataset card，记录 consent snapshot/purpose、来源、时间范围、覆盖品类、标签分布、缺失率、删除传播、已知偏差和不可用场景；purpose 必须为 model_training。
8. 在正式构建前计算并冻结 `I-DATA-READY` 报告；未达标时仍允许验证数据管道和生成 non-trainable manifest，但禁止产出可供 I4 发布训练使用的数据集。

**交付物**

- `services/ai-service/ml/datasets/ranking.py`。
- `scripts/build_ranking_dataset.py` 和 `scripts/audit_ranking_dataset.py`。
- `services/ai-service/tests/test_ranking_dataset.py`。
- 版本化 dataset manifest、schema、hash 和 dataset card 模板。
- `fixtures/recommendation/ranking_events.json` 的确定性小样本。

**完成标准**

- 人为注入未来购买、退货和特征更新时，审计工具能识别并拒绝 feature leakage。
- 所有负样本均能追溯到真实 impression_rendered；未实际呈现的候选不会被静默当成用户不喜欢。
- train/validation/test 在 subject、session 和时间规则上无违规交叉，冻结 test 不被训练脚本读取标签以外内容。
- purchase_proxy、客户端伪造事件、撤回授权后的数据和已删除主体不会进入正式训练集。
- 同一 manifest、源快照和代码版本重复构建得到相同记录数、样本 ID 和内容 hash。
- 任一 `I-DATA-READY` 数值未达到时，构建结果标记 `insufficient_for_training`，I4 不得绕过门禁训练发布模型。
- 每条正式样本都能追溯到有效 impression_rendered、candidate_set、render_token 校验结果、model_training consent snapshot 和完整策略/模型/特征/事实版本。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_ranking_dataset.py -q
uv run --project services/ai-service python scripts/build_ranking_dataset.py --fixture fixtures/recommendation/ranking_events.json --dry-run
uv run --project services/ai-service python scripts/audit_ranking_dataset.py --manifest artifacts/recommendation/dataset_manifest.json
```

**任务边界**

不购买或抓取来源不明的用户行为数据，不把 Golden Set 伪造为线上反馈，不通过随机切分掩盖时间泄漏和用户泄漏。

### I4：训练并服务可审计的个性化排序器

**目标**

训练一个输入、标签、特征和输出均可版本化审计的排序模型，在冻结数据上证明相对 C4 baseline 的增益，并以独立、超时可回退的服务接口提供分数和解释信息。

**前置依赖**

- I3 数据门禁判定数据足以训练，dataset manifest 和 release test 已冻结。
- I1 已冻结 baseline、目标和特征兼容契约。
- G3 的 feature version、H1 的 ontology version 可按 manifest 重放。

**实施内容**

1. 首版选择可解释的 pointwise/pairwise 排序模型；固定随机种子、依赖、超参数搜索空间和训练资源，不允许训练脚本临时访问线上服务。
2. 训练输入只读取 I3 manifest 声明的特征，保存 feature signature、缺失处理、类别编码、单调性约束和训练代码 commit。
3. 建立 model registry 元数据：model_version、dataset_version、feature_versions、ontology_version、metrics、artifact hash、signature、approved_by、stage 和 rollback target；加载前校验 hash/signature，制品篡改或来源不明时 fail closed。
4. 在 frozen test 上与 C4 baseline 比较 NDCG@10、MRR、Recall@K、coverage、diversity，并按新/老用户、品类、价格带和活跃度分组报告。
5. 对预算、库存、退货等关键特征执行方向性、极值、缺失和对抗测试；无法解释的异常依赖必须阻止模型进入 approved stage。
6. 实现批量 rank API，返回 model_score、model_version、feature snapshot ref 和有限 explanation codes；不得返回伪造自然语言理由。
7. 设置 100 候选 `150ms` deadline、输入规模和并发上限；模型缺失、版本不兼容、非法输出、超时或服务故障时回退 C4 baseline。

**交付物**

- `services/ai-service/app/domains/recommendation/ranker.py`。
- `services/ai-service/app/infrastructure/recommendation/model_registry.py`。
- `services/ai-service/ml/ranking/`、`scripts/train_personalized_ranker.py` 和 `scripts/evaluate_personalized_ranker.py`。
- `services/ai-service/tests/test_personalized_ranker.py`。
- model card、评测报告和可校验模型 artifact。

**完成标准**

- 训练可由一条命令从冻结 manifest 复现；模型 artifact、feature signature、指标和 hash 与 registry 一致。
- frozen test 至少包含 10,000 个 request，NDCG@10 点估计达到 baseline `1.05` 倍且 95% bootstrap CI 下界 `> 1.00` 倍；硬约束、事实准确、coverage 和 diversity 护栏无回归。
- 每个关键分群至少 500 个 request 且 NDCG@10 不低于 baseline `0.98` 倍；样本不足或低于下限时模型保持 shadow/rejected，不能仅凭总体提升发布。
- 非法 NaN/Inf、未知特征、版本不兼容、超大候选集和服务超时全部触发 baseline fallback。
- explanation code 来自已注册特征贡献或规则，不把模型分数包装成商品事实或无证据推荐理由。
- 模型不能使 ineligible 候选进入结果；排序前后均有断言和测试验证。
- 100 候选批量排序 P95 `<= 150ms`；制品 hash/signature 不匹配、未知来源或 feature signature 异常时不加载候选模型。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_personalized_ranker.py -q
uv run --project services/ai-service python scripts/train_personalized_ranker.py --manifest artifacts/recommendation/dataset_manifest.json --output artifacts/recommendation/models/candidate
uv run --project services/ai-service python scripts/evaluate_personalized_ranker.py --model-version candidate --compare baseline-c4
```

**任务边界**

不训练生成式推荐模型，不允许线上请求即时更新模型权重，不以隐藏特征、人工修改 test 标签或删除失败分群获得发布指标。

### I5：接入安全混排、影子验证与回退门禁

**目标**

把个性化排序接入正式 Agent 决策链路，并通过双重硬过滤、影子对照、版本开关和自动回退控制风险；在缺少完整实验平台时只允许受控试运行，不自动扩大流量或自我更新策略。

**前置依赖**

- I1-I4 已通过契约、召回、数据和模型门禁。
- G5/H5 的 M4、M5 阶段退出条件均满足。
- F5 发布脚本和运行手册可扩展新的策略开关与回滚步骤。

**实施内容**

1. 正式链路固定为多路召回 -> 实时事实补全 -> C2 硬过滤 -> I4 排序 -> 结果后置硬约束复核 -> 多样性/去重 -> D5 GroundingVerifier。
2. 定义 `RankingDecision`，记录 baseline 顺序、model 顺序、最终顺序、变更原因、policy/model/feature/fact 版本和所有 dropped reason。
3. 首先运行 shadow mode：用户仍看到 baseline，后台记录模型建议但不得重复调用副作用工具；对照报告按 request/candidate 比较差异。
4. 实现配置化 enable、allowlist、category scope、traffic percentage、model version 和 kill switch；按 subject_id + experiment_version 稳定散列分桶，匿名主体按 session 稳定分桶，默认关闭且配置无效或指标缺失时保持 baseline。
5. 建立自动回退条件：5 分钟窗口错误率 `> 1%`、超时率 `> 2%`、fallback 率 `> 5%`、任一硬约束违规或事实不一致即触发；触发后 `60s` 内停止向新请求提供候选模型结果。
6. 提供后端个性化设置 API，支持查询/关闭个性化并与 consent 撤回关联；接口具有用户隔离、幂等和审计测试，不依赖任何前端页面。
7. 扩展响应解释，只允许使用通过 D5 校验的商品事实、评论证据和注册 explanation code；个性化原因不泄露敏感画像。
8. 扩展 threat model，覆盖事件/特征投毒、seller gaming、曝光伪造、模型制品篡改、特征签名不匹配、分桶绕过和候选分数异常；高风险项必须有自动化测试或明确剩余风险。
9. 建立 M6 门禁和运行手册：shadow 至少连续 `7` 天且具有 `>= 10,000` 个可对齐 request；覆盖 I-DATA-READY、离线指标、性能、安全、数据删除传播、回滚演练和已知限制。

**交付物**

- Agent 决策链路中的 recommendation orchestrator 与后置 guard。
- 个性化设置与关闭 API、审计事件和契约测试。
- `services/ai-service/tests/test_personalized_ranking_integration.py`。
- `fixtures/evals/shopping_agent_personalized_ranking.json`。
- `scripts/verify_personalized_ranking_release.ps1`。
- `docs/personalized_ranking_runbook.md` 和 shadow report 模板。
- `config/agent_quality_gates.yaml` 的 I-DATA-READY、M6 门禁与自动回退阈值及配置校验测试。
- 版本化 recommendation compatibility manifest，记录 baseline、candidate、分桶规则和回退目标。

**完成标准**

- 个性化开关关闭、模型服务不可用、版本不兼容和 kill switch 激活时均返回冻结 baseline，且不改变 API schema。
- pre-filter 与 post-filter 均能拦截违反预算、品牌排除、库存和配送约束的候选；全套对抗样本违规数为 0。
- shadow mode 不改变用户响应、购物车或事件语义，且能按 request_id 对齐 baseline/model 差异。
- 模型接入后 M3 Agent 质量门禁不下降，M6-01 至 M6-03 全部达到明确阈值。
- 自动回退条件均有故障注入测试；触发后 `60s` 内新请求停止使用候选模型，并保留审计证据。
- 通过个性化设置 API 关闭或撤回授权后不再读取画像/模型特征，后续回答回到非个性化策略；不依赖前端界面完成验证。
- 稳定分桶在重试、服务重启和并发请求中保持一致，跨用户或跨 experiment version 不串桶。
- 事件/特征投毒、曝光伪造、seller gaming 和模型制品篡改测试全部通过或在运行手册列出不可接受的剩余风险并阻止发布。
- 回滚演练证明无需删除事件、模型和历史 Trace 即可恢复 M5 决策链路。

**验证命令**

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_personalized_ranking_integration.py services/ai-service/tests/test_agent_security.py -q
powershell -ExecutionPolicy Bypass -File scripts/verify_personalized_ranking_release.ps1
```

**任务边界**

不建设通用实验平台，不自动扩大真实流量，不允许模型修改硬过滤、事实快照、工具授权和动作确认规则。

建议新增模块：

- `services/ai-service/app/domains/recommendation/contracts.py`
- `services/ai-service/app/domains/recommendation/recall.py`
- `services/ai-service/app/domains/recommendation/ranker.py`
- `services/ai-service/app/infrastructure/recommendation/`
- `services/ai-service/ml/datasets/ranking.py`
- `services/ai-service/ml/indexes/`
- `services/ai-service/ml/ranking/`

阶段退出条件（M6-A）：I1-I3 的契约、索引构建、candidate_set/impression_rendered 关联、point-in-time 数据管道和影子回放全部通过；数据不足时明确输出 insufficient_for_training，不启动 I4 发布训练。阶段退出条件（M6-B）：I-DATA-READY 与 M6-01 至 M6-03 全部达标；候选模型统计上优于确定性基线且关键分群无越界退化；连续 7 天影子链路、攻击测试和 60 秒自动回退经过演练；关闭个性化后可完整恢复 M5 行为。

## 15. 任务依赖与并行建议

```text
A1 -> A2
A1 -> A3 -> A4 -> A5
A3 -> B1 -> B2 -> B3 -> B4 -> B5
A3 -> C1 -> C3
B3 + C1 -> C2
C2 + C3 -> C4 -> C5
A5 + B5 + C5 -> D1 -> D2 -> D3 -> D4 -> D5
A3 + B5 + C1 -> E1 -> E2
A4 + D5 + E1 -> E3
C1 + D3 + E3 -> E4
A5 + B5 + C4 + E2 + E3 + E4 -> E5
D3 + D5 + E1 + E4 + E5 -> F1 -> F2
A2 + F2 -> F3 -> F4 -> F5
F5 + E5 -> G1 -> G2 -> G3 -> G4 -> G5
F5 + C1 + C3 -> H1 -> H2 -> H3
H2 + C1 -> H4
H1 + H2 + H3 + H4 + C5 + D5 -> H5
G5 + H5 -> I1 -> I2 -> I3 -> [I-DATA-READY] -> I4 -> I5
```

可并行组合：

- A2 场景集与 A3/A4 协议设计可以并行。
- B 购物目标状态与 C1/C3 商品事实建模可在接口冻结后并行；C2 硬过滤必须等待 B3 的约束模型。
- E1 上下文组装和 E5 反馈事件可在 D 阶段后半段并行准备。
- M3 通过后，G 数据画像与 H 商品知识可以由不同小组并行建设，但必须共同复用 consent、canonical ID、版本和 Trace 规则。
- H3 图谱与 H4 事实网关可在 H2 canonical ID 冻结后并行；H5 等待两者完成。
- I2 可以先用冻结夹具开发，但 I3 正式数据集必须同时具有 I2 candidate_set/lane provenance 和 E5 真实 impression_rendered；I4 不得跳过 I-DATA-READY。
- 测试应与对应任务同步编写，不集中留到阶段 F、G5、H5 或 I5。

## 16. 每个任务的完成定义

任务只有同时满足以下条件才能视为完成：

1. 先提交失败测试或场景，再实现功能，并保留可复现测试入口。
2. 新增数据结构具有 Pydantic schema、序列化版本和边界校验。
3. 新增工具具有类型化输入输出、超时、错误码和授权策略。
4. 新增事件、特征或数据管道具有幂等、重放、时间语义、版本血缘和删除传播测试。
5. 新增决策具有确定性测试，并在 Trace 中保留输入摘要、版本、证据与结论。
6. 读取用户行为或画像前验证身份、purpose、consent、retention 和跨用户隔离。
7. 新增模型具有冻结数据、baseline 对照、分群报告、模型卡和可验证回退路径。
8. 不破坏现有聊天、RAG、联网搜索、会话恢复、硬过滤、事实校验和旧 API 消费方协议。
9. G-H-I 的领域、基础设施、worker 与 ML 代码遵守第 2.4 节目录边界，在线 Web 进程不执行回填、索引全量构建或模型训练。
10. 文档、配置示例、迁移、测试夹具、验证命令和实际实现保持一致。
11. 未达到验收指标时记录失败样本并阻止发布，不通过修改评分口径、删除失败样本或放宽护栏规避问题。

## 17. 后续版本候选

这些能力只有在 M6-B 稳定、已有足量真实反馈并完成独立收益评审后再进入下一份任务书：

- 商品图片、截图和评论图片的多模态理解。
- Prompt/SFT/DPO 策略训练及模型蒸馏。
- 跨设备长期购物任务和家庭成员偏好隔离。
- 人工客服转接、坐席辅助和售后服务闭环。
- 真实京东或其他电商平台的数据与交易接口。
- 通用灰度实验平台、多策略 A/B、因果评估和自动流量分配。
- 面向长期价值、退货率和满意度的多目标/强化学习排序。
