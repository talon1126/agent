import json
from pathlib import Path


DEPARTMENT_WORKFLOWS = {
    "customer-support-workflow.json": {
        "workflow_name": "Customer Support Workflow",
        "webhook_path": "customer-support-inbound",
        "agent": "Customer Support Agent",
        "model": "Customer Support Qwen Chat Model",
        "memory": "Customer Support Postgres Chat Memory",
        "tools": {"order_status_tool", "policy_search_tool"},
        "forbidden_tools": {
            "warehouse_inventory_tool",
            "warehouse_exception_tool",
            "warehouse_fulfillment_tool",
            "warehouse_inventory_table_provision_tool",
            "warehouse_inventory_table_sync_tool",
            "warehouse_table_schema_tool",
            "warehouse_view_create_tool",
            "warehouse_replenishment_request_tool",
            "warehouse_purchase_order_arrival_sync_tool",
            "procurement_mock_tool",
            "procurement_replenishment_request_tool",
            "procurement_approve_replenishment_tool",
            "procurement_reject_replenishment_tool",
            "procurement_sync_replenishment_requests_tool",
            "procurement_sync_purchase_orders_tool",
            "procurement_approve_replenishment_batch_tool",
            "procurement_confirm_purchase_order_arrival_tool",
            "operations_mock_tool",
            "delivery_status_tool",
            "delivery_exception_tool",
            "delivery_case_tool",
            "echo_task_tool",
        },
    },
    "warehouse-workflow.json": {
        "workflow_name": "Warehouse Workflow",
        "webhook_path": "warehouse-inbound",
        "agent": "Warehouse Agent",
        "model": "Warehouse Qwen Chat Model",
        "memory": "Warehouse Postgres Chat Memory",
        "tools": {
            "warehouse_inventory_tool",
            "warehouse_exception_tool",
            "warehouse_fulfillment_tool",
            "warehouse_inventory_table_provision_tool",
            "warehouse_inventory_table_sync_tool",
            "warehouse_table_schema_tool",
            "warehouse_view_create_tool",
            "warehouse_replenishment_request_tool",
            "warehouse_purchase_order_arrival_sync_tool",
            "warehouse_inventory_sync_jobs_tool",
        },
        "forbidden_tools": {
            "order_status_tool",
            "policy_search_tool",
            "procurement_mock_tool",
            "procurement_replenishment_request_tool",
            "procurement_approve_replenishment_tool",
            "procurement_reject_replenishment_tool",
            "procurement_sync_replenishment_requests_tool",
            "procurement_sync_purchase_orders_tool",
            "procurement_approve_replenishment_batch_tool",
            "procurement_confirm_purchase_order_arrival_tool",
            "operations_mock_tool",
            "delivery_status_tool",
            "delivery_exception_tool",
            "delivery_case_tool",
            "echo_task_tool",
        },
    },
    "procurement-workflow.json": {
        "workflow_name": "Procurement Workflow",
        "webhook_path": "procurement-inbound",
        "agent": "Procurement Agent",
        "model": "Procurement Qwen Chat Model",
        "memory": "Procurement Postgres Chat Memory",
        "tools": {
            "procurement_mock_tool",
            "procurement_replenishment_request_tool",
            "procurement_approve_replenishment_tool",
            "procurement_reject_replenishment_tool",
            "procurement_sync_replenishment_requests_tool",
            "procurement_sync_purchase_orders_tool",
            "procurement_approve_replenishment_batch_tool",
            "procurement_confirm_purchase_order_arrival_tool",
        },
        "forbidden_tools": {
            "order_status_tool",
            "policy_search_tool",
            "warehouse_inventory_tool",
            "warehouse_exception_tool",
            "warehouse_fulfillment_tool",
            "warehouse_inventory_table_provision_tool",
            "warehouse_inventory_table_sync_tool",
            "warehouse_table_schema_tool",
            "warehouse_view_create_tool",
            "warehouse_replenishment_request_tool",
            "warehouse_purchase_order_arrival_sync_tool",
            "warehouse_inventory_sync_jobs_tool",
            "operations_mock_tool",
            "delivery_status_tool",
            "delivery_exception_tool",
            "delivery_case_tool",
            "echo_task_tool",
        },
    },
    "operations-workflow.json": {
        "workflow_name": "Operations Workflow",
        "webhook_path": "operations-inbound",
        "agent": "Operations Agent",
        "model": "Operations Qwen Chat Model",
        "memory": "Operations Postgres Chat Memory",
        "tools": {"operations_mock_tool"},
        "forbidden_tools": {
            "order_status_tool",
            "policy_search_tool",
            "warehouse_inventory_tool",
            "warehouse_exception_tool",
            "warehouse_fulfillment_tool",
            "warehouse_inventory_table_provision_tool",
            "warehouse_inventory_table_sync_tool",
            "warehouse_table_schema_tool",
            "warehouse_view_create_tool",
            "warehouse_replenishment_request_tool",
            "warehouse_purchase_order_arrival_sync_tool",
            "warehouse_inventory_sync_jobs_tool",
            "procurement_mock_tool",
            "procurement_replenishment_request_tool",
            "procurement_approve_replenishment_tool",
            "procurement_reject_replenishment_tool",
            "procurement_sync_replenishment_requests_tool",
            "procurement_sync_purchase_orders_tool",
            "procurement_approve_replenishment_batch_tool",
            "procurement_confirm_purchase_order_arrival_tool",
            "delivery_status_tool",
            "delivery_exception_tool",
            "delivery_case_tool",
            "echo_task_tool",
        },
    },
    "delivery-workflow.json": {
        "workflow_name": "Delivery Workflow",
        "webhook_path": "delivery-inbound",
        "agent": "Delivery Agent",
        "model": "Delivery Qwen Chat Model",
        "memory": "Delivery Postgres Chat Memory",
        "tools": {
            "delivery_status_tool",
            "delivery_exception_tool",
            "delivery_case_tool",
        },
        "forbidden_tools": {
            "order_status_tool",
            "policy_search_tool",
            "warehouse_inventory_tool",
            "warehouse_exception_tool",
            "warehouse_fulfillment_tool",
            "warehouse_inventory_table_provision_tool",
            "warehouse_inventory_table_sync_tool",
            "warehouse_table_schema_tool",
            "warehouse_view_create_tool",
            "warehouse_replenishment_request_tool",
            "warehouse_purchase_order_arrival_sync_tool",
            "warehouse_inventory_sync_jobs_tool",
            "procurement_mock_tool",
            "procurement_replenishment_request_tool",
            "procurement_approve_replenishment_tool",
            "procurement_reject_replenishment_tool",
            "procurement_sync_replenishment_requests_tool",
            "procurement_sync_purchase_orders_tool",
            "procurement_approve_replenishment_batch_tool",
            "procurement_confirm_purchase_order_arrival_tool",
            "operations_mock_tool",
            "echo_task_tool",
        },
    },
}


