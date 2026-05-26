# 采购模块开发进度

更新时间：2026-05-26

## 当前工作区

- 用户项目根目录：`D:\Project\agent`
- 当前采购模块 worktree：`D:\Project\agent\.worktrees\procurement-approval-drafts`
- 当前分支：`codex/procurement-approval-drafts`
- 当前状态：功能实现和本地验证已完成，尚未提交，等待 review 后再决定是否合入 `master`。

另一个 Codex 对话接手时，请优先进入当前 worktree：

```powershell
cd D:\Project\agent\.worktrees\procurement-approval-drafts
git status --short
```

## 已完成

### 1. 采购补货申请审批与采购草稿单

- 新增/完善 `replenishment_requests` 补货申请流转。
- 新增 `procurement_suppliers` mock 默认供应商数据。
- 新增 `purchase_order_drafts` 采购草稿单。
- 支持单条补货申请批准：
  - `POST /procurement/replenishment-requests/{request_id}/approve`
  - 状态从 `pending_procurement_review` 更新为 `purchase_order_draft_created`
  - 创建或复用采购草稿单。
- 支持单条补货申请驳回：
  - `POST /procurement/replenishment-requests/{request_id}/reject`
  - 状态更新为 `rejected`
  - 记录拒绝原因。
- 支持查询采购草稿单：
  - `GET /procurement/purchase-order-drafts?request_id=...`
- 重复 approve 保持幂等，不重复创建草稿单。

### 2. 采购飞书视图同步

- 新增补货请求飞书表同步能力：
  - 表名：`Procurement Replenishment Requests`
  - 唯一键：`Request ID`
  - adapter endpoint：
    - `POST /procurement/replenishment-requests-table/provision`
    - `POST /procurement/replenishment-requests-table/sync`
- 新增采购草稿单飞书表同步能力：
  - 表名：`Procurement Purchase Order Drafts`
  - 唯一键：`PO Draft ID`
  - adapter endpoint：
    - `POST /procurement/purchase-order-drafts-table/provision`
    - `POST /procurement/purchase-order-drafts-table/sync`
- 新增 mock-api table schema/rows API：
  - `GET /procurement/replenishment-requests/table-schema`
  - `POST /procurement/replenishment-requests/table-rows`
  - `GET /procurement/purchase-order-drafts/table-schema`
  - `POST /procurement/purchase-order-drafts/table-rows`
- 已验证真实飞书表可以创建并同步数据。

### 3. 批量批准生成采购草稿单

- 新增批量审批 API：
  - `POST /procurement/replenishment-requests/approve-batch`
- 默认处理全部 `pending_procurement_review` 补货申请。
- 返回：
  - `processed_count`
  - `approved_count`
  - `skipped_count`
  - `created_or_reused_drafts`
  - `errors`
- 某个商品没有默认供应商时，该申请会跳过并返回明确错误，不中断整个批次。
- n8n `procurement_approve_replenishment_batch_tool` 会在批量批准后自动刷新两张飞书采购表。

### 4. 采购草稿单预计到达时间

- `purchase_order_drafts` 新增 `estimated_arrival_date`。
- 预计到达时间按补货申请 `created_at + lead_time_days` 生成。
- 重复 approve 复用已有草稿单，预计到达时间保持稳定，不覆盖已有草稿编号。

### 5. 采购草稿到仓确认

- 新增批量确认到仓 API：
  - `POST /procurement/purchase-order-drafts/confirm-arrival-batch`
- 输入一个或多个 `POD-*`。
- 到仓确认后：
  - `purchase_order_drafts.status` 更新为 `received_at_warehouse`
  - 写入仓库库存入库批次，批次号类似 `RCV-POD-5001`
  - 返回需要 Warehouse 同步库存飞书视图的 `item_id`、`warehouse_id`、`location_code`
  - 同步采购草稿单飞书表。

### 6. Warehouse 库存同步任务入口

- 将采购到仓后的 `warehouse_inventory_sync_requested` 内部通知升级为 Warehouse workflow 可消费入口。
- 新增/完善 Warehouse Agent 工具：
  - `warehouse_inventory_sync_jobs_tool`
- 用户可发送：

```text
@warehouse 处理库存同步任务
```

- Warehouse Agent 会读取待处理库存同步任务，完成后同步对应库存视图。

