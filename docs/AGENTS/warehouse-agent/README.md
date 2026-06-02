# 仓储agent

本文档用于和其他 workflow / agent 协作时快速说明仓储 agent 的业务边界、可复用接口、数据表和交接契约。后续维护时，仓储 agent 只维护本章节；其他 workflow 可以按同级大标题追加自己的章节。

仓储 agent 的库存模型以“批次 + 库位”为核心，当前可售库存以 `inventory_location_balances` 为准，`inventory_batches` 只保留入库事实和批次溯源。

## 文档路由

本目录按原 `AGENTS.md` 的二级标题拆分。维护本 agent 时，优先修改对应主题文档。

- [业务边界](business-boundary.md)
- [workflow 入口](workflow-entry.md)
- [ai-service](ai-service.md)
- [mock-api](mock-api.md)
- [feishu-adapter](feishu-adapter.md)
- [业务数据库表](database-tables.md)
- [其他 workflow 可能会用到的契约](cross-workflow-contracts.md)
- [常见业务对象](common-business-objects.md)
- [维护原则](maintenance-principles.md)
