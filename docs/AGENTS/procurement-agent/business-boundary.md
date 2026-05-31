# 业务边界

采购 agent 负责仓储补货申请的采购审核、默认供应商匹配、采购单生成、采购飞书视图同步、采购单到仓确认，以及到仓后把采购单标记为等待仓储同步。

采购 agent 不负责直接修改库存事实、不负责同步仓储库存飞书视图、不负责创建或完成 Warehouse sync job、不负责客服退款/赔付、不负责物流承运商或派送决策。采购单到仓后，采购只维护 `purchase_orders.warehouse_sync_status=arrived_unsynced`，Warehouse 后续自行检查未同步采购单并同步库存批次。

当前采购系统是 `mock-procurement`，用于内部流程验证和 demo；尚未接入真实 ERP 或正式采购下单系统。
