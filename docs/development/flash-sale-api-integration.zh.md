# 秒杀前后端对接文档

本文档记录 TalonMart 秒杀功能的前后端对接契约，供前端开发、后端联调和本地测试使用。

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

### 当前接口状态

当前后端已提供单活动查询、活动激活和抢购接口，暂未提供秒杀活动列表接口。

因此前端当前策略是：

1. 主页新增 `Flash Deals` 秒杀专区，但不硬编码 `flash_sale_id=1` 或其他活动 ID。
2. 秒杀专区先展示空状态和预留卡片，等待后端补齐列表接口后再渲染真实活动。
3. 前端已封装单活动查询和抢购接口，后续列表接口返回活动 ID 后可直接复用。
4. 前端查询库存策略为“用户每次刷新页面时查询一次”；不做轮询，不做前端倒计时扣库存。
5. 最终是否抢购成功以后端 `/flash-sales/{id}/purchase` 返回为准。

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

## 前端业务

### 主页秒杀专区

主页新增秒杀专区，参考 Walmart `Flash Deals` 的信息密度：

- 标题：`Flash Deals`。
- 副文案：提示折扣和刷新策略。
- 卡片区：当前只显示预留卡片和空状态，不展示真实商品。
- `View all`：列表接口补齐前禁用。

当前不能硬编码 `flash_sale_id=1`，原因是后端尚未提供列表接口，前端无法判断哪些活动应该展示在首页。

### 前端接口封装

推荐文件：

```txt
apps/talonmart-web/src/services/flashSaleApi.ts
apps/talonmart-web/src/types/flashSale.ts
```

已封装能力：

- `fetchFlashSale(flashSaleId)`：调用 `GET /flash-sales/{flash_sale_id}`。
- `purchaseFlashSale(flashSaleId, payload)`：调用 `POST /flash-sales/{flash_sale_id}/purchase`。
- `purchaseFlashSaleWithDefaultAddress(flashSaleId, userId)`：先查询默认配送地址，再发起抢购。

### 抢购地址规则

点击秒杀购买时，前端复用默认收货地址接口：

```http
GET /delivery_addresses?user_id=1
```

前端处理：

1. 优先使用 `is_default=1` 的地址。
2. 如果没有默认地址，使用返回列表第一条地址。
3. 如果没有地址或 `address` 为空，不调用抢购接口，提示用户先配置收货地址。
4. 调用 `/flash-sales/{id}/purchase` 时传入：

```json
{
  "user_id": 1,
  "shipping_address": "广东省深圳市南山区示例路 100 号",
  "delivery_provider_id": "sf"
}
```

### 后续接入列表接口

后端补齐列表接口后，前端应按以下方式接入：

1. 主页加载时调用列表接口。
2. 列表接口需要返回活动 ID、商品 ID、秒杀价、剩余秒杀库存、活动状态、开始和结束时间。
3. 前端每次页面刷新重新查询列表和库存，不复用旧缓存。
4. `stock_remaining <= 0` 时卡片按钮置为售罄。
5. `status !== active` 或活动未开始 / 已结束时卡片按钮置为不可购买。
6. 点击购买时调用 `purchaseFlashSaleWithDefaultAddress(activity.id, 1)`。

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

