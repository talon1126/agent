import json
from pathlib import Path


WORKFLOW_PATH = Path("n8n/workflows/chat-parent-son-agent.json")


def load_workflow() -> dict:
    data = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return data[0] if isinstance(data, list) else data


def node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow["nodes"]:
        if node.get("name") == name:
            return node
    raise AssertionError(f"missing node {name}")


def test_workflow_contains_english_business_agents_and_tools() -> None:
    workflow = load_workflow()
    node_names = {node["name"] for node in workflow["nodes"]}

    expected_nodes = {
        "Parent Agent",
        "Customer Support Agent",
        "Warehouse Agent",
        "Procurement Agent",
        "Operations Agent",
        "order_status_tool",
        "policy_search_tool",
        "inventory_status_tool",
        "procurement_mock_tool",
        "operations_mock_tool",
    }

    assert expected_nodes.issubset(node_names)
    assert "AI Agent" not in node_names
    assert "after_sales_agent" not in node_names
    assert "weather_agent" not in node_names
    assert "weather_forecast_tool" not in node_names


def test_parent_prompt_routes_to_multi_domain_agents() -> None:
    workflow = load_workflow()
    parent = node_by_name(workflow, "Parent Agent")
    system_message = parent["parameters"]["options"]["systemMessage"]

    assert "Customer Support Agent" in system_message
    assert "Warehouse Agent" in system_message
    assert "Procurement Agent" in system_message
    assert "Operations Agent" in system_message
    assert "订单" in system_message
    assert "库存" in system_message
    assert "采购" in system_message
    assert "运营" in system_message
    assert "不要自己编造" in system_message
    assert "weather_agent" not in system_message


def test_customer_support_agent_keeps_order_and_policy_tools() -> None:
    workflow = load_workflow()
    agent = node_by_name(workflow, "Customer Support Agent")
    order_tool = node_by_name(workflow, "order_status_tool")
    policy_tool = node_by_name(workflow, "policy_search_tool")
    system_message = agent["parameters"]["options"]["systemMessage"]

    assert agent["type"] == "@n8n/n8n-nodes-langchain.agentTool"
    assert "order_status_tool" in system_message
    assert "policy_search_tool" in system_message
    assert "clause_id" in system_message
    assert "source_file" in system_message
    assert "http://mock-api:8000/orders/" in order_tool["parameters"]["jsCode"]
    assert "http://mock-api:8000/shipments/" in order_tool["parameters"]["jsCode"]
    assert "http://mock-api:8000/policies/search" in policy_tool["parameters"]["jsCode"]

    assert workflow["connections"]["order_status_tool"]["ai_tool"] == [
        [{"node": "Customer Support Agent", "type": "ai_tool", "index": 0}]
    ]
    assert workflow["connections"]["policy_search_tool"]["ai_tool"] == [
        [{"node": "Customer Support Agent", "type": "ai_tool", "index": 0}]
    ]
    assert workflow["connections"]["Customer Support Agent"]["ai_tool"] == [
        [{"node": "Parent Agent", "type": "ai_tool", "index": 0}]
    ]


def test_new_business_agents_have_tools_and_parent_connections() -> None:
    workflow = load_workflow()
    warehouse = node_by_name(workflow, "Warehouse Agent")
    procurement = node_by_name(workflow, "Procurement Agent")
    operations = node_by_name(workflow, "Operations Agent")
    inventory_tool = node_by_name(workflow, "inventory_status_tool")
    procurement_tool = node_by_name(workflow, "procurement_mock_tool")
    operations_tool = node_by_name(workflow, "operations_mock_tool")

    assert "inventory_status_tool" in warehouse["parameters"]["options"]["systemMessage"]
    assert "procurement_mock_tool" in procurement["parameters"]["options"]["systemMessage"]
    assert "operations_mock_tool" in operations["parameters"]["options"]["systemMessage"]
    assert "http://mock-api:8000/inventory/" in inventory_tool["parameters"]["jsCode"]
    assert "http://mock-api:8000/procurement/mock" in procurement_tool["parameters"]["jsCode"]
    assert "http://mock-api:8000/operations/summary/mock" in operations_tool["parameters"]["jsCode"]

    assert workflow["connections"]["inventory_status_tool"]["ai_tool"] == [
        [{"node": "Warehouse Agent", "type": "ai_tool", "index": 0}]
    ]
    assert workflow["connections"]["procurement_mock_tool"]["ai_tool"] == [
        [{"node": "Procurement Agent", "type": "ai_tool", "index": 0}]
    ]
    assert workflow["connections"]["operations_mock_tool"]["ai_tool"] == [
        [{"node": "Operations Agent", "type": "ai_tool", "index": 0}]
    ]
    for agent_name in ("Warehouse Agent", "Procurement Agent", "Operations Agent"):
        assert workflow["connections"][agent_name]["ai_tool"] == [
            [{"node": "Parent Agent", "type": "ai_tool", "index": 0}]
        ]


