# 本地运行手册

## 启动

```powershell
docker compose up --build -d
```

## 健康检查

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8010/health
Invoke-RestMethod http://localhost:8002/orders/ord_100
```

## n8n

打开 `http://localhost:5678`。

Compose 文件使用 `docker.n8n.io/n8nio/n8n:stable`，跟随 n8n 的稳定 Docker 发布通道。当前已验证的容器版本是 `2.20.11`。

将 `n8n/workflows/ecommerce-after-sales.json` 导入 n8n。该 workflow 暴露的 webhook 路径是：

```text
POST /webhook/after-sales-event
```

如果使用 Docker 容器中的 CLI 导入：

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/ecommerce-after-sales.json
docker compose exec -T n8n n8n publish:workflow --id=wf_ecommerce_after_sales
docker compose exec -T n8n n8n update:workflow --id=wf_ecommerce_after_sales --active=true
docker compose restart n8n
```

如果还要导入 message-agent workflow：

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/message-agent.json
docker compose exec -T n8n n8n publish:workflow --id=wf_message_agent
docker compose restart n8n
```

导入 Feishu Gateway Adapter 使用的部门 chat workflows：

```powershell
docker compose exec -T n8n n8n import:workflow --input=/workflows/customer-support-workflow.json
docker compose exec -T n8n n8n import:workflow --input=/workflows/warehouse-workflow.json
docker compose exec -T n8n n8n import:workflow --input=/workflows/procurement-workflow.json
docker compose exec -T n8n n8n import:workflow --input=/workflows/operations-workflow.json
docker compose exec -T n8n n8n import:workflow --input=/workflows/delivery-workflow.json
docker compose exec -T n8n n8n publish:workflow --id=customer-support-workflow
docker compose exec -T n8n n8n publish:workflow --id=warehouse-workflow
docker compose exec -T n8n n8n publish:workflow --id=procurement-workflow
docker compose exec -T n8n n8n publish:workflow --id=operations-workflow
docker compose exec -T n8n n8n publish:workflow --id=delivery-workflow
docker compose restart n8n
```

`n8n/workflows/chat-parent-son-agent.json` 仍保留为历史兼容 workflow，新内部聊天接入建议使用部门 workflow。

## 发送 Demo 事件

```powershell
./scripts/send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

如果本机 PowerShell 禁用了脚本执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

## 发送 Demo 消息

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -MessageFile fixtures\messages\order_status_text.json
```

如果想绕过 n8n，直接调用 AI service：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -Url http://localhost:8001/message/handle -MessageFile fixtures\messages\order_status_text.json
```

带 transcript 的音频形态消息可以这样测试：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_message.ps1 -Url http://localhost:8001/message/handle -MessageFile fixtures\messages\order_status_audio_transcript.json
```

## 飞书 Adapter

默认真实飞书接入使用长连接模式：

```text
FEISHU_EVENT_MODE=long_connection
```

多个部门机器人使用一个 gateway adapter，通过 `FEISHU_BOTS_JSON` 配置：

```text
FEISHU_BOTS_JSON=[{"name":"customer_support","app_id":"cli_customer","app_secret":"secret_customer","bot_open_id":"ou_customer_bot","n8n_webhook_url":"http://n8n:5678/webhook/customer-support-inbound"},{"name":"warehouse","app_id":"cli_warehouse","app_secret":"secret_warehouse","bot_open_id":"ou_warehouse_bot","n8n_webhook_url":"http://n8n:5678/webhook/warehouse-inbound"},{"name":"procurement","app_id":"cli_procurement","app_secret":"secret_procurement","bot_open_id":"ou_procurement_bot","n8n_webhook_url":"http://n8n:5678/webhook/procurement-inbound"},{"name":"operations","app_id":"cli_operations","app_secret":"secret_operations","bot_open_id":"ou_operations_bot","n8n_webhook_url":"http://n8n:5678/webhook/operations-inbound"},{"name":"delivery","app_id":"cli_delivery","app_secret":"secret_delivery","bot_open_id":"ou_delivery_bot","n8n_webhook_url":"http://n8n:5678/webhook/delivery-inbound"}]
```

`FEISHU_BOTS_JSON` 留空时，adapter 会继续使用旧的单机器人 fallback：`FEISHU_APP_ID`、`FEISHU_APP_SECRET` 和 `N8N_CHAT_WEBHOOK_URL`。

