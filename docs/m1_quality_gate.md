# M1 质量门禁

`D` 阶段对应核心导购 MVP（M1）。D1-D5 的历史模块验收仍然有效，但旧阶段证据使用了旧质量配置哈希，不能作为本门禁的通过证明。

## 输入与计算

`scripts/build_m1_quality_report.py` 从一个冻结的 Kayn run 目录读取 `manifest.json`、`single-turn-results.json` 和 `multi-turn-results.json`。场景集必须与清单中的版本及哈希一致，每个场景须恰好得到一个结果或明确列为延后。延后、`NOT_EVALUATED`、错误和缺失样例都留在各自分母中，不能提高通过率。

门槛只在 `config/agent_quality_gates.yaml` 的 M1-01 至 M1-08 中定义。总体、多轮、比较、评论总结与硬约束场景的成功率分别计算；工具契约使用 Kayn 的确定性 `talonmart_contract_guard` 结果。现有 D 阶段冻结验收继续检查硬过滤、商品事实和工具授权；Judge 评分不替代这些断言。

运行时指纹由目标连接器在启动时固定，并随每个 Kayn 契约指标的 `runtime_fingerprint` 证据返回。评测脚本汇总为 `manifest.target_runtime_fingerprint`；CI 会逐案从原始指标证据重新核对，不能仅靠填写 manifest 通过。该值还须与评测脚本的 `implementation_fingerprint` 一致。评测文件必须来自干净的 Git 工作区，且在评测提交与 CI 当前提交之间没有改动。缺少任一证明时，M1-07 为 0。

## CI 复验

1. 将 Kayn 原始结果和生成的质量报告放在同一个仓库目录，例如 `artifacts/kayn-evaluations/<run-id>/`。先运行 `python scripts/build_m1_quality_report.py --run-dir artifacts/kayn-evaluations/<run-id> --output artifacts/kayn-evaluations/<run-id>/m1-quality-report.json`。
2. 将受测实现、原始结果和报告提交到待审分支。通过 `Agent task gate` 的 `workflow_dispatch` 选择 `milestone_id=D`，`quality_report` 填写仓库内的报告路径。
3. CI 会从原始结果重算报告并逐字段比较，再独立重跑 A、B、C、D 阶段的冻结验收和质量门禁。下载完整的 `agent-phase-D-evidence` artifact，经审查后按仓库证据流程纳入版本控制。

当前 2026-09-23 的 Kayn 基线只有 33/40 个样例得到有效判定，总体通过 10/40，多轮 0/13，比较 0/6，评论 0/5，工具契约通过 26/40，且没有运行时指纹。它会被本门禁拒绝。此结果只用于验证门禁能正确失败。
