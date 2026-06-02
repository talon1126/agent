# 秒杀接口对接

本文档记录 TalonMart 秒杀接口的后端契约，供前端联调和本地测试使用。

## 业务模型

- `flash_sales`：秒杀活动表，一条活动绑定一个商品。
- `flash_sale_claims`：秒杀抢购结果表，记录用户、活动、商品和关联订单。
- `flash_sales.stock_limit` 是独立营销秒杀库存配额，不代表真实仓储库存。
- 秒杀成功后会立即复用 `/warehouse/orders` 创建 `未付款` 订单，并扣减 `inventory_location_balances`。
- 同一 `flash_sale_id + user_id` 只允许成功一次。

## Redis 约定

- `flash_sale:{id}:stock`：活动剩余营销库存。
- `flash_sale:{id}:users`：已抢购用户集合。
- `/flash-sales/{id}/purchase` 使用 Redis Lua 原子执行检查、扣减和写用户集合。
- 如果 Redis 扣减成功但订单创建失败，后端会回补 Redis 库存并移除用户集合。

## 接口

### 查询活动列表

```http
GET /flash-sales?status=active&limit=20
```

查询参数：

- `status`：可选，按活动状态过滤，例如 `active`、`draft`、`ended`、`disabled`。
- `limit`：可选，默认 20，范围 1 到 100。

成功返回：

```json
{
  "ok": true,
  "count": 2,
  "flash_sales": [
    {
      "id": 1,
      "item_id": "item_milk_pure",
      "sale_price": 9.9,
      "stock_limit": 5,
      "stock_remaining": 4,
      "status": "active",
      "starts_at": "2026-06-02T00:00:00+00:00",
      "ends_at": "2099-06-03T00:00:00+00:00"
    }
  ]
}
```

说明：

- `stock_remaining` 来自 Redis，前端可用于弱实时展示剩余数量。
- 如果某条活动还没有初始化 Redis 库存，列表中该条 `stock_remaining` 返回 `null`。
- 前端最终应以 `POST /flash-sales/{id}/purchase` 的返回结果判断是否抢购成功。

### 查询活动

```http
GET /flash-sales/1
```

成功返回：

```json
{
  "ok": true,
  "flash_sale": {
    "id": 1,
    "item_id": "item_milk_pure",
    "sale_price": 9.9,
    "stock_limit": 5,
    "stock_remaining": 5,
    "status": "active",
    "starts_at": "2026-06-02T00:00:00+00:00",
    "ends_at": "2026-06-03T00:00:00+00:00"
  }
}
```

### 激活活动

```http
POST /flash-sales/1/activate
```

用途：把活动状态更新为 `active`，并按 `stock_limit` 初始化 Redis 剩余库存。

注意：重复调用会重置 Redis 剩余配额和已抢购用户集合，只建议测试或活动开始前使用。

### 参与秒杀

```http
POST /flash-sales/1/purchase
Content-Type: application/json

{
  "user_id": 1,
  "shipping_address": "广东省深圳市南山区示例路 100 号",
  "delivery_provider_id": "sf"
}
```

成功返回重点：

```json
{
  "ok": true,
  "claim": {
    "flash_sale_id": 1,
    "user_id": 1,
    "item_id": "item_milk_pure",
    "status": "ordered",
    "order_id": "ORD-CODEX-9001"
  },
  "order": {
    "order_id": "ORD-CODEX-9001",
    "status": "未付款",
    "customer_id": "1"
  },
  "items": [
    {
      "item_id": "item_milk_pure",
      "quantity": 1
    }
  ]
}
```

常见失败：

- `409 already_claimed`：同一用户已经抢过该活动。
- `409 sold_out`：营销秒杀库存已抢完。
- `409 flash_sale_not_active`：活动未激活、未开始或已结束。
- `503 flash_sale_not_initialized`：活动未初始化 Redis 库存，需要先调用激活接口。

## 本地测试步骤

1. 插入测试活动。

```sql
INSERT INTO flash_sales (
  item_id,
  sale_price,
  stock_limit,
  status,
  starts_at,
  ends_at,
  created_at,
  updated_at
) VALUES (
  'item_milk_pure',
  9.90,
  5,
  'draft',
  '2026-06-02T00:00:00+00:00',
  '2099-06-03T00:00:00+00:00',
  now()::text,
  now()::text
)
RETURNING id;
```

2. 激活活动。

```bash
curl -X POST http://localhost:8002/flash-sales/1/activate
```

3. 查询剩余数量。

```bash
curl http://localhost:8002/flash-sales/1
```

预期：`stock_remaining` 等于 `stock_limit`。

4. 用户抢购。

```bash
curl -X POST http://localhost:8002/flash-sales/1/purchase \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":1,\"shipping_address\":\"广东省深圳市南山区示例路 100 号\",\"delivery_provider_id\":\"sf\"}"
```

预期：

- 返回 `ok=true`。
- 返回订单状态为 `未付款`。
- `flash_sale_claims.status=ordered`。
- `inventory_location_balances` 中对应商品库存减少 1。
- 再次用同一 `user_id` 请求会返回 `already_claimed`。

