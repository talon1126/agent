# 购物车接口前后端对接文档

本文档用于约定 TalonMart 前端与后端购物车功能的对接方式。当前目标是实现“添加商品到购物车”、“按用户查询购物车商品”、“从购物车移除商品”，并补充“购物车结算创建订单”的前后端契约。当前不处理登录鉴权、优惠、在线支付或真实配送。

## 当前数据库表

用户已在数据库中新增 `users`、`cart_items`，并计划新增 `delivery_addresses` 配送地址表。

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

### `delivery_addresses`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | integer | 配送地址主键，自增。 |
| `user_id` | integer | 用户 ID，对应 `users.id`。 |
| `receiver_name` | varchar(100) | 收货人。 |
| `phone_number` | varchar(32) | 收货人手机号。 |
| `address` | varchar(500) | 收货地址。创建订单时不能为空。 |
| `is_default` | integer | 是否默认地址。`1` 表示默认，`0` 表示非默认。 |

### 配送地址表规则

1. `user_id`、`receiver_name`、`phone_number`、`address` 必填。
2. `address` 不能为空字符串；前端点击 `Continue to checkout` 前必须校验。
3. `is_default` 只使用 `0` 和 `1`，不要使用布尔值字符串。
4. 同一个 `user_id` 推荐最多只有一条 `is_default=1` 的地址；后端新增或切换默认地址时应把该用户其他地址更新为 `0`。
5. 本文档只约定表结构、地址查询和购物车结算使用方式；配送地址的新增、编辑、删除接口后续另行设计。

## 接口范围

购物车 v1 包含以下接口和跨模块调用：

- `POST /cart`：添加商品到购物车。
- `GET /cart?user_id=1`：按用户查询购物车商品。
- `DELETE /cart?user_id=1&item_id=item_milk_pure`：从购物车移除某个商品。
- `GET /delivery_addresses?user_id=1`：查询用户配送地址。
- `POST /warehouse/orders`：点击 `Continue to checkout` 后创建订单。

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
| `price` | number | 是 | 前端传入当前展示价格；后端 v1 只做非负校验，最终写入购物车时以后端 `items.price` 为准。 |
| `quantity` | number | 否 | 添加数量，默认 1，必须大于 0。 |

### 业务规则

1. 如果 `user_id` 不存在，返回 404。
2. 如果 `quantity <= 0`，返回 400。
3. 如果 `price < 0`，返回 400。
4. 如果同一个 `user_id + item_id` 已存在购物车行，建议累加 `quantity`，不要重复插入多行。
5. 后端必须读取 `items.price` 作为可信价格来源，不能直接信任前端传入的 `price`。
6. 当前 `cart_items` 表没有唯一约束，后端应在业务逻辑中先查后写。后续可以补唯一约束 `unique(user_id, item_id)`。

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

## 点击 Continue to checkout 创建订单

购物车页面点击 `Continue to checkout` 后，前端需要先校验当前用户的收货地址，再把购物车商品转换为仓储订单接口所需的 `items[]`，调用现有 `POST /warehouse/orders` 创建订单。创建订单成功后跳回主页面 `/`。

当前使用固定测试用户：

```txt
user_id=1
```

### 查询配送地址

```http
GET /delivery_addresses?user_id=1
```

成功响应：

```json
{
  "ok": true,
  "user_id": 1,
  "count": 1,
  "items": [
    {
      "id": 1,
      "user_id": 1,
      "receiver_name": "Talon 测试用户",
      "phone_number": "13800000001",
      "address": "广东省深圳市南山区示例路 100 号",
      "is_default": 1
    }
  ]
}
```

前端展示规则：

1. 调用 `GET /delivery_addresses?user_id=1` 后展示地址卡片列表。
2. 优先选中 `is_default=1` 的地址；如果没有默认地址，选中第一条地址。
3. 用户可以在购物车页切换本次订单使用的地址。
4. 没有地址或选中地址的 `address` 为空时，点击 `Continue to checkout` 不创建订单。

### 前端流程

1. 读取当前用户购物车：`GET /cart?user_id=1`。
2. 查询当前用户收货地址：`GET /delivery_addresses?user_id=1`。
3. 校验 `address` 不能为空；如果为空，不调用创建订单接口，在购物车页展示错误提示。
4. 校验购物车不能为空；如果为空，不调用创建订单接口。
5. 将 `cart_items` 转换为 `POST /warehouse/orders` 的 `items[]`。
6. 调用 `POST /warehouse/orders`。
7. 创建订单成功后跳转主页面 `/`。

### 现有创建订单接口

当前创建订单复用仓储模块已有接口：

```http
POST /warehouse/orders
Content-Type: application/json
```

