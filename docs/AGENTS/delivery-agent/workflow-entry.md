# workflow 入口

- Feishu 物流机器人消息进入 `feishu-adapter`，再转发到 n8n `Delivery Workflow`。
- n8n webhook：`POST /webhook/delivery-inbound`。
- workflow 文件：`n8n/workflows/delivery-workflow.json`。
- 当前主要工具：
  - `delivery_status_tool`：按 `ord_` 订单号查询数据库订单的物流状态、供应商、快递员电话和处理建议。
  - `delivery_exception_tool`：按订单状态或物流供应商查询物流订单列表，常用于已发货未到货或某承运商配送排查。
  - `delivery_case_tool`：为指定订单创建物流跟进 case。
