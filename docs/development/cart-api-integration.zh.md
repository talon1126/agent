# 购物车接口前后端对接文档

本文档用于约定 TalonMart 前端与后端购物车功能的对接方式。当前目标是先实现“添加商品到购物车”、“按用户查询购物车商品”和“从购物车移除商品”，不处理登录鉴权、优惠、库存锁定、订单结算或支付。

## 当前数据库表

用户已在数据库中新增 `users` 和 `cart_items` 两张表。

### `users`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | integer | 用户主键。 |
| `phone_number` | varchar(32) | 手机号。 |
| `email` | varchar(255) | 邮箱。 |
| `username` | varchar(100) | 用户名。 |
| `password` | varchar(20) | 密码字段。当前仅记录现状，后续正式实现不应明文存储密码。 |

### `cart_items`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | integer | 购物车明细主键。 |
| `item_id` | varchar(64) | 商品 ID。 |
| `item_name` | varchar(255) | 商品名称。 |
| `user_id` | integer | 用户 ID，对应 `users.id`。 |
| `price` | numeric(12,2) | 商品加入购物车时的价格。 |
| `quantity` | integer | 数量，默认 1，必须大于 0。 |

## 接口范围

购物车 v1 只做三个接口：

- `POST /cart`：添加商品到购物车。
- `GET /cart?user_id=1`：按用户查询购物车商品。
- `DELETE /cart?user_id=1&item_id=item_milk_pure`：从购物车移除某个商品。

后端必须通过 `user_id` 过滤数据，只返回该用户自己的 `cart_items` 行。

## 添加商品到购物车

```http
POST /cart
Content-Type: application/json
```

### 请求体

```json
{
  "user_id": 1,
  "item_id": "item_milk_pure",
  "item_name": "纯牛奶",
  "price": 18.4,
  "quantity": 1
}
```

### 请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | number | 是 | 当前用户 ID。v1 由前端显式传入，后续接登录后改为从 token 获取。 |
| `item_id` | string | 是 | 商品 ID。 |
| `item_name` | string | 是 | 商品名称。 |
| `price` | number | 是 | 商品加入购物车时的价格。 |
| `quantity` | number | 否 | 添加数量，默认 1，必须大于 0。 |

### 业务规则

1. 如果 `user_id` 不存在，返回 404。
2. 如果 `quantity <= 0`，返回 400。
3. 如果 `price < 0`，返回 400。
4. 如果同一个 `user_id + item_id` 已存在购物车行，建议累加 `quantity`，不要重复插入多行。
5. 当前 `cart_items` 表没有唯一约束，后端应在业务逻辑中先查后写。后续可以补唯一约束 `unique(user_id, item_id)`。

### 成功响应

```json
{
  "ok": true,
  "item": {
    "id": 10,
    "user_id": 1,
    "item_id": "item_milk_pure",
    "item_name": "纯牛奶",
    "price": 18.4,
    "quantity": 2
  }
}
```

## 查询购物车商品

```http
GET /cart?user_id=1
```

### Query 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | number | 是 | 当前用户 ID。 |

### 成功响应

后端需要根据 `user_id` 返回 `cart_items` 中的字段。

```json
{
  "ok": true,
  "user_id": 1,
  "count": 1,
  "items": [
    {
      "id": 10,
      "user_id": 1,
      "item_id": "item_milk_pure",
      "item_name": "纯牛奶",
      "price": 18.4,
      "quantity": 2
    }
  ]
}
```

### 返回字段

| 字段 | 来源 | 类型 | 说明 |
| --- | --- | --- | --- |
| `id` | `cart_items.id` | number | 购物车明细 ID。 |
| `user_id` | `cart_items.user_id` | number | 用户 ID。 |
| `item_id` | `cart_items.item_id` | string | 商品 ID。 |
| `item_name` | `cart_items.item_name` | string | 商品名称。 |
| `price` | `cart_items.price` | number | 商品价格。 |
| `quantity` | `cart_items.quantity` | number | 购物车数量。 |

