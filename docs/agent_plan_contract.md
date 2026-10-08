# Agent 静态计划协议

## 1. 目标与边界

D1 把 Agent 的隐式推理结果收敛为可验证的静态 DAG。协议负责描述步骤、依赖、输入输出、工具授权、风险、预算和停止原因；它不负责生成计划、执行工具、重试、恢复进度或暴露模型思维链。这些运行时能力由 D2-D5 在该协议之上实现。

计划分为两层：

- `AgentPlanDraft` 是模型可提交的内容，只包含版本、计划 ID、步骤和停止原因。
- `AgentPlan` 是服务端验证后的内容，额外绑定策略版本、生效预算和规范化拓扑顺序。

模型不能在草稿中声明预算或策略版本。未知字段、未知步骤、未知工具和自由文本指令都会被拒绝。

## 2. 步骤协议

每个 `PlanStep` 必须声明：

| 字段 | 约束 |
| --- | --- |
| `step_id` | 计划内唯一、稳定、可作为依赖引用 |
| `step_type` | 必须属于服务端闭合的 `StepType` |
| `dependencies` | 前置步骤 ID 及期望输出类型 |
| `inputs` | 请求、购物目标、上下文或前置步骤的类型化引用 |
| `output_type` | 必须与步骤类型的服务端契约一致 |
| `risk_level` | 由步骤类型固定，模型不能自行降级 |
| `allowed_tools` | 必须与步骤类型的最小授权集合完全一致 |
| `timeout_ms` | 不得超过生效的单步超时 |

支持的步骤类型为 `clarify`、`product_search`、`snapshot`、`review_fetch`、`rag_lookup`、`filter`、`rank`、`compare`、`compose` 和 `action_preview`。其中 `filter`、`rank`、`compare`、`compose` 不直接获得工具授权；`compose` 可直接消费请求或消费前置结果，避免简单问答被迫增加无意义步骤。`action_preview` 是唯一可获得写前预览能力的步骤，可基于已选择商品或排序结果生成预览，但 D1 不执行真实写操作。

输入引用必须满足以下规则：

- `request` 只能提供 `user_query`。
- `goal` 只能提供 `shopping_goal`。
- `context` 只能提供 `page_context` 或 `user_context`。
- `step` 必须同时声明依赖，引用类型必须与前置步骤输出完全一致。
- 每个依赖都必须被输入消费，不允许隐藏依赖或未声明的数据流。

## 3. DAG 与恢复

验证器先检查唯一 ID 和依赖存在性，再检测环路，最后验证数据流和步骤契约。合法计划使用按步骤 ID 排序的确定性拓扑算法；因此模型返回步骤的先后顺序不会改变执行顺序。

序列化后的 `AgentPlan` 包含 `policy_version`、`budget` 和 `topological_step_ids`。恢复时会重新验证策略版本、硬上限、DAG、类型和规范化拓扑，不信任持久化内容。策略变化后，旧计划必须重新规划，不能静默沿用。

## 4. 预算与停止

`plan_policy.yaml` 是 D1 的服务端策略，分别定义默认值和硬上限：

- 最大步骤数和候选数；
- 最大并发数；
- 单步和总超时；
- 最大模型调用数；
- 最大重试数。

调用方可以通过独立的 `ExecutionBudgetRequest` 请求较小值或硬上限内的值，但不能把预算字段塞进模型草稿，也不能超过服务端硬上限。验证阶段会拒绝超出步骤数、模型调用数或单步超时的计划；其余预算由后续执行器消费。

停止原因也是闭合枚举，包括完成、需要澄清、无候选、预算耗尽、工具拒绝、超时、取消、验证失败和安全降级。后续执行器只能从该集合中选择终态。

## 5. Trace 与错误

成功验证会写入 `plan` Trace 事件，内容仅包括：

- 计划 ID、策略版本和步骤数量；
- 步骤类型、授权工具和规范化拓扑；
- 停止原因和完整预算摘要。

失败事件只记录稳定的验证错误码。Trace 不记录原始草稿、提示词、用户问题、步骤自然语言说明或模型思维链。

常见错误码包括 `invalid_schema`、`duplicate_step_id`、`missing_dependency`、`dependency_cycle`、`output_type_mismatch`、`step_contract_violation`、`budget_exceeds_limit`、`step_budget_exceeded`、`policy_version_mismatch` 和 `topology_mismatch`。验证失败必须显式返回错误，不存在“开放全部工具后继续”的降级路径。

## 6. 使用方式

```python
policy = load_plan_policy()
validator = AgentPlanValidator(policy)
plan = validator.validate(draft_payload, requested_budget={"max_steps": 8})

serialized = plan.model_dump_json()
restored = validator.restore(serialized)
```

只有 `AgentPlanValidator` 返回的 `AgentPlan` 才能交给后续执行层。不得直接执行 `AgentPlanDraft`，也不得跳过恢复校验。
