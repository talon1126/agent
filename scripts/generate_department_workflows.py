import copy
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "n8n" / "workflows" / "chat-parent-son-agent.json"
TARGET_DIR = ROOT / "n8n" / "workflows"

DEPARTMENTS = {
    "customer_support": {
        "workflow_id": "customer-support-workflow",
        "workflow_name": "Customer Support Workflow",
        "filename": "customer-support-workflow.json",
        "webhook_path": "customer-support-inbound",
        "agent": "Customer Support Agent",
        "model": "Customer Support Qwen Chat Model",
        "memory": "Customer Support Postgres Chat Memory",
        "memory_prefix": "customer_support",
        "tools": ["order_status_tool", "policy_search_tool"],
    },
    "warehouse": {
        "workflow_id": "warehouse-workflow",
        "workflow_name": "Warehouse Workflow",
        "filename": "warehouse-workflow.json",
        "webhook_path": "warehouse-inbound",
        "agent": "Warehouse Agent",
        "model": "Warehouse Qwen Chat Model",
        "memory": "Warehouse Postgres Chat Memory",
        "memory_prefix": "warehouse",
        "tools": [
            "warehouse_inventory_tool",
            "warehouse_exception_tool",
            "warehouse_fulfillment_tool",
        ],
    },
    "procurement": {
        "workflow_id": "procurement-workflow",
        "workflow_name": "Procurement Workflow",
        "filename": "procurement-workflow.json",
        "webhook_path": "procurement-inbound",
        "agent": "Procurement Agent",
        "model": "Procurement Qwen Chat Model",
        "memory": "Procurement Postgres Chat Memory",
        "memory_prefix": "procurement",
        "tools": ["procurement_mock_tool"],
    },
    "operations": {
        "workflow_id": "operations-workflow",
        "workflow_name": "Operations Workflow",
        "filename": "operations-workflow.json",
        "webhook_path": "operations-inbound",
        "agent": "Operations Agent",
        "model": "Operations Qwen Chat Model",
        "memory": "Operations Postgres Chat Memory",
        "memory_prefix": "operations",
        "tools": ["operations_mock_tool"],
    },
}

FORMAT_REPLY_JS = """const source = $items('Normalize Inbound Message')[0]?.json ?? {};

function normalizeToolTrace(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map((step) => ({
    tool: step.tool ?? step.name ?? step.action?.tool ?? 'unknown_tool',
    input: step.input ?? step.toolInput ?? step.action?.toolInput ?? null,
    output: step.output ?? step.result ?? step.observation ?? null,
  }));
}

return $input.all().map((item) => {
  const reply = item.json.answer ?? item.json.output ?? item.json.text ?? item.json.response ?? JSON.stringify(item.json);
  const rawToolTrace = item.json.tool_trace ?? item.json.tool_calls ?? item.json.intermediateSteps ?? item.json.intermediate_steps ?? [];
  return {
    json: {
      ok: true,
      platform: source.platform,
      chat_id: source.chat_id,
      sender_id: source.sender_id,
      message_id: source.message_id,
      reply,
      tool_trace: normalizeToolTrace(rawToolTrace),
      raw_agent_output: item.json
    }
  };
});"""


def load_source_workflow() -> dict[str, Any]:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    return data[0] if isinstance(data, list) else data


def find_node(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    for node in workflow["nodes"]:
        if node.get("name") == name:
            return node
    raise KeyError(name)


def clone_node(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    return copy.deepcopy(find_node(workflow, name))


def make_agent_node(source_agent: dict[str, Any]) -> dict[str, Any]:
    agent = copy.deepcopy(source_agent)
    agent["type"] = "@n8n/n8n-nodes-langchain.agent"
    agent["parameters"].pop("toolDescription", None)
    agent["parameters"]["promptType"] = "define"
    agent["position"] = [320, 120]
    return agent


def make_memory_node(template: dict[str, Any], name: str, prefix: str) -> dict[str, Any]:
    memory = copy.deepcopy(template)
    memory["name"] = name
    memory["id"] = f"{prefix}-postgres-chat-memory"
    memory["position"] = [520, 320]
    memory["parameters"]["sessionKey"] = (
        f"={{{{ '{prefix}:' + ($json.session_id || 'feishu:' + $json.chat_id + ':' + $json.sender_id) }}}}"
    )
    return memory


def base_workflow(source: dict[str, Any], department: dict[str, Any]) -> dict[str, Any]:
    workflow = copy.deepcopy(source)
    workflow["id"] = department["workflow_id"]
    workflow["name"] = department["workflow_name"]
    workflow["description"] = (
        f"Independent department workflow for {department['workflow_name']}."
    )
    workflow["nodes"] = []
    workflow["connections"] = {}
    workflow["pinData"] = {}
    workflow["tags"] = []
    workflow["shared"] = []
    workflow["versionCounter"] = 1
    workflow["triggerCount"] = 0
    workflow.pop("activeVersionId", None)
    workflow.pop("versionId", None)
    return workflow


def add_connection(workflow: dict[str, Any], source: str, channel: str, target: str) -> None:
    workflow["connections"].setdefault(source, {}).setdefault(channel, [])
    workflow["connections"][source][channel].append(
        [{"node": target, "type": channel, "index": 0}]
    )


def build_department_workflow(source: dict[str, Any], department: dict[str, Any]) -> dict[str, Any]:
    workflow = base_workflow(source, department)

    webhook = clone_node(source, "When Chat Message Received")
    webhook["parameters"]["path"] = department["webhook_path"]
    webhook["position"] = [-720, 80]

    normalize = clone_node(source, "Normalize Inbound Message")
    normalize["position"] = [-496, 80]

    agent = make_agent_node(clone_node(source, department["agent"]))
    model = clone_node(source, department["model"])
    model["position"] = [256, 320]

    memory_template = clone_node(source, "Customer Support Postgres Chat Memory")
    memory = (
        clone_node(source, department["memory"])
        if department["memory"] == "Customer Support Postgres Chat Memory"
        else make_memory_node(memory_template, department["memory"], department["memory_prefix"])
    )
    memory["position"] = [512, 320]

    format_reply = clone_node(source, "Format Webhook Reply")
    format_reply["position"] = [720, 80]
    format_reply["parameters"]["jsCode"] = FORMAT_REPLY_JS
    respond = clone_node(source, "Respond to Webhook")
    respond["position"] = [944, 80]

    tools = []
    for index, tool_name in enumerate(department["tools"]):
        tool = clone_node(source, tool_name)
        tool["position"] = [720 + index * 176, 320]
        tools.append(tool)

    workflow["nodes"] = [webhook, normalize, agent, model, memory, *tools, format_reply, respond]

    add_connection(workflow, "When Chat Message Received", "main", "Normalize Inbound Message")
    add_connection(workflow, "Normalize Inbound Message", "main", department["agent"])
    add_connection(workflow, department["agent"], "main", "Format Webhook Reply")
    add_connection(workflow, "Format Webhook Reply", "main", "Respond to Webhook")
    add_connection(workflow, department["model"], "ai_languageModel", department["agent"])
    add_connection(workflow, department["memory"], "ai_memory", department["agent"])
    for tool_name in department["tools"]:
        add_connection(workflow, tool_name, "ai_tool", department["agent"])

    return workflow


def main() -> None:
    source = load_source_workflow()
    for department in DEPARTMENTS.values():
        workflow = build_department_workflow(source, department)
        target = TARGET_DIR / department["filename"]
        target.write_text(
            json.dumps([workflow], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
