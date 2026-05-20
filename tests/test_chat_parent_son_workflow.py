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


def test_workflow_contains_after_sales_agent_and_tool() -> None:
    workflow = load_workflow()

    after_sales_agent = node_by_name(workflow, "after_sales_agent")
    order_status_tool = node_by_name(workflow, "order_status_tool")

    assert after_sales_agent["type"] == "@n8n/n8n-nodes-langchain.agentTool"
    assert order_status_tool["type"] == "@n8n/n8n-nodes-langchain.toolCode"


def test_parent_prompt_routes_after_sales_tasks() -> None:
    workflow = load_workflow()
    parent = node_by_name(workflow, "AI Agent")
    system_message = parent["parameters"]["options"]["systemMessage"]

    assert "after_sales_agent" in system_message
    assert "订单" in system_message
    assert "物流" in system_message
    assert "退款" in system_message
    assert "不要自己编造" in system_message


def test_after_sales_agent_uses_order_status_tool() -> None:
    workflow = load_workflow()
    agent = node_by_name(workflow, "after_sales_agent")
    tool = node_by_name(workflow, "order_status_tool")
    system_message = agent["parameters"]["options"]["systemMessage"]
    tool_code = tool["parameters"]["jsCode"]

    assert "order_status_tool" in system_message
    assert "直接转述" in system_message
    assert "http://mock-api:8000/orders/" in tool_code
    assert "http://mock-api:8000/shipments/" in tool_code
    assert "helpers.httpRequest" in tool_code
    assert "fetch(" not in tool_code

    agent_tool_connections = workflow["connections"]["order_status_tool"]["ai_tool"]
    assert agent_tool_connections == [[{"node": "after_sales_agent", "type": "ai_tool", "index": 0}]]

    parent_tool_connections = workflow["connections"]["after_sales_agent"]["ai_tool"]
    assert parent_tool_connections == [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]


def test_chat_messages_must_go_through_parent_before_son_agent() -> None:
    workflow = load_workflow()
    node_names = {node["name"] for node in workflow["nodes"]}

    assert "Detect Fast After-sales Path" in node_names
    assert "Is Fast After-sales Path?" in node_names
    assert "Run After-sales Fast Path" in node_names
    normalize_connections = workflow["connections"]["Normalize Inbound Message"]["main"]
    assert normalize_connections == [[{"node": "Has Text Or Recognition?", "type": "main", "index": 0}]]

    has_text_connections = workflow["connections"]["Has Text Or Recognition?"]["main"]
    assert has_text_connections[0] == [
        {"node": "Detect Fast After-sales Path", "type": "main", "index": 0}
    ]
    fast_path_connections = workflow["connections"]["Is Fast After-sales Path?"]["main"]
    assert fast_path_connections[0] == [
        {"node": "Run After-sales Fast Path", "type": "main", "index": 0}
    ]
    assert fast_path_connections[1] == [{"node": "AI Agent", "type": "main", "index": 0}]

    fast_path_http = node_by_name(workflow, "Run After-sales Fast Path")
    assert fast_path_http["parameters"]["url"] == "http://ai-service:8000/after-sales/fast-path"

    fast_path_detector = node_by_name(workflow, "Detect Fast After-sales Path")
    detector_code = fast_path_detector["parameters"]["jsCode"]
    assert "hasRefundIntent" in detector_code
    assert "fastPathCandidate = Boolean((orderId && hasAfterSalesIntent) || hasRefundIntent" in detector_code

    handled_connections = workflow["connections"]["Is Fast Path Handled?"]["main"]
    assert handled_connections[0] == [
        {"node": "Format Webhook Reply", "type": "main", "index": 0}
    ]
    assert handled_connections[1] == [{"node": "AI Agent", "type": "main", "index": 0}]


def test_parent_and_son_agents_return_intermediate_steps_for_runtime_verification() -> None:
    workflow = load_workflow()
    parent = node_by_name(workflow, "AI Agent")
    after_sales_agent = node_by_name(workflow, "after_sales_agent")

    assert parent["parameters"]["options"]["returnIntermediateSteps"] is True
    assert after_sales_agent["parameters"]["options"]["returnIntermediateSteps"] is True
    assert parent["parameters"]["options"]["maxIterations"] == 3
    assert after_sales_agent["parameters"]["options"]["maxIterations"] == 3


def test_chat_agents_use_postgres_memory_scoped_by_feishu_user() -> None:
    workflow = load_workflow()

    parent_memory = node_by_name(workflow, "Parent Postgres Chat Memory")
    after_sales_memory = node_by_name(workflow, "After-sales Postgres Chat Memory")

    for memory_node in (parent_memory, after_sales_memory):
        assert memory_node["type"] == "@n8n/n8n-nodes-langchain.memoryPostgresChat"
        assert memory_node["typeVersion"] == 1.4
        assert memory_node["parameters"]["sessionIdType"] == "customKey"
        assert "feishu:" in memory_node["parameters"]["sessionKey"]
        assert "chat_id" in memory_node["parameters"]["sessionKey"]
        assert "sender_id" in memory_node["parameters"]["sessionKey"]
        assert memory_node["parameters"]["tableName"] == "n8n_chat_histories"
        assert memory_node["parameters"]["contextWindowLength"] == 6

    assert parent_memory["parameters"]["sessionKey"].startswith("={{ 'parent:'")
    assert after_sales_memory["parameters"]["sessionKey"].startswith("={{ 'after_sales:'")
    assert parent_memory["parameters"]["sessionKey"] != after_sales_memory["parameters"]["sessionKey"]

    assert workflow["connections"]["Parent Postgres Chat Memory"]["ai_memory"] == [
        [{"node": "AI Agent", "type": "ai_memory", "index": 0}]
    ]
    assert workflow["connections"]["After-sales Postgres Chat Memory"]["ai_memory"] == [
        [{"node": "after_sales_agent", "type": "ai_memory", "index": 0}]
    ]


def test_after_sales_agent_uses_policy_search_tool_for_refunds() -> None:
    workflow = load_workflow()
    agent = node_by_name(workflow, "after_sales_agent")
    policy_tool = node_by_name(workflow, "policy_search_tool")
    system_message = agent["parameters"]["options"]["systemMessage"]
    tool_code = policy_tool["parameters"]["jsCode"]

    assert policy_tool["type"] == "@n8n/n8n-nodes-langchain.toolCode"
    assert "policy_search_tool" in system_message
    assert "退款" in system_message
    assert "clause_id" in system_message
    assert "source_file" in system_message
    assert "http://mock-api:8000/policies/search" in tool_code
    assert "helpers.httpRequest" in tool_code

    assert workflow["connections"]["policy_search_tool"]["ai_tool"] == [
        [{"node": "after_sales_agent", "type": "ai_tool", "index": 0}]
    ]
