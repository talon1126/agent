# 部署和运维

这个项目默认先用本地 Docker Compose 演示，再逐步升级到接近生产的部署形态，而且不改变核心边界。

## 本地 Demo

默认运行方式是 Docker Compose：

```powershell
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://localhost:8010/health/details | ConvertTo-Json -Depth 10
```

测试真实飞书消息前，需要先在 n8n 中导入并激活四个部门 workflow。

## 接近生产的形态

保持同样的服务拆分：

- `feishu-adapter`：协议网关、bot 路由、去重、飞书回复、run log。
- `n8n`：workflow 编排和部门流程归属。
- `ai-service`：模型 prompt、deterministic fast path、memory 策略、语音转写、RAG 和模型 fallback。
- `mock-api` 替换层：真实订单、仓储、采购、运营、工单、审批和通知系统。
- `postgres`：持久化 session state、user profile 摘要、run log、dead letter 和 replay 记录。

## 必要环境变量

不要提交 `.env`。运行时配置这些变量：

- `FEISHU_BOTS_JSON`：每个部门机器人一项；如果多个 bot 在同一个群里，需要配置 `bot_open_id`。
- `FEISHU_EVENT_MODE=long_connection`
- `FEISHU_RUN_LOG_URL=http://mock-api:8000/run-logs`，或者生产环境的 run-log endpoint。
- `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`，用于模型网关。
- `DATABASE_URL`，用于持久化 memory 和运维状态。

## 运维检查

每次 demo 或部署前先验证：

```powershell
pytest services\ai-service\tests -v
pytest services\mock-api\tests -v
pytest services\feishu-adapter\tests -v
pytest tests\test_department_workflows.py -v
docker compose config --quiet
```

再检查运行中的服务：

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8010/health/details | ConvertTo-Json -Depth 10
Invoke-RestMethod http://localhost:8002/run-logs | ConvertTo-Json -Depth 10
```

## 监控信号

重点观察这些日志和 run-log 字段：

- `message_id`、`bot_name`、`workflow`
- `status` 和 `error`
- `latency_ms`、`n8n_ms`、`token_ms`、`reply_ms`
- workflow 返回的 `tool_calls`
- 重复消息和群聊过滤日志

这些信号能回答生产环境里最关键的问题：哪个 bot 处理了消息、哪个 workflow 运行了、耗时多久、用了什么工具、失败在哪一层。

## 成本和延迟控制

尽量先走确定性路径，再调用 LLM：

- 常见订单和退款追问走 fast path。
- 政策问题先 RAG，再生成回答。
- 使用短窗口 memory 和紧凑 session state。
- n8n、工具或模型超时时返回降级回复。
- 用 run log 对比 fast path 和 LLM path 的延迟。

## CI/CD

GitHub Actions 会在 push 到 `master` 和 pull request 时运行服务测试、workflow 结构测试和 Docker Compose 校验。新增 Agent 行为前，先保持 CI 通过。
