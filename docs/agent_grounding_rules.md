# Agent 事实校验规则

D5 校验层只消费本轮已经产生的结构化领域结果，不搜索商品、不调用 LLM
Judge，也不放宽购物目标中的硬约束。相同输入必须产生相同的有序校验结果。

## 输入与信任边界

- `ShoppingGoal`：本轮用户目标与硬约束。
- `ProductSnapshot`：价格、库存、配送、评分和规格的唯一事实来源。
- `CandidateSet`：召回与过滤结果；校验器仍会用同一策略重新执行硬过滤。
- `RankingResult`：推荐顺序和 rank claim 的唯一来源。
- `ComparisonMatrix`：比较列、单元格和值状态的唯一来源。
- `EvidenceRef`：claim 与推荐理由可引用的证据白名单。
- `GroundedResponseDraft`：A4 响应载荷与结构化 material claims。

模型生成的商品 ID、名称、证据元数据、自然语言理由和 claim value 均不被信任。

## Claim 白名单

| claim_type | 权威来源 | 额外字段 |
| --- | --- | --- |
| `category` | `ProductSnapshot.category` | 无 |
| `brand` | `ProductSnapshot.brand` | 无 |
| `price` | `ProductSnapshot.current_price` | 无 |
| `stock` | `ProductSnapshot.stock` | 无 |
| `delivery` | `ProductSnapshot.delivery` | `field` 必填 |
| `rating` | `ProductSnapshot.rating` | 无 |
| `review_count` | `ProductSnapshot.review_count` | 无 |
| `specification` | `ProductSnapshot.specifications` | `field` 必填 |
| `rank` | `RankingResult.ranked` | 无 |
| `comparison_cell` | `ComparisonMatrix` | `field` 必填 |

`promotion`、`sales_volume` 和 `absolute` 是显式拒绝类型。当前领域结果没有优惠和
销量事实源，因此即使附带其他证据也不能通过。价格、库存和配送事实必须为 `known`
且 `fresh`；未知或过期值不能被文案包装成确定结论。

## 失败与恢复

首次失败只把稳定错误码与失败 claim ID 交给 response composer。composer 最多调用
一次，修复稿必须完整重新校验。第二次失败或 composer 异常时返回 A4 `fallback`：
仅保留通过验证的 claim 和对应证据，并给出刷新后重试的下一步建议。

Trace 只记录校验数量、失败数量、错误码、修复次数、已验证 claim 数量和最终决策；
不记录回答正文、claim value、Prompt、异常文本或隐藏思维链。
