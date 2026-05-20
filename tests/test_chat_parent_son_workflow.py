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
    assert "查询成功" in tool_code

    agent_tool_connections = workflow["connections"]["order_status_tool"]["ai_tool"]
    assert agent_tool_connections == [[{"node": "after_sales_agent", "type": "ai_tool", "index": 0}]]

    parent_tool_connections = workflow["connections"]["after_sales_agent"]["ai_tool"]
    assert parent_tool_connections == [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]


def test_workflow_has_deterministic_after_sales_order_path() -> None:
    workflow = load_workflow()
    detect = node_by_name(workflow, "Detect After-sales Order")
    run_tool = node_by_name(workflow, "Run After-sales API Tool")

    assert "is_after_sales_order" in detect["parameters"]["jsCode"]
    assert "http://mock-api:8000/orders/" in run_tool["parameters"]["jsCode"]
    assert "http://mock-api:8000/shipments/" in run_tool["parameters"]["jsCode"]
    assert "this.helpers.httpRequest" in run_tool["parameters"]["jsCode"]
    assert "fetch(" not in run_tool["parameters"]["jsCode"]

    normalize_connections = workflow["connections"]["Normalize Inbound Message"]["main"]
    assert normalize_connections == [[{"node": "Detect After-sales Order", "type": "main", "index": 0}]]
