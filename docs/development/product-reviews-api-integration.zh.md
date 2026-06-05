# 商品评论前后端对接文档

本文档记录 TalonMart 商品详情页评论功能的后端接口、数据表和前端展示要求。当前目标是先实现商品评价模型：每个 `item_id` 可以对应多条评论，每条评论包含星级评分、标题和正文。

## 目标

- 商品详情页新增评论栏目，展示该商品的用户评价列表。
- 后端新增商品评论表，评论通过 `item_id` 关联 `items.item_id`。
- 后端提供查询评论和创建评论接口。
- 商品详情页可提交评论，并在提交成功后刷新评论列表。
- 第一版不接登录系统，前端沿用当前用户约定 `user_id=1`。

## 数据表

### `item_reviews`

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int primary key autoincrement` | 评论自增主键。 |
| `item_id` | `varchar(64) not null` | 商品 ID，关联 `items.item_id`。 |
| `user_id` | `int not null` | 用户 ID，第一版前端固定使用 `1`。 |
| `rating` | `int not null` | 星级评分，范围 `1-5`。 |
| `title` | `varchar(120) not null` | 评论标题。 |
| `content` | `text not null` | 评论正文。 |
| `created_at` | `text not null` | 创建时间，ISO 字符串。 |
| `updated_at` | `text not null` | 更新时间，ISO 字符串。 |

建议索引：

- `idx_item_reviews_item_id`：用于按商品查询评论。
- `idx_item_reviews_created_at`：用于按时间倒序展示。

约束：

- `rating` 必须在 `1-5`。
- `title` 去除首尾空格后不能为空，建议最大长度 120。
- `content` 去除首尾空格后不能为空，建议最大长度 2000。
- 商品不存在时不能创建评论。

## 后端接口

### 查询商品评论

```http
GET /items/{item_id}/reviews?limit=20&offset=0
```

路径参数：

- `item_id`：商品 ID，例如 `item_milk_pure`。

查询参数：

- `limit`：每页数量，默认 `20`，范围 `1-100`。
- `offset`：分页偏移，默认 `0`。

成功返回：

```json
{
  "ok": true,
  "item_id": "item_milk_pure",
  "count": 2,
  "summary": {
    "average_rating": 4.5,
    "review_count": 2
  },
  "reviews": [
    {
      "id": 2,
      "item_id": "item_milk_pure",
      "user_id": 1,
      "rating": 5,
      "title": "Good value",
      "content": "Fresh taste and good price for a family pack.",
      "created_at": "2026-06-03T10:00:00+08:00",
      "updated_at": "2026-06-03T10:00:00+08:00"
    }
  ]
}
```

失败返回：

```json
{
  "ok": false,
  "error": "item_not_found",
  "message": "Item not found."
}
```

状态码：

- `200`：查询成功。
- `404`：商品不存在。
- `503`：Postgres 后端不可用。

### 创建商品评论

```http
POST /items/{item_id}/reviews
Content-Type: application/json
```

请求体：

```json
{
  "user_id": 1,
  "rating": 5,
  "title": "Good value",
  "content": "Fresh taste and good price for a family pack."
}
```

成功返回：

```json
{
  "ok": true,
  "review": {
    "id": 10,
    "item_id": "item_milk_pure",
    "user_id": 1,
    "rating": 5,
    "title": "Good value",
    "content": "Fresh taste and good price for a family pack.",
    "created_at": "2026-06-03T10:00:00+08:00",
    "updated_at": "2026-06-03T10:00:00+08:00"
  }
}
```

失败返回：

```json
{
  "ok": false,
  "error": "invalid_review",
  "message": "Rating must be between 1 and 5."
}
```

状态码：

- `200`：创建成功。
- `400`：评分、标题或正文不合法。
- `404`：商品不存在。
- `503`：Postgres 后端不可用。

## mock-api 实现要求

- 在 `services/mock-api/app/warehouse_store.py` 中新增 `item_reviews` 表定义。
- `init_warehouse_schema` 需要创建该表，并在 Postgres 模式下补充表字段注释。
- `seed_warehouse_fixtures` 可以插入少量真实测试评论，用于前端本地展示。
- `WarehouseRepository` 新增：
  - `list_item_reviews(item_id, limit, offset)`
  - `create_item_review(item_id, payload)`
  - `item_review_summary(item_id)`
- 新增 router，建议文件：
  - `services/mock-api/app/routers/product_reviews.py`
- `main.py` include 新 router。

## 前端接口封装

建议新增：

```txt
apps/talonmart-web/src/services/productReviewApi.ts
apps/talonmart-web/src/types/productReview.ts
```

类型建议：

```ts
export interface ProductReview {
  id: number
  item_id: string
  user_id: number
  rating: number
  title: string
  content: string
  created_at: string
  updated_at: string
}

export interface ProductReviewSummary {
  average_rating: number
  review_count: number
}
```

接口封装：

- `fetchItemReviews(itemId, params)`：调用 `GET /items/{item_id}/reviews`。
- `createItemReview(itemId, payload)`：调用 `POST /items/{item_id}/reviews`。

## 商品详情页展示

在 `ProductDetailView.vue` 中新增评论栏目，建议放在商品主内容下方、推荐或详情模块之后。

展示内容：

- 评论区标题：`Customer reviews`。
- 汇总评分：平均星级和评论数量。
- 评论列表：星级、标题、正文、用户 ID、创建时间。
- 空状态：`No reviews yet`。
- 创建评论表单：
  - 星级评分 `1-5`。
  - 标题输入框。
  - 正文 textarea。
  - 提交按钮。

交互规则：

- 页面加载商品详情后并行或随后加载评论。
- 评论提交成功后清空表单，并刷新评论列表和汇总评分。
- 提交失败时显示后端 `message`。
- 当前用户固定使用 `CART_USER_ID`，即 `1`。

## 测试计划

后端测试：

- seed 后 `item_reviews` 表存在，并有测试评论。
- `GET /items/{item_id}/reviews` 返回对应商品评论和汇总评分。
- 查询不存在商品返回 `404 item_not_found`。
- `POST /items/{item_id}/reviews` 成功创建评论。
- `rating < 1` 或 `rating > 5` 返回 `400 invalid_review`。
- 空标题或空正文返回 `400 invalid_review`。

前端测试：

- 商品详情页加载后调用评论查询接口。
- 有评论时展示平均评分、评论数量和评论正文。
- 无评论时展示空状态。
- 提交评论时调用创建接口，成功后刷新列表。
- 创建接口返回错误时展示错误文案。

## 联调步骤

1. 重建 mock-api。
2. 访问：

```http
GET http://localhost:8002/items/item_milk_pure/reviews
```

预期：返回 `ok=true`、`summary` 和评论列表。

3. 创建评论：

```bash
curl -X POST http://localhost:8002/items/item_milk_pure/reviews \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":1,\"rating\":5,\"title\":\"Good value\",\"content\":\"Fresh taste and good price for a family pack.\"}"
```

预期：返回 `ok=true` 和新评论。

4. 前端访问：

```txt
/items/item_milk_pure
```

预期：详情页展示评论栏目，并支持提交新评论。
