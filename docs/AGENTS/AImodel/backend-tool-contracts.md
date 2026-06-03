# 后端工具契约

`AImodel` 的商品回答必须通过后端工具读取真实商品数据，不能让模型编造商品、价格、库存或链接。

## 商品详情工具

- 工具职责：从前端商品链接解析 `/items/{item_id}`，再调用 `mock-api` 的 `GET /ip/{item_id}`。
- 成功结果：返回 `ok=true`、`item_id` 和商品详情数据。
- 失败结果：链接无法解析、商品不存在或 `mock-api` 异常时，返回 `ok=false` 和错误原因，不中断其他工具调用。

## 商品搜索工具

- 工具职责：根据用户推荐意图生成搜索词，调用 `mock-api` 的 `GET /search?q={query}`。
- 成功结果：返回真实商品列表，并为每个商品根据 `item_id` 生成前端链接。
- 推荐约束：`recommended_links` 只能来自工具返回的真实商品。

## 链接生成

- 配置了 `FRONTEND_BASE_URL`：生成 `${FRONTEND_BASE_URL}/items/{item_id}`。
- 未配置 `FRONTEND_BASE_URL`：生成 `/items/{item_id}`。

实现时需要去掉 `FRONTEND_BASE_URL` 末尾的 `/`，避免双斜杠链接。
