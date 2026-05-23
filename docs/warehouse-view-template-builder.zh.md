# 仓储视图模板构建器

仓储员工可以用自然业务语言创建飞书库存视图。

示例：

- 帮我建一个高风险库存视图
- 创建一个香港仓缺货预警视图
- 生成一个仓储异常看板
- 建一个履约风险视图

员工不需要提供字段名、筛选条件、排序规则或 API payload。后端会把请求映射到受控模板，校验当前飞书表格 schema，然后创建或复用视图。

MVP 边界：当前飞书集成会创建或复用一个 grid 视图，并返回已校验的字段、筛选和排序计划；它还不会把这些可见字段、筛选或排序设置直接应用到飞书 UI 中。将 validated_plan 真正落到飞书可见视图配置，是后续增强项。

第一版模板：

- 库存风险视图
- 缺货/低库存预警视图
- 仓储异常视图
- 补货候选视图
- 履约阻塞视图

Smoke test：

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/views/from-template `
  -ContentType "application/json" `
  -Body '{"message":"帮我建一个香港仓高风险库存视图"}' | ConvertTo-Json -Depth 10
```
