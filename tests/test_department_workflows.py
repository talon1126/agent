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
            "procurement_mock_tool",
            "operations_mock_tool",
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
        },
        "forbidden_tools": {
            "order_status_tool",
            "policy_search_tool",
            "procurement_mock_tool",
            "operations_mock_tool",
            "echo_task_tool",
        },
    },
    "procurement-workflow.json": {
        "workflow_name": "Procurement Workflow",
        "webhook_path": "procurement-inbound",
        "agent": "Procurement Agent",
        "model": "Procurement Qwen Chat Model",
        "memory": "Procurement Postgres Chat Memory",
        "tools": {"procurement_mock_tool"},
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
            "operations_mock_tool",
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
            "procurement_mock_tool",
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
            assert "创建、初始化或配置飞书库存表" in system_message
            assert "同步、导出、发布、表格、飞书表格或看板" in system_message
            assert "必须先调用 warehouse_table_schema_tool" in system_message
            assert "不要编造 schema 中不存在的字段" in system_message
            assert "不要重复调用 warehouse_view_create_tool" in system_message
            assert agent["parameters"]["options"]["maxIterations"] >= 6
            template_detector = node_by_name(
                workflow, "Detect Warehouse View Template Request"
            )
            template_create = node_by_name(workflow, "Create Warehouse View From Template")
            template_reply = node_by_name(
                workflow, "Format Warehouse View Template Reply"
            )

            detector_code = template_detector["parameters"]["jsCode"]
            assert "warehouse_view_template_candidate" in detector_code
            assert "视图" in detector_code
            assert "看板" in detector_code
            assert "建" in detector_code
            assert "建一个" in detector_code
            assert "帮我建" in detector_code
            assert "'表格'" not in detector_code
            assert "'table'" not in detector_code
            assert "extractVisibleFields" not in detector_code
            assert "extractRiskFilter" not in detector_code
            assert "extractSorts" not in detector_code
            assert "visible_fields" not in detector_code
            assert "filters" not in detector_code
            assert "sorts" not in detector_code
            assert template_create["parameters"]["url"].endswith(
                "/warehouse/inventory-table/views/from-template"
            )
            assert template_create["parameters"]["jsonBody"] == (
                "={{ $json.warehouse_view_template_body }}"
            )
            assert (
                "warehouse_view_template_fast_path"
                in template_reply["parameters"]["jsCode"]
            )
            assert "tool_trace" in template_reply["parameters"]["jsCode"]


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
                        "node": "Detect Warehouse View Template Request",
                        "type": "main",
                        "index": 0,
                    }
                ]
            ]
            assert connections["Detect Warehouse View Template Request"]["main"] == [
                [
                    {
                        "node": "Is Warehouse View Template Request",
                        "type": "main",
                        "index": 0,
                    }
                ]
            ]
            assert connections["Is Warehouse View Template Request"]["main"] == [
                [
                    {
                        "node": "Create Warehouse View From Template",
                        "type": "main",
                        "index": 0,
                    }
                ],
                [{"node": expected["agent"], "type": "main", "index": 0}],
            ]
            assert connections["Create Warehouse View From Template"]["main"] == [
                [
                    {
                        "node": "Format Warehouse View Template Reply",
                        "type": "main",
                        "index": 0,
                    }
                ]
            ]
            assert connections["Format Warehouse View Template Reply"]["main"] == [
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