### 7. 库存事实表 batch_id 调整

- 将 `inventory_batches.batch_id` 从字符串业务编号调整为自增整数主键。
- fixture seed 时会移除旧的 `batch_id`，由数据库生成整数 id。
- receipt batch 仍使用 `batch_no` 记录业务批次号，例如 `RCV-POD-5001`。
- 运行时已确认 Postgres 中 `inventory_batches.batch_id` 类型为 `integer`。

### 8. 库存同步 job 从内存迁移到 Postgres

- 新增 Postgres 表：
  - `warehouse_inventory_sync_jobs`
- 同步任务不再只依赖 mock-api 进程内存。
- 支持 job 创建/复用、查询、完成、失败。
- mock-api 无数据库时仍保留 in-memory fallback。
- 已修复 Warehouse Agent 消费库存同步 job 时的状态判定：
  - 如果飞书库存同步接口返回 HTTP 200 但 body 为 `ok:false`，workflow 不再调用 `/complete`。
  - 失败结果会走 `/fail`，job 状态更新为 `failed`。
  - mock-api 的 `/warehouse/inventory-sync-jobs/{job_id}/complete` 增加后端兜底，拒绝 `ok:false` 或带 `error` 的失败同步结果。

### 9. n8n Procurement Workflow 更新

- 当前采购 workflow 已包含以下工具：
  - `procurement_sync_replenishment_requests_tool`
  - `procurement_sync_purchase_order_drafts_tool`
  - `procurement_approve_replenishment_batch_tool`
  - `procurement_confirm_arrival_batch_tool`
  - `procurement_replenishment_request_tool`
  - `procurement_approve_replenishment_tool`
  - `procurement_reject_replenishment_tool`
  - `procurement_mock_tool`
- prompt 已覆盖：
  - 同步补货请求
  - 同步采购草稿
  - 批量批准
  - 单条批准/驳回
  - `REQ-*` 编号要求
  - `POD-*` 到仓确认
  - 到仓后通知 Warehouse 同步库存。

### 10. 员工手册

- 新增采购部门员工使用手册：
  - `docs/procurement-employee-handbook.zh.md`
- 内容覆盖：
  - 如何输入
  - 会返回什么
  - 常见异常
  - 状态说明
  - 飞书视图说明
  - 后续持续更新规则。

## 未完成

- 尚未提交 git commit。
- 尚未合入 `master`。
- 尚未将这份 progress 文档同步到其他 worktree 或主 worktree。
- 真实 ERP/正式采购单系统未接入；当前仍是 `mock-procurement`。
- 飞书表是数据库到飞书的只读同步视图，暂不支持从飞书表编辑回写数据库。
- 到仓确认后的 Warehouse 库存同步目前由 Warehouse Agent 消费 job；是否做自动跨 workflow 通知还未最终实现。

## 已修改或新增的关键文件

### 配置与运行文档

- `.env.example`
- `docker-compose.yml`
- `docs/local-runbook.md`
- `docs/local-runbook.zh.md`
- `docs/procurement-employee-handbook.zh.md`
- `docs/workflow-context/procurement-progress.zh.md`

### n8n workflow

- `n8n/workflows/procurement-workflow.json`
- `n8n/workflows/warehouse-workflow.json`

### mock-api

- `services/mock-api/app/main.py`
- `services/mock-api/app/warehouse_store.py`
- `services/mock-api/tests/test_api.py`
- `services/mock-api/tests/test_warehouse_store.py`
- `fixtures/data/procurement_suppliers.json`

### feishu-adapter

- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/app/intent_router.py`
- `services/feishu-adapter/tests/test_feishu_adapter.py`
- `services/feishu-adapter/tests/test_intent_router.py`

### workflow 结构测试

- `tests/test_department_workflows.py`

## 已验证测试结果

最近一次确认通过的命令：

```powershell
pytest services\mock-api\tests -v
```

结果：`33 passed`

```powershell
pytest services\feishu-adapter\tests -v
```

结果：已在采购飞书表同步实现阶段通过。

```powershell
pytest tests\test_department_workflows.py -v
```

结果：`3 passed`

新增针对 Warehouse 库存同步 job 状态误判的回归测试：

```powershell
pytest services\mock-api\tests\test_api.py::test_warehouse_inventory_sync_jobs_are_created_and_can_be_completed_or_failed -v
pytest tests\test_department_workflows.py::test_department_workflows_have_own_webhook_agent_memory_and_tools -v
```

结果：均已通过。测试先确认失败，再修复后通过。

```powershell
ruff check services\mock-api
```

结果：`All checks passed`

备注：ruff 曾提示无法写入 `.ruff_cache` 的权限 warning，但检查本身通过。

```powershell
docker compose -p after-sales-implementation config --quiet
```

结果：退出码 `0`

```powershell
docker compose -p after-sales-implementation up -d --build mock-api
```

结果：mock-api 已重建并启动，postgres healthy。

运行时 smoke 已确认：

- `http://localhost:8002/health` 返回 `ok`
- `inventory_batches.batch_id` 在 API 返回中为整数。
- Postgres schema 中 `inventory_batches.batch_id` 为 `integer`。
- Postgres schema 中存在 `warehouse_inventory_sync_jobs`。
- 已重建运行时 mock-api，并重新导入、发布、激活 Warehouse Workflow 后重启 n8n。
- 检查当前 Postgres 中最近的 `warehouse_inventory_sync_jobs`，没有发现 `result_json.ok=false` 但状态为 `completed` 的坏记录。

## 推荐人工验收路径

### 1. 导入并启用 workflow

```powershell
docker compose -p after-sales-implementation exec -T n8n n8n import:workflow --input=/workflows/procurement-workflow.json
docker compose -p after-sales-implementation exec -T n8n n8n import:workflow --input=/workflows/warehouse-workflow.json
docker compose -p after-sales-implementation restart n8n
```

### 2. 采购飞书同步

在飞书发送：

```text
@procurement 同步补货请求
```

预期：

- bot 有回复。
- 飞书出现或刷新 `Procurement Replenishment Requests` 表。
- 回复包含飞书表链接。

### 3. 批量批准

在飞书发送：

```text
@procurement 批量批准生成采购草稿单
```

预期：

- 生成或复用采购草稿单。
- 补货请求表刷新。
- 采购草稿单表刷新。
- 回复包含处理数量、跳过数量、异常明细和两张表链接。

### 4. 同步采购草稿

在飞书发送：

```text
@procurement 同步采购草稿
```

预期：

- 飞书 `Procurement Purchase Order Drafts` 表刷新。
- 草稿单包含 `Estimated Arrival Date`。

### 5. 到仓确认

在飞书发送：

```text
@procurement POD-5001 已到仓库
```

预期：

- 对应采购草稿单状态变为 `received_at_warehouse`。
- 创建 `RCV-POD-5001` 类入库批次。
- 返回 Warehouse 需要同步库存的 item/warehouse/location 信息。

### 6. Warehouse 消费库存同步 job

在飞书发送：

```text
@warehouse 处理库存同步任务
```

预期：

- Warehouse Agent 消费 pending job。
- 同步对应库存飞书视图。
- job 状态更新为 completed 或失败时记录 error。

## 下一步

1. 让用户 review 当前采购流程和员工手册。
2. 如 review 通过，整理 commit 范围，避免把无关 worktree 变更混入提交。
3. 提交前建议重新跑：

   ```powershell
   pytest services\mock-api\tests -v
   pytest services\feishu-adapter\tests -v
   pytest tests\test_department_workflows.py -v
   docker compose -p after-sales-implementation config --quiet
   ```

4. 如果需要给另一个对话继续开发，请让它先读：

   ```powershell
   Get-Content docs\workflow-context\procurement-progress.zh.md
   git status --short
   git diff --stat
   ```

5. 如果要把进度同步给主 worktree 或其他模块 worktree，需要手动复制本文档或通过 git commit/merge/cherry-pick 传播。

## 注意事项

- 不要在另一个对话里凭旧上下文修改采购 workflow；先读取本文件和当前 `git status`。
- 不要让采购 Agent 直接改仓储库存视图；到仓后的库存飞书同步归 Warehouse Agent 处理。
- 不要把飞书表当成源数据；当前是数据库到飞书的同步视图。
- 不要回退 `procurement-approval-drafts` 中已实现的批量审批、飞书同步、到仓确认和 Postgres job 能力。
- 当前有大量未提交变更，提交前必须确认文件范围。
