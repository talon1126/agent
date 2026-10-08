# TalonMart Agent A-D Kayn 正式测评

- Git 基线：`3c975d603dd5cbbdaf2678342f847f2fde87527e`
- 被测工作区：有未提交变更
- 被测实现指纹：`6c977220a26d92972b15271d0ab5fe7231c0fa64e72ac77d49019f74f72eeace`
- Golden Set：`1.0.0` / `aa0f9a087d2783ad43361a0ba525a980410e319055d2c317e6fba0149e09a1ac`
- 单轮 Run：`4607cb21-2ec1-4e17-a756-192b323a0089`
- 多轮 Run：`e855f3a4-d034-474f-880e-3833a77ecddb`
- 评审指标：`talonmart_abcd_quality` `1.2.0`
- 主评分样例：35；待故障注入验证：5
- 判定：0.80 及以上通过；硬约束、工具边界、事实与终态错误按严格上限扣分。

## 平台结果

| 类型 | 状态 | 样例 | 通过率 | 失败 | 错误 |
|---|---:|---:|---:|---:|---:|
| 单轮 | COMPLETED | 23 | 91.3043% | 2 | 0 |
| 多轮 | COMPLETED | 12 | 10.0% | 9 | 0 |

## 多轮指标

| 指标 | 样例 | 可评分 | 均分 | 通过 | 评审错误 |
|---|---:|---:|---:|---:|---:|
| `context_retention` | 10 | 10 | 0.590 | 5 | 0 |
| `goal_achievement` | 10 | 10 | 0.430 | 4 | 0 |
| `role_adherence` | 10 | 10 | 0.550 | 4 | 0 |
| `talonmart_abcd_quality` | 10 | 10 | 0.780 | 5 | 0 |
| `talonmart_contract_guard` | 10 | 10 | 0.600 | 6 | 0 |
| `turn_relevancy` | 10 | 10 | 0.460 | 4 | 0 |

## 阶段诊断

| 阶段 | 样例 | 通过 | 失败/错误 | 通过率 |
|---|---:|---:|---:|---:|
| A | 35 | 22 | 13 | 62.9% |
| B | 19 | 8 | 11 | 42.1% |
| C | 29 | 18 | 11 | 62.1% |
| D | 30 | 17 | 13 | 56.7% |

## 场景诊断

| 场景类别 | 样例 | 通过率 |
|---|---:|---:|
| comparison | 6 | 66.7% |
| constraint_conflict | 5 | 60.0% |
| hard_constraint | 8 | 50.0% |
| no_candidates | 5 | 80.0% |
| review_summary | 5 | 60.0% |
| vague_need | 6 | 66.7% |

## 未通过样例

| scenario_id | 判定 | 错误码 | 延迟(ms) |
|---|---|---|---:|
| `shop_hard_stroller_exact_boundary` | FAIL | - | 9148 |
| `shop_hard_earbuds_budget_60` | FAIL | - | 11610 |
| `shop_conflict_air_purifier_budget_reduction` | NOT_EVALUATED | - | 4375 |
| `shop_hard_withdraw_brand_preference` | NOT_EVALUATED | - | 6154 |
| `shop_review_air_fryer_negative_followup` | FAIL | - | 5334 |
| `shop_none_add_xiaomi_exclusion` | FAIL | - | 241 |
| `shop_vague_stroller_budget_followup` | FAIL | - | 18290 |
| `shop_vague_air_quality_room_size` | FAIL | - | 15981 |
| `shop_compare_pantry_items` | FAIL | - | 12163 |
| `shop_hard_delivery_tomorrow` | FAIL | - | 8895 |
| `shop_review_milk_low_rating` | FAIL | - | 7373 |
| `shop_conflict_delivery_and_choice` | FAIL | - | 13028 |
| `shop_compare_stroller_budget_change` | FAIL | - | 3756 |

## 已通过样例

- `shop_compare_dairy`
- `shop_compare_earbuds_tv`
- `shop_compare_xiaomi_cleaning`
- `shop_compare_xiaomi_kitchen`
- `shop_conflict_capacity_brand`
- `shop_conflict_include_exclude_xiaomi`
- `shop_conflict_tv_budget`
- `shop_hard_air_fryer_capacity`
- `shop_hard_exclude_xiaomi`
- `shop_hard_pen_quantity_budget`
- `shop_hard_xiaomi_air_fryer`
- `shop_none_delivery_one_hour`
- `shop_none_electronics_under_5`
- `shop_none_tv_under_100`
- `shop_none_unknown_earbud_brand`
- `shop_review_earbuds_summary`
- `shop_review_stroller_summary`
- `shop_review_tv_summary`
- `shop_vague_beverage_gift`
- `shop_vague_breakfast_food`
- `shop_vague_earbuds_use`
- `shop_vague_office_restock`

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
