# mock-api

物流路由已拆到 `services/mock-api/app/routers/delivery/`，由 `main.py` 通过 `delivery_router` 统一注册。物流路由只读取 Warehouse 拥有的订单模型，不直接修改库存或订单状态。

- `GET /delivery/providers`
  - 用途：列出当前可用物流供应商。
  - 默认供应商：顺丰（`sf`）、京东（`jd`）、圆通（`yto`）。

- `POST /delivery/status/lookup`
  - 用途：按 `order_id` 查询订单物流状态。
  - 返回重点：`order.status`、`delivery.provider_id`、`delivery.provider_name`、`delivery.courier_phone`、`delivery.tracking_no`、`risk_level`、`recommendation`。
  - 只接受订单号，不再依赖历史 `ship_` 运单 demo。

- `POST /delivery/exceptions/search`
  - 用途：按订单状态或供应商查询物流列表。
  - 常用入参：`status`、`provider_id`、`limit`。
  - 状态枚举：`未付款`、`待发货`、`已发货`、`已到货`、`已退款`、`已退货`、`已取消`。

- `POST /delivery/cases`
  - 用途：为真实订单创建物流跟进 case。
  - 入参重点：`order_id`、`case_type`、`reason`、`created_by`。