def test_chat_messages_go_through_fast_path_then_parent_agent() -> None:
    workflow = load_workflow()
    node_names = {node["name"] for node in workflow["nodes"]}

    assert "Detect Fast Customer Support Path" in node_names
    assert "Is Fast Customer Support Path?" in node_names
    assert "Run Customer Support Fast Path" in node_names
    normalize_connections = workflow["connections"]["Normalize Inbound Message"]["main"]
    assert normalize_connections == [[{"node": "Has Text Or Recognition?", "type": "main", "index": 0}]]

    has_text_connections = workflow["connections"]["Has Text Or Recognition?"]["main"]
    assert has_text_connections[0] == [
        {"node": "Detect Fast Customer Support Path", "type": "main", "index": 0}
    ]
    fast_path_connections = workflow["connections"]["Is Fast Customer Support Path?"]["main"]
    assert fast_path_connections[0] == [
        {"node": "Run Customer Support Fast Path", "type": "main", "index": 0}
    ]
    assert fast_path_connections[1] == [{"node": "Parent Agent", "type": "main", "index": 0}]

    fast_path_http = node_by_name(workflow, "Run Customer Support Fast Path")
    assert fast_path_http["parameters"]["url"] == "http://ai-service:8000/after-sales/fast-path"

    handled_connections = workflow["connections"]["Is Fast Path Handled?"]["main"]
    assert handled_connections[0] == [
        {"node": "Format Webhook Reply", "type": "main", "index": 0}
    ]
    assert handled_connections[1] == [{"node": "Parent Agent", "type": "main", "index": 0}]


def test_agents_return_intermediate_steps_for_runtime_verification() -> None:
    workflow = load_workflow()

    for agent_name in (
        "Parent Agent",
        "Customer Support Agent",
        "Warehouse Agent",
        "Procurement Agent",
        "Operations Agent",
    ):
        agent = node_by_name(workflow, agent_name)
        assert agent["parameters"]["options"]["returnIntermediateSteps"] is True
        assert agent["parameters"]["options"]["maxIterations"] == 3


def test_chat_agents_use_scoped_postgres_memory() -> None:
    workflow = load_workflow()

    parent_memory = node_by_name(workflow, "Parent Postgres Chat Memory")
    customer_support_memory = node_by_name(workflow, "Customer Support Postgres Chat Memory")

    for memory_node in (parent_memory, customer_support_memory):
        assert memory_node["type"] == "@n8n/n8n-nodes-langchain.memoryPostgresChat"
        assert memory_node["typeVersion"] == 1.4
        assert memory_node["parameters"]["sessionIdType"] == "customKey"
        assert "feishu:" in memory_node["parameters"]["sessionKey"]
        assert "chat_id" in memory_node["parameters"]["sessionKey"]
        assert "sender_id" in memory_node["parameters"]["sessionKey"]
        assert memory_node["parameters"]["tableName"] == "n8n_chat_histories"
        assert memory_node["parameters"]["contextWindowLength"] == 6

    assert parent_memory["parameters"]["sessionKey"].startswith("={{ 'parent:'")
    assert customer_support_memory["parameters"]["sessionKey"].startswith("={{ 'customer_support:'")
    assert parent_memory["parameters"]["sessionKey"] != customer_support_memory["parameters"]["sessionKey"]

    assert workflow["connections"]["Parent Postgres Chat Memory"]["ai_memory"] == [
        [{"node": "Parent Agent", "type": "ai_memory", "index": 0}]
    ]
    assert workflow["connections"]["Customer Support Postgres Chat Memory"]["ai_memory"] == [
        [{"node": "Customer Support Agent", "type": "ai_memory", "index": 0}]
    ]
