# Warehouse View Template Builder

Warehouse employees can create Feishu inventory views with plain business language.

Examples:

- Help me create a high-risk inventory view.
- Create a Hong Kong warehouse low-stock warning view.
- Generate a warehouse exception board.
- Create a fulfillment risk view.

Employees do not need to provide field names, filters, sort rules, or API payloads. The backend maps the request to a controlled template, validates the current Feishu table schema, then creates or reuses the view.

Initial templates:

- Inventory risk view
- Low-stock warning view
- Warehouse exception view
- Replenishment candidate view
- Fulfillment-blocking inventory view

Smoke test:

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/views/from-template `
  -ContentType "application/json" `
  -Body '{"message":"帮我建一个香港仓高风险库存视图"}' | ConvertTo-Json -Depth 10
```
