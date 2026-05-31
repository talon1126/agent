# mock-api

客服 agent 通过 `mock-api` 获取订单、物流和政策事实，不直接访问 Postgres。

- `GET /orders/{order_id}`
  - 用途：按订单号查询订单事实。
  - 当前主要由 `services/ai-service/app/order_status_tool.py` 调用。

- `GET /shipments/{shipment_id}`
  - 用途：查询历史物流 fixture 中的运单状态；新订单物流状态应优先消费 Warehouse/Delivery 维护在订单上的物流字段。

- `POST /policies/search`
  - 用途：检索售后政策。
  - 返回必须包含可审计元数据：`source_file`、`section`、`clause_id`、`clause_title`。
  - 如果没有匹配条款，客服回复必须说明“未找到对应公司政策，需要人工确认”，不要编造政策。