前端购物车数量可以通过 `items[].quantity` 求和：

```ts
const cartQuantity = items.reduce((sum, item) => sum + item.quantity, 0)
```

前端购物车金额可以通过 `price * quantity` 求和：

```ts
const cartTotal = items.reduce((sum, item) => sum + item.price * item.quantity, 0)
```

## 从购物车移除商品

```http
DELETE /cart?user_id=1&item_id=item_milk_pure
```

### Query 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | number | 是 | 当前用户 ID。 |
| `item_id` | string | 是 | 要移除的商品 ID。 |

### 业务规则

1. 后端必须同时按 `user_id` 和 `item_id` 删除，不能只按 `item_id` 删除。
2. 如果用户购物车中不存在该商品，返回 404 或返回 `removed=false`。v1 推荐返回 404，便于前端发现状态不一致。
3. `DELETE /cart` 的语义是移除整条购物车商品，不是数量减 1。
4. 如果后续要支持数量减 1，应新增或约定更新数量接口，不复用该删除接口表达减量。

### 成功响应

```json
{
  "ok": true,
  "removed": true,
  "user_id": 1,
  "item_id": "item_milk_pure"
}
```

## 错误响应

### 缺少用户 ID

```json
{
  "ok": false,
  "error": "missing_user_id",
  "message": "user_id is required"
}
```

### 用户不存在

```json
{
  "ok": false,
  "error": "user_not_found",
  "message": "user does not exist"
}
```

### 请求参数非法

```json
{
  "ok": false,
  "error": "invalid_cart_item",
  "message": "quantity must be greater than 0"
}
```

### 购物车商品不存在

```json
{
  "ok": false,
  "error": "cart_item_not_found",
  "message": "cart item does not exist"
}
```

## 前端处理约定

1. 商品卡片点击 `Add to cart` 时调用 `POST /cart`。
2. v1 暂时使用固定测试用户，例如 `user_id=1`。接入登录后再从用户会话读取。
3. 添加成功后，前端刷新 `GET /cart?user_id=1` 或更新本地购物车数量。
4. 添加失败时，在商品卡片或页面顶部展示错误提示，不静默失败。
5. 添加成功后，商品卡片按钮从 `Add to cart` 变为数量状态，例如 `1 added`、`2 added`。
6. 数量状态按钮参考 Walmart 交互：左侧 `-`，中间显示 `N added`，右侧 `+`。
7. 点击 `+` 时继续调用 `POST /cart`，后端累加该商品数量。
8. 点击 `-` 时如果当前数量大于 1，v1 可以继续调用 `POST /cart` 以负数减量前必须另行设计更新接口；当前文档尚未定义减量接口，所以前端 v1 推荐只在数量为 1 时调用 `DELETE /cart?user_id=1&item_id=...` 移除整条商品，数量大于 1 的减量功能等更新数量接口确定后再做。
9. 当前搜索接口不返回价格，前端如果要添加购物车，需要先明确价格来源。v1 可以使用前端 mock 价格或后端补商品价格字段，但正式实现应由后端返回可信价格。

## 后端实现建议

推荐在 `mock-api` 中新增购物车 router，不放入仓储、采购或物流 router。

推荐文件：

```txt
services/mock-api/app/routers/cart.py
```

推荐路由：

```txt
POST /cart
GET /cart
DELETE /cart
```

实现步骤：

1. 校验 `user_id` 是否存在于 `users`。
2. 添加购物车时校验 `item_id`、`item_name`、`price`、`quantity`。
3. 查询是否已有 `user_id + item_id` 的购物车行。
4. 已存在则累加数量；不存在则插入新行。
5. 查询购物车时按 `user_id` 过滤 `cart_items`。
6. 删除购物车商品时按 `user_id + item_id` 删除。
7. 返回字段保持和本文档一致。

## 当前不做

- 不做登录、注册、JWT 或 session。
- 不做减少数量、清空购物车。
- 不做库存锁定或库存扣减。
- 不做价格优惠、税费、运费或结算。
- 不让前端直接访问数据库。
