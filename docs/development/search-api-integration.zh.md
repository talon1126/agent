# 商品列表 / 搜索接口前后端对接文档

本文档用于约定 TalonMart 前端与后端 `GET /search` 商品搜索接口的对接方式。当前目标是先支持商品列表和关键词搜索，不引入 Elasticsearch，不暴露批次、库位、临期风险等消费者不需要理解的仓储内部字段。

## 接口定位

`GET /search` 是消费者商城前台使用的商品搜索读接口。

接口负责把商品主数据和库存余额明细组合成前端商品列表可用的数据结构。前端展示时按商品聚合；库存余额明细放在商品记录的 `balances[]` 中。

该接口不是仓储运营查询接口，不直接复用 `POST /warehouse/inventory/search` 的批次级返回结构。

## 请求

```http
GET /search?q=milk
```

### Query 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `q` | string | 是 | 搜索关键词。后端匹配 `items.item_id`、`items.item_name`、`items.brand`、`items.spec`。 |

### 搜索匹配规则

`q` 只匹配以下字段：

- `items.item_id`
- `items.item_name`
- `items.brand`
- `items.spec`

当前不把 `category_id` 纳入关键词匹配。`item_id` 用于支持 `milk` 这类英文关键词命中 `item_milk_pure`。分类后续如需支持，应作为独立筛选条件设计，例如 `GET /search?q=milk&category_id=dairy`。

## 返回结构

返回结果按 `item_id` 聚合。每条外层记录代表一个商品，库存余额明细放在 `balances[]`。

```json
{
  "ok": true,
  "query": "milk",
  "count": 1,
  "items": [
    {
      "item_id": "item_milk_pure",
      "item_name": "Pure Milk",
      "brand": "Talon Fresh",
      "spec": "1L x 12",
      "category_id": "cat_dairy",
      "balances": [
        {
          "id": 12,
          "warehouse_id": "wh_hk_1",
          "item_id": "item_milk_pure",
          "quantity_on_hand": 80,
          "storage_status": "available"
        },
        {
          "id": 13,
          "warehouse_id": "wh_sz_1",
          "item_id": "item_milk_pure",
          "quantity_on_hand": 40,
          "storage_status": "available"
        }
      ]
    }
  ]
}
```

### 顶层字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ok` | boolean | 请求是否成功。 |
| `query` | string | 后端实际使用的搜索关键词。 |
| `count` | number | 返回的商品数量，不是库存余额行数量。 |
| `items` | array | 商品聚合结果列表。 |

### `items[]` 字段

| 字段 | 来源 | 类型 | 说明 |
| --- | --- | --- | --- |
| `item_id` | `items.item_id` | string | 商品 ID。 |
| `item_name` | `items.item_name` | string | 商品名称。 |
| `brand` | `items.brand` | string | 品牌。 |
| `spec` | `items.spec` | string | 规格。 |
| `category_id` | `items.category_id` | string | 分类 ID。 |
| `balances` | `inventory_location_balances` | array | 该商品对应的库存余额明细。 |

### `balances[]` 字段

| 字段 | 来源 | 类型 | 说明 |
| --- | --- | --- | --- |
| `id` | `inventory_location_balances.id` | number | 库存余额记录 ID。 |
| `warehouse_id` | `inventory_location_balances.warehouse_id` | string | 仓库 ID。 |
| `item_id` | `inventory_location_balances.item_id` | string | 商品 ID，应与外层 `item_id` 一致。 |
| `quantity_on_hand` | `inventory_location_balances.quantity_on_hand` | number | 当前库存余额。 |
| `storage_status` | `inventory_location_balances.storage_status` | string | 库存状态，例如 `available`。 |

## 前端处理约定

前端不接收 `total_quantity_on_hand` 字段。商品总库存由前端从 `balances[]` 自行求和：

```ts
const totalQuantityOnHand = product.balances.reduce(
  (sum, balance) => sum + balance.quantity_on_hand,
  0,
)
```

前端商品卡片建议展示：

- 商品名称：`item_name`
- 品牌：`brand`
- 规格：`spec`
- 分类：`category_id`
- 总库存：前端根据 `balances[]` 求和
- 库存状态：根据 `balances[].storage_status` 和库存数量转换为消费者可理解文案

消费者页面不展示 `inventory_location_balances.id`。该字段主要用于前后端调试、后续库存明细展开、或者定位具体库存余额记录。

## 后端实现建议

后端实现时建议在 `mock-api` 中新增消费者商品搜索读接口，不要把该接口放入采购或物流路由。

推荐路由：

```txt
GET /search
```

推荐数据组合逻辑：

1. 从 `items` 表按 `item_id`、`item_name`、`brand`、`spec` 匹配关键词。
2. 拿到匹配商品的 `item_id` 集合。
3. 查询 `inventory_location_balances` 中对应 `item_id` 的余额行。
4. 按 `item_id` 聚合成商品列表。
5. 每个商品外层返回商品主数据，`balances[]` 返回库存余额明细。

如果 `q` 为空，后端应返回 400 错误，避免无条件扫全表。

## 错误响应

### 缺少关键词

```http
GET /search
```

```json
{
  "ok": false,
  "error": "missing_query",
  "message": "q is required"
}
```

### 无结果

无结果不算错误，返回空列表：

```json
{
  "ok": true,
  "query": "unknown",
  "count": 0,
  "items": []
}
```

## TypeScript 类型

```ts
export interface SearchBalance {
  id: number
  warehouse_id: string
  item_id: string
  quantity_on_hand: number
  storage_status: string
}

export interface SearchProduct {
  item_id: string
  item_name: string
  brand: string
  spec: string
  category_id: string
  balances: SearchBalance[]
}

export interface SearchResponse {
  ok: true
  query: string
  count: number
  items: SearchProduct[]
}
```

## 当前不做

- 不引入 Elasticsearch。
- 不返回 `total_quantity_on_hand`。
- 不把 `category_id` 纳入 `q` 的关键词匹配。
- 不在消费者搜索结果里返回批次号、库位、临期风险或补货状态。
- 不让前端直接访问 Postgres 或飞书表。
