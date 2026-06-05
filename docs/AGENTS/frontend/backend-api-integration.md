# 后端接口对接

前端通常通过 Axios 调用后端 API，不直接访问 Postgres、n8n 或飞书表。AImodel 的 SSE 流式对话接口使用 `fetch` + `ReadableStream` 读取。

当前商品列表 / 搜索接口文档：`docs/development/search-api-integration.zh.md`。
当前商品详情接口文档：`docs/development/product-detail-api-integration.zh.md`。
当前商品评论接口文档：`docs/development/product-reviews-api-integration.zh.md`。
当前购物车 / 结算接口文档：`docs/development/cart-api-integration.zh.md`。
当前秒杀接口文档：`docs/development/flash-sale-api-integration.zh.md`。
当前 AImodel 设计文档：`docs/AImodel/design.md`。

本地代理约定：

- 商品、购物车、结算、秒杀等业务接口使用 `VITE_API_BASE_URL=/api`，由 Vite 代理到 `mock-api`。
- AImodel 对话接口使用 `VITE_AI_SERVICE_BASE_URL=/ai-service`，由 Vite 代理到 `ai-service`。
- 不要让 AImodel 复用普通 `apiClient` 的 `/api` base URL，否则 `/AImodel/chat` 会打到 `mock-api` 并返回 404。
- AImodel 不新增 `/chat/stream`，`POST /AImodel/chat` 直接返回 `text/event-stream`，前端需要解析 `status`、`delta`、`done`、`error` 事件。
- AImodel 前端类型不包含 `tool_results`；工具调用结果只留在后端内部，前端只消费回答文本和推荐商品链接。
- AImodel 回答展示需要按段落和列表做安全格式化，不直接使用 `v-html` 渲染模型输出。

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

商品详情相关接口：

- `GET /ip/{item_id}`：按商品 ID 读取商品详情，用于商品详情页。
- `GET /items/{item_id}/reviews`：按商品 ID 读取评论列表和汇总评分。
- `POST /items/{item_id}/reviews`：创建商品评论，当前前端沿用 `CART_USER_ID=1`。
- `item_id` 保持字符串，例如 `item_milk_pure`。
- 基础字段来自后端 `items` 表；`images`、`features`、`ingredients`、`description`、`details`、`rating`、`badges`、`fulfillment` 由 mock-api 生成，供详情页完整展示。
- 商品详情页图片区支持桌面端主图 hover 局部放大效果。
- 前端已新增 `/items/:item_id` 路由、`productDetailApi`、`productReviewApi` 服务封装和 `ProductDetailView`；搜索结果商品图和标题可跳转到详情页。
- 商品不存在时，后端返回 `404 item_not_found`，详情页展示 `Item not found` 错误态。

购物车结算相关接口：

- `POST /cart`：添加商品到购物车，后端按 `items.price` 写入可信价格。
- `GET /cart?user_id=1`：按用户读取购物车商品。
- `DELETE /cart?user_id=1&item_id=item_milk_pure`：移除用户购物车中的某个商品。
- `GET /delivery_addresses?user_id=1`：按用户读取配送地址，当前用于 `Continue to checkout` 前获取默认收货地址。
- `POST /warehouse/orders`：复用仓储订单接口创建订单；前端传 `shipping_address`，不传 `warehouse_id` / `location_code` 时由 Warehouse 按地址和库存选择仓库。

秒杀相关接口：

- `GET /flash-sales?status=active&limit=20`：读取秒杀活动列表，前端用 `flash_sales[].stock_remaining` 展示弱实时剩余数量。
- `GET /flash-sales/{flash_sale_id}`：读取活动详情和剩余营销库存，前端可用 `stock_remaining` 展示剩余数量。
- `POST /flash-sales/{flash_sale_id}/purchase`：用户抢购，后端通过 Redis Lua 保证营销库存扣减和一人一单；成功后立即返回 `未付款` 订单。
- `POST /flash-sales/{flash_sale_id}/activate`：测试或运营初始化活动，重置 Redis 剩余配额和已抢购用户集合。

注意：首页秒杀专区调用 `GET /flash-sales?status=active&limit=20`，不能硬编码 `flash_sale_id=1` 或其他活动 ID。前端按“用户每次刷新页面时重新查询库存”的策略加载活动，不做轮询。最终是否抢购成功以后端 `/purchase` 返回为准。
