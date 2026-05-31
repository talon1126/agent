# 业务数据库表

- `delivery_providers`
  - 物流供应商表。
  - 关键字段：`provider_id`、`provider_name`、`service_hotline`、`tracking_prefix`、`status`。
  - 当前内置供应商：顺丰、京东、圆通。

- `orders`
  - 物流只读取订单主表上的物流字段，不拥有该表。
  - 物流相关字段：`delivery_provider_id`、`delivery_provider_name`、`courier_phone`、`tracking_no`、`status`。
  - 订单状态：`未付款`、`待发货`、`已发货`、`已到货`、`已退款`、`已退货`、`已取消`。
