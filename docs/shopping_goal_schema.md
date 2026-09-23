# Shopping Goal Schema v1

`ShoppingGoal` 是独立于聊天文本的购物任务状态。后续抽取、合并、过滤、排序和澄清
只能读写这里定义的受控字段，不能把自由文本列表当作隐藏状态。

## 顶层结构

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `schema_version` | `"v1"` | 当前持久化协议版本 |
| `revision` | 非负整数 | 每次合法阶段迁移后递增 |
| `decision_stage` | `DecisionStage` | 当前决策阶段 |
| `stage_reason` | 可选短文本 | 最近一次阶段迁移原因，回退澄清时必填 |
| `hard_constraints` | `Constraint[]` | 候选必须满足的用户或页面明确条件 |
| `preferences` | `Preference[]` | 可在排序时权衡的偏好 |
| `exclusions` | `Exclusion[]` | 必须排除的明确值 |
| `open_slots` | `OpenSlot[]` | 仍需回答的受控问题 |

四类集合由字面量 `kind` 固定为 `hard`、`soft`、`exclude` 和 `unknown`，禁止在
一个字符串数组中混用。模型不可变、拒绝额外字段，同一集合内不允许重复语义键；
规格的语义键由 `field + attribute` 组成。

## 受控字段

| `GoalField` | 值类型与约束 | 示例 |
| --- | --- | --- |
| `category` | 非空短文本 | `冰箱` |
| `usage_scenario` | 非空短文本 | `三口之家` |
| `budget_min` / `budget_max` | `0..1,000,000,000` 的有限 Decimal | `3000` / `5000` |
| `brand` | 非空短文本；硬包含与排除不能重叠 | `海尔` |
| `specification` | 非空短文本，且必须提供属性名 | `attribute=capacity, value=500L` |
| `delivery_deadline` | 带时区的时间 | `2026-09-25T18:00:00+08:00` |
| `quantity` | `1..999` 的严格整数 | `2` |
| `freeform_preference` | 非空短文本，只用于软偏好 | `低噪音` |

硬预算同时存在时必须满足 `budget_min <= budget_max`。布尔值、浮点数量、无时区配送
时间、空白文本和非有限预算均无效。已回答的语义键不能同时保留在 `open_slots`。

## 来源证据

每个目标项必须包含 `GoalEvidence`：

| 字段 | 规则 |
| --- | --- |
| `source_type` | `user_turn`、`page_context`、`model_inference` 或 `system_default` |
| `source_turn` | 非系统来源必须提供，从 1 开始 |
| `quote` | 用户和页面来源必须提供，最多 512 字符 |
| `confidence` | 闭区间 `[0, 1]` |
| `created_at` / `updated_at` | 必须带时区，更新时间不能早于创建时间 |

只有 `user_turn` 和 `page_context` 可以生成硬约束或排除项。模型推断只能进入软偏好
或未知槽位，不能伪装成用户原话。`system_default` 只能描述系统提出的未知槽位，不能
携带虚构的 turn 或 quote。

## 阶段迁移

正常主路径为：

`discovering -> clarifying -> searching -> comparing -> decided`

允许跳过不需要的澄清，从 `discovering` 进入 `searching`；不允许从
`discovering` 或 `searching` 直接进入 `decided`。搜索、比较或已决定状态发现缺失、
冲突、无候选或用户修改条件时，可以回到 `clarifying`，但必须提供非空
`clarification_reason`。`decided` 不能直接回到搜索，必须先澄清新的条件。

## 兼容策略

`ShoppingGoal.from_payload()` 接受 `schema_version="v1"`。缺少版本时按唯一的 v0
策略升级：`constraints`、`soft_preferences`、`excluded`、`unknown`、`stage` 分别映射
到当前字段，并为集合项补充固定 `kind`。旧名和新名同时出现会拒绝，显式未知版本也
会拒绝；不会猜测未声明的历史结构。

## 禁止用法

- 不保存完整 Prompt、整段聊天记录或未经截断的页面内容。
- 不把模型推测写成 hard 或 exclude。
- 不用 `free_text_constraints`、逗号分隔字符串或任意字典绕过受控字段。
- 不在 B1 内执行自然语言抽取、状态合并、数据库持久化、商品过滤或排序。
