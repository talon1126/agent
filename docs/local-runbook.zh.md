# 本地运行手册

## 启动

```powershell
docker compose up --build -d
```

## 健康检查

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
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

## 发送 Demo 事件

```powershell
./scripts/send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

如果本机 PowerShell 禁用了脚本执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_event.ps1 -EventFile fixtures/events/refund_high_value.json
```

## Replay 失败事件

```powershell
./scripts/replay_failed_event.ps1 -EventId evt_mock_api_failure
```

## 说明

n8n 容器可能会记录 Python task runner warning。本项目不在 n8n 中使用 Python Code node，所以该 warning 不阻塞 demo workflow。
