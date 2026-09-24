# TalonMart Agent A-D Kayn 正式测评

- Git 基线：`3c975d603dd5cbbdaf2678342f847f2fde87527e`
- 被测工作区：有未提交变更
- 被测实现指纹：`c402d8cdc0d3ffe1175657bd8d61a85a251900b2e12c7708e2b4f580bb2aaf17`
- Golden Set：`1.0.0` / `aa0f9a087d2783ad43361a0ba525a980410e319055d2c317e6fba0149e09a1ac`
- 单轮 Run：`b123419b-a12e-47d8-b0ac-5104d3341e71`
- 多轮 Run：`28135727-2623-4984-bcb7-ea57bc55848d`
- 评审指标：`talonmart_abcd_quality` `1.2.0`
- 主评分样例：35；待故障注入验证：5
- 判定：0.80 及以上通过；硬约束、工具边界、事实与终态错误按严格上限扣分。

## 平台结果

| 类型 | 状态 | 样例 | 通过率 | 失败 | 错误 |
|---|---:|---:|---:|---:|---:|
| 单轮 | COMPLETED | 23 | 21.7391% | 18 | 0 |
| 多轮 | COMPLETED | 12 | 0.0% | 11 | 0 |

## 多轮指标

| 指标 | 样例 | 可评分 | 均分 | 通过 | 评审错误 |
|---|---:|---:|---:|---:|---:|
| `context_retention` | 11 | 11 | 0.382 | 3 | 0 |
| `goal_achievement` | 11 | 11 | 0.136 | 1 | 0 |
| `role_adherence` | 11 | 11 | 0.464 | 4 | 0 |
| `talonmart_abcd_quality` | 11 | 11 | 0.645 | 0 | 0 |
| `talonmart_contract_guard` | 11 | 11 | 0.455 | 5 | 0 |
| `turn_relevancy` | 11 | 11 | 0.323 | 2 | 0 |

## 阶段诊断

| 阶段 | 样例 | 通过 | 失败/错误 | 通过率 |
|---|---:|---:|---:|---:|
| A | 35 | 5 | 30 | 14.3% |
| B | 19 | 2 | 17 | 10.5% |
| C | 29 | 5 | 24 | 17.2% |
| D | 30 | 4 | 26 | 13.3% |

## 场景诊断

| 场景类别 | 样例 | 通过率 |
|---|---:|---:|
| comparison | 6 | 0.0% |
| constraint_conflict | 5 | 40.0% |
| hard_constraint | 8 | 12.5% |
| no_candidates | 5 | 0.0% |
| review_summary | 5 | 40.0% |
| vague_need | 6 | 0.0% |

## 未通过样例

| scenario_id | 判定 | 错误码 | 延迟(ms) |
|---|---|---|---:|
| `shop_none_delivery_one_hour` | FAIL | - | 11046 |
| `shop_none_tv_under_100` | FAIL | - | 12223 |
| `shop_none_electronics_under_5` | FAIL | - | 9500 |
| `shop_none_unknown_earbud_brand` | FAIL | - | 14524 |
| `shop_conflict_tv_budget` | FAIL | - | 12969 |
| `shop_review_stroller_summary` | FAIL | - | 16342 |
| `shop_compare_xiaomi_cleaning` | FAIL | - | 11694 |
| `shop_compare_dairy` | FAIL | - | 12590 |
| `shop_compare_xiaomi_kitchen` | FAIL | - | 10918 |
| `shop_compare_earbuds_tv` | FAIL | - | 14220 |
| `shop_hard_exclude_xiaomi` | FAIL | - | 14656 |
| `shop_hard_xiaomi_air_fryer` | FAIL | - | 14250 |
| `shop_hard_stroller_exact_boundary` | FAIL | - | 13921 |
| `shop_hard_earbuds_budget_60` | FAIL | - | 13701 |
| `shop_vague_beverage_gift` | FAIL | - | 8449 |
| `shop_vague_office_restock` | FAIL | - | 16927 |
| `shop_vague_breakfast_food` | FAIL | - | 7674 |
| `shop_vague_earbuds_use` | FAIL | - | 9677 |
| `shop_review_milk_low_rating` | NOT_EVALUATED | - | 40247 |
| `shop_review_air_fryer_negative_followup` | FAIL | - | 36728 |
| `shop_hard_pen_quantity_budget` | FAIL | - | 10109 |
| `shop_vague_stroller_budget_followup` | FAIL | - | 14705 |
| `shop_hard_delivery_tomorrow` | FAIL | - | 20776 |
| `shop_vague_air_quality_room_size` | FAIL | - | 19969 |
| `shop_compare_stroller_budget_change` | FAIL | - | 16278 |
| `shop_compare_pantry_items` | FAIL | - | 6411 |
| `shop_hard_withdraw_brand_preference` | FAIL | - | 6876 |
| `shop_conflict_delivery_and_choice` | FAIL | - | 1301 |
| `shop_conflict_air_purifier_budget_reduction` | FAIL | - | 247 |
| `shop_none_add_xiaomi_exclusion` | FAIL | - | 196 |

## 已通过样例

- `shop_conflict_capacity_brand`
- `shop_conflict_include_exclude_xiaomi`
- `shop_hard_air_fryer_capacity`
- `shop_review_earbuds_summary`
- `shop_review_tv_summary`

## 待故障注入验证

- `shop_failure_catalog_timeout`：mock-api has no deterministic tool-timeout injection
- `shop_failure_reviews_timeout`：mock-api has no deterministic tool-timeout injection
- `shop_failure_delivery_timeout`：mock-api has no deterministic tool-timeout injection
- `shop_failure_detail_timeout`：mock-api has no deterministic tool-timeout injection
- `shop_failure_retry_keeps_constraints`：mock-api has no deterministic tool-timeout injection

## 解释边界

- 这是 A-D 功能诊断，不替代任务书阶段门禁，也不代表 E/F 发布质量门禁通过。
- 单轮页面上下文经版本化评测信封还原，可覆盖 A3 请求协议。
- 多轮由 Kayn 用户模拟器依据目标生成，不是对 fixture 固定话术的逐字回放。
- 多轮 scenarioContext 供模拟器与评审使用；只有显式 targetContext 会作为 Agent page_context 透传。
- tool_timeout 样例仅在 mock-api 支持确定性故障注入后进入主评分；本次不会把用户话术中的‘超时’当成真实工具故障。
- 阶段通过率按关联场景的整体验证判定统计，不把同一场景拆成虚假的独立阶段成绩。
