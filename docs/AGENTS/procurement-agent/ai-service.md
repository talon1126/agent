# ai-service

当前采购业务没有在 `ai-service` 暴露专用 HTTP 接口。采购的确定性业务动作主要通过 n8n agent 调用 `mock-api` 和 `feishu-adapter` 完成。

如果后续要把采购决策下沉到 `ai-service`，需要先明确是否新增可测试的采购决策层，例如供应商选择策略、比价规则、采购审批策略。不要让 `ai-service` 直接读写采购 Postgres 表，采购事实仍应通过 `mock-api` 或未来采购服务暴露。
