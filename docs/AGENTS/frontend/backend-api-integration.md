# 后端接口对接

前端通过 Axios 调用后端 API，不直接访问 Postgres、n8n 或飞书表。

当前商品列表 / 搜索接口文档：`docs/development/search-api-integration.zh.md`。
当前购物车 / 结算接口文档：`docs/development/cart-api-integration.zh.md`。

当前搜索接口：

- `GET /search?q=milk`
- `q` 通过 Postgres `pg_search` / BM25 匹配 `items.search_text`，来源包括 `items.item_id`、`items.item_name`、`items.brand`、`items.spec`。
- `q` 不匹配 `category_id`。
- 返回结果按 `item_id` 聚合。
- `item_id` 类型保持字符串，例如 `item_milk_pure`，不要为了前端展示改成 number。
- 商品价格来自 `items.price`，搜索结果返回 `items[].price`，购物车写入时以后端价格为准。
- 库存余额明细放在 `items[].balances[]`。
- 前端不接收 `total_quantity_on_hand`；总库存由前端从 `balances[].quantity_on_hand` 求和。

注意：当前 `q=milk` 可以通过 `item_id=item_milk_pure` 命中中文商品；后续如果需要“牛乳”等自然语言同义词，应设计别名或同义词表，不要在接口里临时硬编码翻译规则。

购物车结算相关接口：

- `POST /cart`：添加商品到购物车，后端按 `items.price` 写入可信价格。
- `GET /cart?user_id=1`：按用户读取购物车商品。
- `DELETE /cart?user_id=1&item_id=item_milk_pure`：移除用户购物车中的某个商品。
- `GET /delivery_addresses?user_id=1`：按用户读取配送地址，当前用于 `Continue to checkout` 前获取默认收货地址。
- `POST /warehouse/orders`：复用仓储订单接口创建订单；前端传 `shipping_address`，不传 `warehouse_id` / `location_code` 时由 Warehouse 按地址和库存选择仓库。
