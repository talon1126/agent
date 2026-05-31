# ai-service

当前仓储业务没有在 `ai-service` 暴露专用 HTTP 接口。仓储的业务判断主要走 n8n agent + `mock-api` 工具接口，飞书表格能力走 `feishu-adapter`。

`ai-service` 现有能力主要是客服/售后通用能力，例如：

- `POST /message/handle`：消息处理入口。
- `POST /decide`：售后事件确定性决策。
- `POST /after-sales/fast-path`：售后快路径。

如果其他 agent 需要把仓储逻辑下沉到 `ai-service`，需要先明确是否要新增“模型可测试的仓储决策层”，不要绕过 `mock-api` 直接读仓储数据库。