该接口的现有实现位于：

```txt
services/mock-api/app/routers/warehouse/orders.py
```

对应 Pydantic 请求模型为 `WarehouseOrderCreate`，字段来自：

```txt
services/mock-api/app/routers/warehouse/schemas.py
```

### 请求体

```json
{
  "customer_id": "1",
  "delivery_provider_id": "sf",
  "courier_phone": "",
  "shipping_address": "广东省深圳市南山区示例路 100 号",
  "items": [
    {
      "item_id": "item_milk_pure",
      "quantity": 2
    }
  ],
  "created_by": "talonmart-web"
}
```

### 请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `order_id` | string | 否 | 不传时后端自动生成，例如 `ORD-CODEX-1001`。 |
| `customer_id` | string | 是 | 当前测试阶段使用 `String(user_id)`，例如 `"1"`。 |
| `delivery_provider_id` | string | 否 | 物流供应商，默认 `sf`。当前可使用 `sf`。 |
| `courier_phone` | string | 否 | 快递员手机号，当前前端可传空字符串。 |
| `tracking_no` | string | 否 | 物流单号，不传时后端按供应商前缀自动生成。 |
| `shipping_address` | string | 是 | 收货地址，来自 `delivery_addresses.address`，不能为空。 |
| `items` | array | 是 | 订单商品行，来自购物车。 |
| `items[].item_id` | string | 是 | 商品 ID，来自 `cart_items.item_id`。 |
| `items[].quantity` | number | 是 | 商品数量，来自 `cart_items.quantity`，必须大于 0。 |
| `items[].warehouse_id` | string | 否 | 前端购物车结算不传，由后端按地址和库存选择仓库。 |
| `items[].location_code` | string | 否 | 前端购物车结算不传，由后端库存分配逻辑决定。 |
| `created_by` | string | 否 | 创建来源，前端传 `talonmart-web`。 |

### 后端现有实现行为

1. 如果未传 `order_id`，后端自动生成订单号。
2. 订单创建后初始状态为 `未付款`。
3. 后端会解析 `shipping_address` 中的省、市信息，例如 `广东省深圳市`。
4. 后端按整单同仓策略选择可满足库存的仓库；如果请求项显式传入多个不同仓库，会返回 400。
5. 创建订单时会写入 `orders` 和 `order_items`，并扣减 `inventory_location_balances`。
6. 付款接口 `POST /warehouse/orders/{order_id}/pay` 只更新状态，不再次扣减库存。
7. 发货、到货、取消、退货由仓储订单后续接口处理，购物车结算 v1 不调用这些接口。

### 成功响应

```json
{
  "ok": true,
  "order": {
    "order_id": "ORD-CODEX-1001",
    "customer_id": "1",
    "status": "未付款",
    "delivery_provider_id": "sf",
    "delivery_provider_name": "顺丰",
    "shipping_address": "广东省深圳市南山区示例路 100 号",
    "selected_warehouse_id": "wh_sz_1",
    "selected_warehouse_name": "深圳前置仓"
  },
  "items": [
    {
      "order_id": "ORD-CODEX-1001",
      "customer_id": "1",
      "status": "未付款",
      "item_id": "item_milk_pure",
      "warehouse_id": "wh_sz_1",
      "location_code": "A1",
      "quantity": 2
    }
  ]
}
```

### 前端错误处理

| 场景 | 前端处理 |
| --- | --- |
| 收货地址为空 | 不调用接口，在购物车页提示“请先填写收货地址”。 |
| 购物车为空 | 不调用接口，在购物车页提示“购物车为空”。 |
| 后端返回库存不足 | 停留购物车页，展示库存不足提示。 |
| 后端创建订单失败 | 停留购物车页，展示后端错误信息。 |
| 创建订单成功 | 跳转主页面 `/`。 |

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
9. 当前搜索接口返回 `items.price`，前端添加购物车时可以把该价格随请求发送；后端仍以后端 `items.price` 为准。
10. 点击 `Continue to checkout` 时必须先校验收货地址不为空，再调用 `POST /warehouse/orders` 创建订单。
11. 创建订单成功后跳转主页面 `/`，不在购物车页继续处理支付。

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

价格规则：`cart_items.price` 写入后端 `items.price`，`POST /cart` 请求体里的 `price` 只用于兼容前端 payload 和基础非负校验。

## 当前不做

- 不做登录、注册、JWT 或 session。
- 不做减少数量、清空购物车。
- 不新增支付接口。
- 不做价格优惠、税费、运费计算或在线支付结算。
- 不设计配送地址新增、编辑、删除接口；当前只查询地址并供购物车结算选择。
- 不让前端直接访问数据库。
