# 仓储视图模板构建器

仓储员工可以用自然业务语言创建飞书库存视图。

示例：

- 帮我建一个高风险库存视图
- 创建一个香港仓缺货预警视图
- 生成一个仓储异常看板
- 建一个履约风险视图

员工不需要提供字段名、筛选条件、排序规则或 API payload。后端会把请求映射到受控模板，校验当前飞书表格 schema，然后创建或复用视图。

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
