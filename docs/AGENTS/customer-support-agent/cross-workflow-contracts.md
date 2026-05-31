# 其他 workflow 可能会用到的契约

- Customer Support Agent 查询订单或物流状态时优先使用 `order_status_tool` 或已暴露的订单/物流 API，不直接读取 Warehouse 的库存批次表。
- Customer Support Agent 需要解释“为什么不能发货”时，可以引用 Warehouse `POST /warehouse/fulfillment/check` 返回的 `blockers` 和 `next_action`，但不要自行承诺退款、补偿或库存调拨。
- Customer Support Agent 需要解释缺货补货进度时，只能引用 Procurement 的补货申请或采购单状态，不要承诺采购系统没有给出的准确到货时间。
- 涉及退款、退货、换货、审批、补偿、物流赔偿或差评处理时，必须通过 `policy_search_tool` / `POST /policies/search` 获取政策条款和元数据。
- 前端或未来 customer API 如果使用 `users`、`cart_items`，应补齐服务层接口和初始化/迁移路径，不要只依赖手工建表状态。
- 当前购物车 API 已由 `mock-api` 暴露：`POST /cart` 添加并累加商品，`GET /cart?user_id=1` 查询指定用户购物车，`DELETE /cart?user_id=1&item_id=...` 移除整条购物车商品。