如果多个部门机器人安装在同一个飞书群里，发消息时需要 @ 目标机器人。adapter 会用 `bot_open_id` 和事件里的 `mentions` 列表做过滤，避免一条群消息触发所有部门 workflow。多 bot 模式下，没有 @ 的群消息会被忽略；单独私聊某个机器人仍然不需要 @。

启动或重建 adapter：

```powershell
docker compose up -d --build feishu-adapter
docker compose logs -f feishu-adapter
Invoke-RestMethod http://localhost:8010/health/details | ConvertTo-Json -Depth 10
```

预期启动日志：

```text
started feishu long connection listener bot=<bot_name>
connected to wss://msg-frontier.feishu.cn/ws/v2...
```

真实机器人消息到达时，adapter 会依次输出 `received feishu long connection event`、`forwarded ... bot=<bot_name> ... to n8n`、`replied to feishu`。群聊里被跳过的消息会记录为 `skipping group feishu event ... because bot was not mentioned` 或 `skipping unmentioned group feishu event ...`。adapter 会按 `bot_name + message_id` 对重复推送做去重。

配置 `FEISHU_RUN_LOG_URL` 后，每条处理完成的飞书消息都会写入结构化 run log，包含 `message_id`、`bot_name`、`workflow`、`status`、耗时字段、`error`，以及 workflow 返回的 `tool_calls`。

`mock-api` 启动时如果配置了 `DATABASE_URL`，会从 fixtures 创建并 seed 仓储相关 PostgreSQL 表。可以这样验证：

```powershell
docker compose exec -T postgres psql -U agent -d agent_ops -c "\dt"
docker compose exec -T postgres psql -U agent -d agent_ops -c "select warehouse_id, location_code, item_id, batch_no, quantity_on_hand, quantity_reserved, storage_status from inventory_batches order by warehouse_id, location_code, item_id limit 10;"
```

仓储 endpoint 仍然统一通过 `mock-api` 访问；n8n 和 `feishu-adapter` 不应该直接读取这些表。

仓储用户可以明确要求创建固定 schema 的飞书库存表，并把单个商品或过滤后的批次快照同步进去。先配置 `FEISHU_INVENTORY_TABLE_APP_TOKEN` 和表格应用凭证，然后创建表：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"table_name":"Warehouse Inventory Snapshot"}' http://localhost:8010/warehouse/inventory-table/provision
```

把返回的 `table_id` 填回 `.env` 的 `FEISHU_INVENTORY_TABLE_ID`，重启 `feishu-adapter`，再测试单商品同步：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"item_id":"item_vinda_tissue"}' http://localhost:8010/warehouse/inventory-table/sync
```

不依赖 Agent 推理，直接测试过滤批次同步：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"warehouse_id":"wh_sz_1","location_code":"A1","category":"dairy","expiry_risk":"expiring_soon","limit":50}' http://localhost:8010/warehouse/inventory-table/sync/filter
```

进入完整 n8n/LLM 链路前，可以先测试确定性的仓储意图路由：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"message":"帮我更新深圳仓A1库位乳制品临期库存"}' http://localhost:8010/warehouse/intents/route | ConvertTo-Json -Depth 10
```

飞书表格只是只读快照/read model，不要把表格编辑当作库存主数据。

采购用户可以把补货请求和采购草稿单同步到同一个 Base 下的两张飞书表。复用 `FEISHU_INVENTORY_TABLE_APP_ID`、`FEISHU_INVENTORY_TABLE_APP_SECRET`、`FEISHU_INVENTORY_TABLE_APP_TOKEN`；也可以把返回的表 ID 填到 `FEISHU_PROCUREMENT_REPLENISHMENT_REQUEST_TABLE_ID` 和 `FEISHU_PROCUREMENT_PURCHASE_ORDER_DRAFT_TABLE_ID`。

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{}' http://localhost:8010/procurement/replenishment-requests-table/provision
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{}' http://localhost:8010/procurement/purchase-order-drafts-table/provision
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{}' http://localhost:8010/procurement/replenishment-requests-table/sync
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{}' http://localhost:8010/procurement/purchase-order-drafts-table/sync
```

Procurement bot 的端到端测试话术是：`@procurement 同步补货请求`、`@procurement 同步采购草稿`、`@procurement 批量批准生成采购草稿单`。

采购草稿到货后，可以批量确认到仓。确认动作会把 `purchase_order_drafts.status` 更新为 `received_at_warehouse`，并在 `inventory_batches` 写入 `RCV-POD-*` 入库批次；返回值会包含需要 Warehouse 同步库存飞书视图的 `item_id`、仓库和库位。当前推荐由用户或后续事件任务通知 Warehouse，例如：`@warehouse 同步 item_vinda_tissue 库存到飞书`。

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"po_draft_ids":["POD-5001","POD-5002"],"received_by":"warehouse:user-001"}' http://localhost:8002/procurement/purchase-order-drafts/confirm-arrival-batch | ConvertTo-Json -Depth 10
```

