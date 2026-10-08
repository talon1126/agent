# Agent 工具权限矩阵

版本：`d3-tool-policy-v1`

## 授权顺序

每次工具调用都必须在实际 I/O 前依次通过以下门禁：

1. `step_id` 必须存在于已验证的 `AgentPlan`，且传入步骤与计划中的步骤完全一致。
2. 工具必须同时存在于服务端 step type 映射和当前步骤的 `allowed_tools`。
3. 步骤风险级别必须与服务端工具风险分类一致。
4. 参数必须通过对应 Pydantic schema；未知字段、超长字符串和错误标量类型直接拒绝。
5. `user_id`、`conversation_id` 必须匹配服务端请求上下文。
6. 商品详情、评论及动作的 `item_id` 必须来自本轮候选集合；URL 只允许公网 HTTPS，路径必须完整匹配 `/items/{item_id}` 或 `/ip/{item_id}`，且其中的商品 ID 也要通过候选来源检查。
7. 写工具还必须通过 E4 提供的确认令牌校验器。D3 阶段没有任何步骤获准调用 `cart_write`，因此保持默认拒绝。

任一条件失败均返回稳定拒绝码并停止执行，不存在“记录后继续”的软拒绝。

## 权限矩阵

| Plan step type | 可调用工具 | 访问模式 | 风险 | 额外约束 |
| --- | --- | --- | --- | --- |
| `product_search` | `product_search` | read | medium | 查询 1-512 字符，绑定用户和会话 |
| `snapshot` | `product_snapshot` | read | medium | 候选 item_id；公网 HTTPS 商品 URL |
| `review_fetch` | `product_reviews` | read | medium | 1-20 个候选 item_id |
| `rag_lookup` | `rag_lookup` | read | medium | collection 白名单格式；最多 8 个集合 |
| `action_preview` | `action_preview` | write_preview | high | 候选 item_id；数量 1-99；不产生业务写入 |
| `clarify` / `filter` / `rank` / `compare` / `compose` | 无 | - | low | 纯内存或模型步骤，不调用业务工具 |

## 默认拒绝能力

| 工具 | 分类 | D3 状态 | 后续接入条件 |
| --- | --- | --- | --- |
| `web_search` | read / medium | 无 PlanStep 授权 | 新增经任务书批准的 step contract 与服务端映射 |
| `order_lookup` | read / medium | 无 PlanStep 授权 | 新增经任务书批准的 step contract 与服务端映射 |
| `cart_write` | write / high | 所有步骤拒绝 | E4 同时增加计划授权、服务端映射和可信确认校验器 |

这些 schema 覆盖当前 Agent 入口的商品、订单、RAG、联网搜索能力以及 E4 预留的购物车写能力。旧版 Intent Router 白名单仍是第一层入口门禁；它不会因为本矩阵而扩大。D4 执行器必须在每次实际调用前执行 `StepPolicyGate.enforce()`。

## 信任边界

以下内容一律只作为数据，不是策略输入：用户消息、商品标题或描述、评论、RAG 文档、网页摘要、模型生成的计划参数。它们不能改写服务端权限矩阵、风险分类、候选集合或确认结果。

Trace 只记录拒绝码、step/tool 标识及参数键名和数量摘要，不记录查询原文、商品 ID、URL、令牌或 Pydantic 内部错误。公网域名的 DNS 重绑定防护属于 F1 的网络出口控制；D3 已拒绝非 HTTPS、凭证 URL、非 443 端口、回环/私网/链路本地 IP、数字 IP 变体、`.internal`/`.local` 主机，以及包含编码分隔符、反斜杠、额外路径段或点段的商品路径。
