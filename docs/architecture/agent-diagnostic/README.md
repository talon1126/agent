# TalonMart Agent 链路与失败诊断图册

这份图册聚焦当前 A–D Agent 的实际行为和 2026-09-24 Kayn 测试。第三张是期望行为，不表示已实现。它是诊断材料，不是任务书阶段验收或发布门禁证据。

## 查看

| 视图 | 回答的问题 | 图像 |
| --- | --- | --- |
| [01 当前链路](index.html#current-flow) | 一个购物问题依次经过哪些模块，每步输入、动作、输出及异常是什么？ | [PNG](current-flow.png) · [SVG](current-flow.svg) |
| [02 失败归因](index.html#failure-map) | 哪些是 Agent 缺口，哪些与事实数据或 Kayn 测试有关？ | [PNG](failure-map.png) · [SVG](failure-map.svg) |
| [03 理想链路](index.html#target-flow) | 同一多轮比较问题应怎样保留约束、限定工具和输出？ | [PNG](target-flow.png) · [SVG](target-flow.svg) |

图册支持离线打开、视图切换、缩放、步骤定位和下载；`atlas.json` 是可编辑源。PNG 为浏览器按 SVG 导出的高清图。

## 证据与口径

- 实际调用链：`services/ai-service/app/routers/AImodel/service.py` 的 `_stream_chat_events_impl` 和 `_process_shopping_goal_turn`，以及 `agent_runtime.py` 的 `ShoppingAgentRuntime.run`。目标状态会持久化，但 `route_with_candidates(request.message)` 先按本轮文字判断，再由 `_recover_goal_backed_route` 有限恢复。
- 固定比较输出：`agent_runtime.py` 的 `_augment_comparison_core_facts` 与 `_comparison_answer_and_claims` 将价格和规格加入比较，并按已知价格追加低价结论。`artifacts/kayn-evaluations/20260924T031934Z/multi-turn-results.json` 的 `shop_compare_pantry_items` 在 `turnIndex` 2、3 明确说“不涉及价格”，回答仍给出价格；`turnIndex` 1 的“只比较数据，不需要推荐”还调用了 `product_reviews`。
- 事实缺失与重复兜底：`agent_runtime.py` 的库存不确定回退与低评论样本回退；`artifacts/kayn-evaluations/20260924T032920Z-multi-rerun/multi-turn-results.json` 中婴儿车快照缺可核验库存，牛奶低评四轮重复“只读取到 2 条评论”。
- 评测历史边界：`scripts/run_kayn_abcd_evaluation.py` 的 `multi_turn_draft` 把 `reference_turns` 放在 `scenarioContext`，Agent 仅接收模拟器实际发出的轮次和 `targetContext`。所以参考轮次不能自动算作 Agent 已见历史。
- 数字：`artifacts/kayn-evaluations/20260924T031934Z/REPORT.md` 记录 A–D 单轮 21/23；多轮 12 例中 10 例评分，1 例通过、9 例失败、2 例未评分。`artifacts/kayn-evaluations/20260924T032920Z-multi-rerun/multi-turn-results.json` 的多轮重跑为 0/10 评分通过、2 例未评分。`artifacts/m1-evaluations/20260924T030811Z/quality-report.json` 的 11/11 是固定回放，不能推论开放式多轮已经稳定。

## 修改与复验

编辑 `atlas.json` 后，用 `architecture-atlas` 技能的 `scripts/build_atlas.py` 生成 SVG 和 HTML，再按 `references/render-and-check.md` 用浏览器导出 PNG、检查文字布局与交互，最后运行 `scripts/finalize_atlas.py`。`verification.json` 记录本次浏览器检查结果和导出文件摘要。不能只改 SVG 而沿用旧 PNG。