WORKFLOW_DIR = Path("n8n/workflows")


def load_workflow(filename: str) -> dict:
    data = json.loads((WORKFLOW_DIR / filename).read_text(encoding="utf-8"))
    return data[0] if isinstance(data, list) else data


def node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow["nodes"]:
        if node.get("name") == name:
            return node
    raise AssertionError(f"missing node {name}")


def test_department_workflow_files_exist() -> None:
    for filename in DEPARTMENT_WORKFLOWS:
        assert (WORKFLOW_DIR / filename).exists()


def test_department_workflows_have_own_webhook_agent_memory_and_tools() -> None:
    for filename, expected in DEPARTMENT_WORKFLOWS.items():
        workflow = load_workflow(filename)
        node_names = {node["name"] for node in workflow["nodes"]}

        assert workflow["name"] == expected["workflow_name"]
        assert "Parent Agent" not in node_names
        assert expected["tools"].issubset(node_names)
        assert node_names.isdisjoint(expected["forbidden_tools"])

        webhook = node_by_name(workflow, "When Chat Message Received")
        agent = node_by_name(workflow, expected["agent"])
        memory = node_by_name(workflow, expected["memory"])

        assert webhook["parameters"]["path"] == expected["webhook_path"]
        assert agent["type"] == "@n8n/n8n-nodes-langchain.agent"
        assert "returnIntermediateSteps" in agent["parameters"]["options"]
        assert memory["parameters"]["sessionKey"].startswith(
            f"={{{{ '{expected['agent'].lower().replace(' ', '_').replace('_agent', '')}:"
        )
        format_reply = node_by_name(workflow, "Format Webhook Reply")
        assert "tool_trace" in format_reply["parameters"]["jsCode"]
        assert "intermediateSteps" in format_reply["parameters"]["jsCode"]
        if expected["agent"] == "Warehouse Agent":
            system_message = agent["parameters"]["options"]["systemMessage"]
            assert "warehouse_inventory_table_provision_tool" in system_message
            assert "warehouse_inventory_table_sync_tool" in system_message
            assert "warehouse_table_schema_tool" in system_message
            assert "warehouse_view_create_tool" in system_message
            assert "warehouse_replenishment_request_tool" in system_message
            assert "warehouse_purchase_order_arrival_sync_tool" in system_message
            assert "warehouse_inventory_sync_jobs_tool" in system_message
            assert "purchase_orders" in system_message
            assert "arrived_unsynced" in system_message
            assert "处理库存同步任务" in system_message
            assert "warehouse_inventory_sync_requested" in system_message
            assert "创建、初始化或配置飞书库存表" in system_message
            assert "同步、导出、发布、表格、飞书表格或看板" in system_message
            assert "必须先调用 warehouse_table_schema_tool" in system_message
            assert "不要编造 schema 中不存在的字段" in system_message
            assert "不要重复调用 warehouse_view_create_tool" in system_message
            assert "item_id" in system_message
            assert "库位" in system_message
            assert "批次" in system_message
            assert "临期" in system_message
            assert "请要求用户提供 SKU" not in system_message
            assert "sku_bag_1" not in system_message
            assert agent["parameters"]["options"]["maxIterations"] >= 6
            intent_router = node_by_name(workflow, "Warehouse Intent Router")
            clarification_condition = node_by_name(
                workflow, "Is Warehouse Clarification Required"
            )
            view_condition = node_by_name(
                workflow, "Is Warehouse View Intent"
            )
            sync_condition = node_by_name(
                workflow, "Is Warehouse Sync Intent"
            )
            purchase_arrival_condition = node_by_name(
                workflow, "Is Warehouse Purchase Order Arrival Sync Intent"
            )
            sync_request = node_by_name(
                workflow, "Sync Warehouse Inventory From Intent"
            )
            sync_reply = node_by_name(
                workflow, "Format Warehouse Sync Reply"
            )
            purchase_arrival_request = node_by_name(
                workflow, "Sync Purchase Order Arrivals From Intent"
            )
            purchase_arrival_reply = node_by_name(
                workflow, "Format Purchase Order Arrival Sync Reply"
            )
            clarification_reply = node_by_name(
                workflow, "Format Warehouse Clarification Reply"
            )
            template_create = node_by_name(workflow, "Create Warehouse View From Template")
            template_matched = node_by_name(
                workflow, "Is Warehouse View Template Matched"
            )
            template_restore = node_by_name(
                workflow, "Restore Warehouse View Template Source"
            )
            route_restore = node_by_name(
                workflow, "Restore Warehouse Intent Router Source"
            )
            template_reply = node_by_name(
                workflow, "Format Warehouse View Template Reply"
            )

            node_names = {node["name"] for node in workflow["nodes"]}
            assert "Detect Warehouse View Template Request" not in node_names
            assert "Is Warehouse View Template Request" not in node_names
            assert intent_router["parameters"]["url"].endswith("/warehouse/intents/route")
            assert intent_router["parameters"]["jsonBody"] == (
                "={{ { message: $json.input_text || $json.text || '' } }}"
            )
            assert (
                clarification_condition["parameters"]["conditions"]["conditions"][0][
                    "leftValue"
                ]
                == "={{ $json.status }}"
            )
            assert (
                view_condition["parameters"]["conditions"]["conditions"][0][
                    "leftValue"
                ]
                == "={{ $json.executor }}"
            )
            assert (
                sync_condition["parameters"]["conditions"]["conditions"][0][
                    "rightValue"
                ]
                == "warehouse_inventory_table_sync"
            )
            assert (
                purchase_arrival_condition["parameters"]["conditions"]["conditions"][0][
                    "rightValue"
                ]
                == "warehouse_purchase_order_arrival_sync"
            )
            assert sync_request["parameters"]["url"].endswith(
                "/warehouse/inventory-table/sync/filter"
            )
            assert "slots.item_id" in sync_request["parameters"]["jsonBody"]
            assert "slots.warehouse" in sync_request["parameters"]["jsonBody"]
            assert "slots.location_code" in sync_request["parameters"]["jsonBody"]
            assert "slots.category" in sync_request["parameters"]["jsonBody"]
            assert "slots.risk_level" in sync_request["parameters"]["jsonBody"]
            assert "slots.expiry_risk" in sync_request["parameters"]["jsonBody"]
            assert "sku:" not in sync_request["parameters"]["jsonBody"]
            assert "warehouse_inventory_table_sync_fast_path" in sync_reply["parameters"]["jsCode"]
            assert "entry.item_id" in sync_reply["parameters"]["jsCode"]
            assert "entry.batch_no" in sync_reply["parameters"]["jsCode"]
            assert "entry.location_code" in sync_reply["parameters"]["jsCode"]
            assert "SKU:" not in sync_reply["parameters"]["jsCode"]
            assert purchase_arrival_request["parameters"]["url"].endswith(
                "/warehouse/purchase-orders/sync-arrivals"
            )
            assert "warehouse-agent" in purchase_arrival_request["parameters"]["jsonBody"]
            assert (
                "warehouse_purchase_order_arrival_sync_fast_path"
                in purchase_arrival_reply["parameters"]["jsCode"]
            )
            assert "synced_items" in purchase_arrival_reply["parameters"]["jsCode"]
            assert "purchase_order_id" in purchase_arrival_reply["parameters"]["jsCode"]
            assert "clarification_question" in clarification_reply["parameters"]["jsCode"]
            assert "warehouse_intent_router" in clarification_reply["parameters"]["jsCode"]
            assert template_create["parameters"]["url"].endswith(
                "/warehouse/inventory-table/views/from-template"
            )
            assert template_create["parameters"]["jsonBody"] == (
                "={{ { message: $json.payload.message, view_name: '' } }}"
            )
            assert (
                template_matched["parameters"]["conditions"]["conditions"][0][
                    "leftValue"
                ]
                == "={{ $json.matched }}"
            )
            restore_code = template_restore["parameters"]["jsCode"]
            assert "$items('Normalize Inbound Message')" in restore_code
            assert "input_text" in restore_code or "...source" in restore_code
            assert "warehouse_intent_route" in route_restore["parameters"]["jsCode"]
            fallback_positions = [
                route_restore["position"],
                template_restore["position"],
                format_reply["position"],
            ]
            assert all(
                isinstance(position, list) and len(position) == 2
                for position in fallback_positions
            )
            assert len({tuple(position) for position in fallback_positions}) == len(
                fallback_positions
            )
            assert (
                "warehouse_view_template_fast_path"
                in template_reply["parameters"]["jsCode"]
            )
            assert "tool_trace" in template_reply["parameters"]["jsCode"]

            inventory_tool = node_by_name(workflow, "warehouse_inventory_tool")
            exception_tool = node_by_name(workflow, "warehouse_exception_tool")
            fulfillment_tool = node_by_name(workflow, "warehouse_fulfillment_tool")
            table_sync_tool = node_by_name(workflow, "warehouse_inventory_table_sync_tool")
            replenishment_tool = node_by_name(workflow, "warehouse_replenishment_request_tool")
            purchase_order_sync_tool = node_by_name(workflow, "warehouse_purchase_order_arrival_sync_tool")
            sync_jobs_tool = node_by_name(workflow, "warehouse_inventory_sync_jobs_tool")
            for tool in (inventory_tool, exception_tool, fulfillment_tool, table_sync_tool):
                tool_code = tool["parameters"]["jsCode"]
                assert "extractItemId" in tool_code
                assert "item_id" in tool_code
                assert "请提供商品 item_id" in tool_code
                assert "extractSku" not in tool_code
                assert "请提供 SKU" not in tool_code
            assert "/warehouse/inventory/' + encodeURIComponent(itemId)" in inventory_tool["parameters"]["jsCode"]
            assert "body: { item_id: itemId" in exception_tool["parameters"]["jsCode"]
            assert "body: { item_id: itemId" in fulfillment_tool["parameters"]["jsCode"]
            assert "body: { item_id: itemId" in table_sync_tool["parameters"]["jsCode"]
            assert "/procurement/replenishment-requests" in replenishment_tool["parameters"]["jsCode"]
            assert "未审批" in replenishment_tool["parameters"]["jsCode"]
            assert "/warehouse/purchase-orders/sync-arrivals" in purchase_order_sync_tool["parameters"]["jsCode"]
            assert "mock-warehouse-purchase-order-arrival-sync" in purchase_order_sync_tool["parameters"]["jsCode"]
            assert "/warehouse/inventory-sync-jobs" in sync_jobs_tool["parameters"]["jsCode"]
            assert "/warehouse/inventory-table/sync/jobs" in sync_jobs_tool["parameters"]["jsCode"]
            assert "/warehouse/inventory-table/sync/filter" not in sync_jobs_tool["parameters"]["jsCode"]
            assert "jobs: jobs.map" in sync_jobs_tool["parameters"]["jsCode"]
            assert "/complete" in sync_jobs_tool["parameters"]["jsCode"]
            assert "/fail" in sync_jobs_tool["parameters"]["jsCode"]
            assert "syncJobsResult.ok !== true" in sync_jobs_tool["parameters"]["jsCode"]
            assert "warehouse_inventory_sync_failed" in sync_jobs_tool["parameters"]["jsCode"]

        if expected["agent"] == "Procurement Agent":
            system_message = agent["parameters"]["options"]["systemMessage"]
            assert "procurement_replenishment_request_tool" in system_message
            assert "procurement_approve_replenishment_tool" in system_message
            assert "procurement_reject_replenishment_tool" in system_message
            assert "procurement_sync_replenishment_requests_tool" in system_message
            assert "procurement_sync_purchase_orders_tool" in system_message
            assert "procurement_approve_replenishment_batch_tool" in system_message
            assert "procurement_confirm_purchase_order_arrival_tool" in system_message
            assert "未审批" in system_message
            assert "已审批" in system_message
            assert "arrived_unsynced" in system_message
            assert "同步补货请求" in system_message
            assert "批量批准" in system_message
            assert "已到仓库" in system_message
            assert "PO-" in system_message
            assert "通知 Warehouse" in system_message
            assert "刷新飞书视图" in system_message
            assert "批准" in system_message
            assert "驳回" in system_message
            assert "REQ-" in system_message
            assert "采购单表" in system_message
            mock_tool = node_by_name(workflow, "procurement_mock_tool")
            list_tool = node_by_name(workflow, "procurement_replenishment_request_tool")
            approve_tool = node_by_name(workflow, "procurement_approve_replenishment_tool")
            reject_tool = node_by_name(workflow, "procurement_reject_replenishment_tool")
            sync_requests_tool = node_by_name(workflow, "procurement_sync_replenishment_requests_tool")
            sync_orders_tool = node_by_name(workflow, "procurement_sync_purchase_orders_tool")
            batch_tool = node_by_name(workflow, "procurement_approve_replenishment_batch_tool")
            arrival_tool = node_by_name(workflow, "procurement_confirm_purchase_order_arrival_tool")
            assert "extractItemId" in mock_tool["parameters"]["jsCode"]
            assert "item_id: itemId" in mock_tool["parameters"]["jsCode"]
            assert "extractSku" not in mock_tool["parameters"]["jsCode"]
            assert "/procurement/replenishment-requests" in list_tool["parameters"]["jsCode"]
            assert "未审批" in list_tool["parameters"]["jsCode"]
            assert "/approve" in approve_tool["parameters"]["jsCode"]
            assert "/reject" in reject_tool["parameters"]["jsCode"]
            assert "/procurement/replenishment-requests-table/sync" in sync_requests_tool["parameters"]["jsCode"]
            assert "/procurement/purchase-orders-table/sync" in sync_orders_tool["parameters"]["jsCode"]
            assert "/approve-batch" in batch_tool["parameters"]["jsCode"]
            assert "/procurement/replenishment-requests-table/sync" in batch_tool["parameters"]["jsCode"]
            assert "/procurement/purchase-orders-table/sync" in batch_tool["parameters"]["jsCode"]
            assert "/procurement/purchase-orders/confirm-arrival-batch" in arrival_tool["parameters"]["jsCode"]
            assert "/procurement/purchase-orders-table/sync" in arrival_tool["parameters"]["jsCode"]
            assert "extractPurchaseOrderIds" in arrival_tool["parameters"]["jsCode"]
            assert "extractRequestId" in approve_tool["parameters"]["jsCode"]
            assert "extractRequestId" in reject_tool["parameters"]["jsCode"]

        if expected["agent"] == "Delivery Agent":
            system_message = agent["parameters"]["options"]["systemMessage"]
            assert "delivery_status_tool" in system_message
            assert "delivery_exception_tool" in system_message
            assert "delivery_case_tool" in system_message
            assert "不要直接执行其他部门动作" in system_message
            assert "Customer Support" in system_message
            assert "mock-delivery" in system_message
            status_tool = node_by_name(workflow, "delivery_status_tool")
            exception_tool = node_by_name(workflow, "delivery_exception_tool")
            case_tool = node_by_name(workflow, "delivery_case_tool")
            assert "/delivery/status/lookup" in status_tool["parameters"]["jsCode"]
            assert "/delivery/exceptions/search" in exception_tool["parameters"]["jsCode"]
            assert "/delivery/cases" in case_tool["parameters"]["jsCode"]
            assert "extractOrderId" in status_tool["parameters"]["jsCode"]
            assert "extractShipmentId" in case_tool["parameters"]["jsCode"]
            assert "case_type" in case_tool["parameters"]["jsCode"]
            assert "refund" not in case_tool["parameters"]["jsCode"].lower()


