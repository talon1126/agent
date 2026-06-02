# 商品详情前后端对接文档

本文档记录 TalonMart 商品详情页的前后端接口契约和前端展示要求。当前后端已实现 `GET /ip/{item_id}`，前端已实现商品详情页和接口封装。

## 目标页面

商品详情页参考 Walmart 商品展示页的信息结构：

- 左侧展示商品图片缩略图列表和主图。
- 鼠标放到主图上时，显示局部图片放大效果。
- 中间展示品牌、商品名、评分、商品卖点、配料、规格等内容。
- 右侧展示价格、加入购物车按钮、购买方式和配送方式。
- 页面后续可从搜索结果页、秒杀专区、购物车推荐等入口跳转进入。

## 接口

### 查询商品详情

```http
GET /ip/{item_id}
```

路径参数：

- `item_id`：商品 ID，保持字符串类型，例如 `item_milk_pure`。

请求示例：

```http
GET /ip/item_milk_pure
```

成功返回建议：

```json
{
  "ok": true,
  "item": {
    "item_id": "item_milk_pure",
    "item_name": "Pure milk 1L multipack",
    "brand": "Talon Value",
    "spec": "1L x 6",
    "category_id": "dairy",
    "price": 18.4,
    "currency": "USD",
    "images": [
      {
        "url": "https://example.com/items/item_milk_pure/front.png",
        "alt": "Pure milk front view",
        "sort_order": 1
      }
    ],
    "rating": {
      "score": 4.6,
      "count": 1280
    },
    "badges": ["Overall pick"],
    "features": [
      "Pure milk for everyday use",
      "Suitable for cereal, coffee, cooking, and baking"
    ],
    "ingredients": "Milk.",
    "description": "Fresh pure milk for daily household needs.",
    "details": [
      {
        "label": "Flavor",
        "value": "Original"
      }
    ],
    "fulfillment": {
      "shipping_available": false,
      "pickup_available": true,
      "delivery_available": true,
      "pickup_message": "As soon as today",
      "delivery_message": "As soon as tomorrow"
    }
  }
}
```

说明：

- `item.item_id` 必须和路径中的 `{item_id}` 一致。
- `item_id` 不转换为 number，前端路由和购物车接口继续使用字符串 ID。
- `price` 用于页面展示；加入购物车时以后端 `POST /cart` 写入价格为准。
- `images` 至少返回 1 张主图；如果没有多图，前端只展示主图，不展示缩略图列表。
- `item_id`、`item_name`、`brand`、`spec`、`category_id`、`price` 来自 Postgres `items` 表。
- `images`、`features`、`ingredients`、`description`、`details`、`rating`、`badges`、`fulfillment` 当前由 mock-api 根据商品基础字段确定性生成，供前端详情页完整展示。

失败返回建议：

```json
{
  "ok": false,
  "error": "item_not_found",
  "message": "Item not found."
}
```

状态码建议：

- `200`：查询成功。
- `404`：商品不存在。
- `500`：服务端异常。

## 后端字段实现

当前商品详情页字段分两层：

基础字段来自 `items` 表：

- `item_id`
- `item_name`
- `brand`
- `spec`
- `category_id`
- `price`

展示字段由 mock-api 生成：

- 商品图片：`images[]`。
- 商品卖点：`features[]`。
- 商品配料：`ingredients`。
- 商品详情描述：`description`。
- 商品规格明细：`details[]`。
- 商品评分：`rating.score`、`rating.count`。
- 商品标签：`badges[]`。
- 履约展示信息：`fulfillment`。

后续如果需要运营可编辑商品详情，再新增商品详情扩展表；当前不新增表，避免影响既有商品搜索和购物车流程。

## 前端业务

### 路由建议

前端商品详情页建议使用：

```txt
/items/:item_id
```

页面加载时调用：

```ts
GET /ip/{item_id}
```

说明：

- 搜索结果卡片、秒杀卡片、购物车商品名后续都可以跳转到 `/items/{item_id}`。
- 前端路由路径不要求和后端接口路径一致。

### 图片展示

参考截图中的 Walmart 商品图区域：

- 左侧缩略图列表展示多张商品图。
- 中间主图展示当前选中图片。
- 点击缩略图切换主图。
- 鼠标 hover 主图时，显示局部放大预览。
- 放大预览不修改图片实际尺寸，只通过鼠标坐标计算背景位置或裁剪区域。
- 图片加载失败时展示占位图和商品名。

### 图片放大交互

前端实现建议：

1. 主图容器监听 `mouseenter`、`mousemove`、`mouseleave`。
2. `mouseenter` 时显示放大浮层。
3. `mousemove` 时根据鼠标在主图中的相对坐标更新放大区域。
4. `mouseleave` 时隐藏放大浮层。
5. 放大浮层在桌面端显示；如果后续做移动端，移动端可改为点击打开全屏图片查看。

### 加入购物车

商品详情页的 `Add to cart` 按钮复用现有购物车接口：

```http
POST /cart
```

请求体继续使用现有购物车契约，`user_id` 暂时固定为 `1`，`item_id` 使用详情接口返回的 `item.item_id`。

### 空状态和错误状态

- 商品不存在：展示 `Item not found`，提供返回搜索页或首页入口。
- 图片为空：展示商品占位图。
- `features` 为空：隐藏 `Key item features` 模块。
- `ingredients` 为空：隐藏或折叠 `Ingredients` 模块。
- `details` 为空：隐藏或折叠 `Specs` 模块。

## 联调步骤

1. 访问 `GET /ip/item_milk_pure`，预期返回 `ok=true` 和完整 `item` 对象。
2. 访问 `GET /ip/item_missing`，预期返回 `404 item_not_found`。
3. 前端访问 `/items/item_milk_pure`，预期商品详情页展示商品基础信息、主图、卖点、规格和履约信息。
4. 验证主图 hover 时出现局部放大效果。
5. 验证点击 `Add to cart` 后购物车数量变化。