Procurement bot 到仓确认测试话术是：`@procurement POD-5001,POD-5002 已到仓库`。

物流用户可以查询 mock 物流状态、汇总延迟或丢件运单，并创建物流跟进 case：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"order_id":"ord_101"}' http://localhost:8002/delivery/status/lookup
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"status":"delayed","min_delay_days":1}' http://localhost:8002/delivery/exceptions/search
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"order_id":"ord_101","case_type":"delivery_delay","reason":"客户要求跟进延迟配送","created_by":"delivery:user-001"}' http://localhost:8002/delivery/cases
```

Delivery bot 的端到端测试话术是：`@delivery 查询 ord_101 物流`、`@delivery 当前有哪些延迟物流` 和 `@delivery 为 ord_101 创建物流延迟跟进 case`。

本地模拟端点是：

```text
POST http://localhost:8010/feishu/events
```

验证飞书 URL challenge：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"type":"url_verification","challenge":"challenge-code"}' http://localhost:8010/feishu/events
```

HTTP callback 模式仍保留，用于本地模拟或备用 callback 部署。callback 模式下 `/feishu/events` 会使用第一个 bot 配置；如果 `FEISHU_BOTS_JSON` 为空，则继续把消息转发到 `N8N_CHAT_WEBHOOK_URL`，并用旧单机器人凭证回复飞书。

如果长连接已经 `connected`，但发送机器人消息后没有 `received feishu long connection event`，需要检查飞书应用是否订阅 `im.message.receive_v1`、应用版本是否已发布、机器人是否安装到目标会话。

adapter 会先返回 `accepted=true`，再在后台把消息转发到 n8n，agent 完成后才尝试回复飞书。如果日志里有 `received feishu event` 但没有 `forwarded ... to n8n`，检查 `N8N_CHAT_WEBHOOK_URL` 和 n8n workflow 是否可用。如果日志里有 `forwarded ... has_reply=True`，随后出现 `failed to reply to feishu`，说明 workflow 已经运行，但飞书回复 API 拒绝了回包。需要检查应用权限，以及 message ID 是否来自真实飞书回调。

本地模拟飞书事件：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"schema":"2.0","header":{"event_id":"evt_local_probe","event_type":"im.message.receive_v1","token":"verify-token"},"event":{"sender":{"sender_id":{"open_id":"ou_local_probe"}},"message":{"message_id":"om_local_probe","chat_id":"oc_local_probe","message_type":"text","content":"{\"text\":\"帮我查一下订单 ord_100\"}"}}}' http://localhost:8010/feishu/events
```

直接通过 n8n 测试部门路由：

```powershell
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"platform":"feishu","message_type":"text","sender_id":"local","chat_id":"local","message_id":"local_ord_100","text":"帮我查一下订单 ord_100"}' http://localhost:5678/webhook/customer-support-inbound
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"platform":"feishu","message_type":"text","sender_id":"local","chat_id":"local","message_id":"local_item_vinda_tissue","text":"item_vinda_tissue 今天能发货吗"}' http://localhost:5678/webhook/warehouse-inbound
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"platform":"feishu","message_type":"text","sender_id":"local","chat_id":"local","message_id":"local_delivery_ord_101","text":"查询 ord_101 物流状态"}' http://localhost:5678/webhook/delivery-inbound
```

预期本地结果：客服 workflow 会调用 `order_status_tool`，仓储 workflow 会调用仓储工具，物流 workflow 会调用物流工具。这类直接 n8n 检查可能调用已配置的 LLM；如果要避免模型额度，优先检查 mock-api endpoint。

## Replay 失败事件

```powershell
./scripts/replay_failed_event.ps1 -EventId evt_mock_api_failure
```

## 说明

n8n 容器可能会记录 Python task runner warning。本项目不在 n8n 中使用 Python Code node，所以该 warning 不阻塞 demo workflow。