def test_department_workflows_connect_directly_to_department_agent() -> None:
    for filename, expected in DEPARTMENT_WORKFLOWS.items():
        workflow = load_workflow(filename)
        connections = workflow["connections"]

        assert connections["When Chat Message Received"]["main"] == [
            [{"node": "Normalize Inbound Message", "type": "main", "index": 0}]
        ]
        if expected["agent"] == "Warehouse Agent":
            assert connections["Normalize Inbound Message"]["main"] == [
                [
                    {
                        "node": "Warehouse Intent Router",
                        "type": "main",
                        "index": 0,
                    }
                ]
            ]
            assert connections["Warehouse Intent Router"]["main"] == [
                [
                    {
                        "node": "Is Warehouse Clarification Required",
                        "type": "main",
                        "index": 0,
                    }
                ]
            ]
            assert connections["Is Warehouse Clarification Required"]["main"] == [
                [
                    {
                        "node": "Format Warehouse Clarification Reply",
                        "type": "main",
                        "index": 0,
                    }
                ],
                [{"node": "Is Warehouse View Intent", "type": "main", "index": 0}],
            ]
            assert connections["Is Warehouse View Intent"]["main"] == [
                [
                    {
                        "node": "Create Warehouse View From Template",
                        "type": "main",
                        "index": 0,
                    }
                ],
                [
                    {
                        "node": "Is Warehouse Sync Intent",
                        "type": "main",
                        "index": 0,
                    }
                ],
            ]
            assert connections["Is Warehouse Sync Intent"]["main"] == [
                [
                    {
                        "node": "Sync Warehouse Inventory From Intent",
                        "type": "main",
                        "index": 0,
                    }
                ],
                [
                    {
                        "node": "Is Warehouse Purchase Order Arrival Sync Intent",
                        "type": "main",
                        "index": 0,
                    }
                ],
            ]
            assert connections["Is Warehouse Purchase Order Arrival Sync Intent"]["main"] == [
                [
                    {
                        "node": "Sync Purchase Order Arrivals From Intent",
                        "type": "main",
                        "index": 0,
                    }
                ],
                [
                    {
                        "node": "Restore Warehouse Intent Router Source",
                        "type": "main",
                        "index": 0,
                    }
                ],
            ]
            assert connections["Create Warehouse View From Template"]["main"] == [
                [
                    {
                        "node": "Is Warehouse View Template Matched",
                        "type": "main",
                        "index": 0,
                    }
                ]
            ]
            assert connections["Is Warehouse View Template Matched"]["main"] == [
                [
                    {
                        "node": "Format Warehouse View Template Reply",
                        "type": "main",
                        "index": 0,
                    }
                ],
                [
                    {
                        "node": "Restore Warehouse View Template Source",
                        "type": "main",
                        "index": 0,
                    }
                ],
            ]
            assert connections["Restore Warehouse View Template Source"]["main"] == [
                [{"node": expected["agent"], "type": "main", "index": 0}]
            ]
            assert connections["Restore Warehouse Intent Router Source"]["main"] == [
                [{"node": expected["agent"], "type": "main", "index": 0}]
            ]
            assert connections["Format Warehouse View Template Reply"]["main"] == [
                [{"node": "Respond to Webhook", "type": "main", "index": 0}]
            ]
            assert connections["Format Warehouse Clarification Reply"]["main"] == [
                [{"node": "Respond to Webhook", "type": "main", "index": 0}]
            ]
            assert connections["Sync Warehouse Inventory From Intent"]["main"] == [
                [{"node": "Format Warehouse Sync Reply", "type": "main", "index": 0}]
            ]
            assert connections["Format Warehouse Sync Reply"]["main"] == [
                [{"node": "Respond to Webhook", "type": "main", "index": 0}]
            ]
            assert connections["Sync Purchase Order Arrivals From Intent"]["main"] == [
                [
                    {
                        "node": "Format Purchase Order Arrival Sync Reply",
                        "type": "main",
                        "index": 0,
                    }
                ]
            ]
            assert connections["Format Purchase Order Arrival Sync Reply"]["main"] == [
                [{"node": "Respond to Webhook", "type": "main", "index": 0}]
            ]
        else:
            assert connections["Normalize Inbound Message"]["main"] == [
                [{"node": expected["agent"], "type": "main", "index": 0}]
            ]
        assert connections[expected["agent"]]["main"] == [
            [{"node": "Format Webhook Reply", "type": "main", "index": 0}]
        ]
        assert connections[expected["model"]]["ai_languageModel"] == [
            [{"node": expected["agent"], "type": "ai_languageModel", "index": 0}]
        ]
        assert connections[expected["memory"]]["ai_memory"] == [
            [{"node": expected["agent"], "type": "ai_memory", "index": 0}]
        ]
        for tool_name in expected["tools"]:
            assert connections[tool_name]["ai_tool"] == [
                [{"node": expected["agent"], "type": "ai_tool", "index": 0}]
            ]
