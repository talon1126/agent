# 其他 workflow 可能会用到的契约

- Warehouse 创建订单时可以指定 `delivery_provider_id`、`courier_phone` 和 `tracking_no`；未指定供应商时默认顺丰。
- Warehouse 付款、发货、到货、退款、退货接口负责订单状态流转；Delivery 不直接调用库存扣减逻辑。
- Delivery 查询物流时只使用 `POST /delivery/status/lookup` 或 `POST /delivery/exceptions/search`，不要读取历史 `fixtures/data/orders.json` 或 `fixtures/data/shipments.json`。
- 需要判断是否能出库时，应交给 Warehouse 的 `POST /warehouse/fulfillment/check`，不要由 Delivery 自行判断库存。
