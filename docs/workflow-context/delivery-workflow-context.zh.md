# Delivery Workflow 上下文

## 范围

Delivery Agent 是物流部门 workflow，入口文件是 `n8n/workflows/delivery-workflow.json`，webhook 路径是 `/webhook/delivery-inbound`。它通过 Feishu Gateway Adapter 接收 delivery bot 消息，调用 mock delivery API 回复物流状态、异常和 case 创建结果。

## 已实现能力

- `delivery_status_tool`：调用 `mock-api /delivery/status/lookup`，按 `ord_*` 或 `ship_*` 查询物流状态、承运商、预计送达、延迟天数、风险等级和建议动作。
- `delivery_exception_tool`：调用 `mock-api /delivery/exceptions/search`，查询延迟、丢件或异常运单。
- `delivery_case_tool`：调用 `mock-api /delivery/cases`，仅在用户明确要求创建物流跟进 case 时创建记录。

## 边界

- Delivery Agent 只负责物流、配送、承运商、运单和物流跟进 case。
- 退款、赔偿、售后政策和客户安抚归 Customer Support。
- 库存、出库、拣货和履约阻塞归 Warehouse。
- 物流 mock 数据当前复用 `fixtures/data/orders.json` 和 `fixtures/data/shipments.json`，case 记录为 mock-api 内存数据。

## 验证

```powershell
pytest services\mock-api\tests -v
pytest services\feishu-adapter\tests -v
pytest tests\test_department_workflows.py -v
ruff check services\mock-api services\feishu-adapter tests
docker compose -p after-sales-implementation config --quiet
```

常用 smoke：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"order_id":"ord_101"}' http://localhost:8002/delivery/status/lookup
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"status":"delayed","min_delay_days":1}' http://localhost:8002/delivery/exceptions/search
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"platform":"feishu","message_type":"text","sender_id":"local","chat_id":"local","message_id":"local_delivery_ord_101","text":"查询 ord_101 物流状态"}' http://localhost:5678/webhook/delivery-inbound
```
