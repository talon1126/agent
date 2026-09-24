"""Standalone Kayn Connector target for the TalonMart shopping Agent."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.kayn_runtime_fingerprint import evaluated_files, implementation_fingerprint
from app.routers.AImodel.evaluation import (
    AiModelEvaluationResult,
    evaluate_chat_non_streaming_async,
)
from app.routers.AImodel.kayn_trace import export_agent_trace_to_kayn
from app.routers.AImodel.memory import get_aimodel_memory_store
from app.routers.AImodel.schemas import AiModelChatRequest
from app.routers.AImodel.service import AiModelExecutionCapture
from app.routers.AImodel.tools import close_rag_knowledge_client

EvaluationRunner = Callable[
    ..., Awaitable[tuple[AiModelEvaluationResult, AiModelExecutionCapture]]
]
CallbackFactory = Callable[[], Any]
_KAYN_REQUEST_MAPPING = {
    "input": "{{ input }}",
    "request": "$",
}
_EVALUATION_ENVELOPE_VERSION = 1
_DEFAULT_CONNECTOR_MAX_MESSAGE_BYTES = 16 * 1024 * 1024


class KaynAgentTarget:
    """Translate Kayn's standard target input into the production Agent request."""

    def __init__(
        self,
        client: Any,
        *,
        mock_api_url: str,
        evaluation_runner: EvaluationRunner = evaluate_chat_non_streaming_async,
        callback_factory: CallbackFactory | None = None,
    ) -> None:
        self._client = client
        self._mock_api_url = mock_api_url
        self._evaluation_runner = evaluation_runner
        self._callback_factory = callback_factory

    async def invoke(
        self,
        input: str,
        user_id: int = 1,
        conversation_id: int | None = None,
        links: list[str] | None = None,
        page_context: dict[str, Any] | None = None,
        request: dict[str, Any] | None = None,
    ) -> AiModelEvaluationResult:
        if request is not None:
            conversation_id_int = request.get("conversation_id_int")
            if isinstance(conversation_id_int, int):
                user_id = conversation_id_int
                conversation_id = conversation_id_int
            target_context = request.get("target_context")
            if isinstance(target_context, dict) and target_context:
                page_context = target_context
        message, envelope_links, envelope_page_context = _decode_evaluation_input(input)
        request = AiModelChatRequest.model_validate(
            {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "message": message,
                "links": links if links is not None else envelope_links,
                "request_version": (
                    "v2"
                    if page_context is not None or envelope_page_context is not None
                    else "v1"
                ),
                "page_context": (
                    page_context if page_context is not None else envelope_page_context
                ),
            }
        )
        callbacks = (
            [self._callback_factory()] if self._callback_factory is not None else []
        )
        result, capture = await self._evaluation_runner(
            request,
            mock_api_url=self._mock_api_url,
            langchain_callbacks=callbacks,
        )
        if capture.trace_context is not None:
            export_agent_trace_to_kayn(self._client, capture.trace_context)
        return result


def _decode_evaluation_input(
    value: str,
) -> tuple[str, list[str], dict[str, Any] | None]:
    """Decode the versioned single-turn envelope while leaving normal chat untouched."""

    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value, [], None
    if (
        not isinstance(payload, dict)
        or payload.get("_talonmart_evaluation_version") != _EVALUATION_ENVELOPE_VERSION
    ):
        return value, [], None

    message = payload.get("message")
    links = payload.get("links", [])
    page_context = payload.get("page_context")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("evaluation envelope message must be a non-empty string")
    if not isinstance(links, list) or not all(isinstance(link, str) for link in links):
        raise ValueError("evaluation envelope links must be a string list")
    if page_context is not None and not isinstance(page_context, dict):
        raise ValueError("evaluation envelope page_context must be an object")
    return message, links, page_context


def register_kayn_agent_target(
    client: Any,
    *,
    mock_api_url: str | None = None,
    evaluation_runner: EvaluationRunner = evaluate_chat_non_streaming_async,
) -> tuple[KaynAgentTarget, Any]:
    """Register the primary endpoint and optional M1 alias in one connector."""

    from kayn_sdk import endpoint, metric
    from kayn_sdk.integrations.langchain import create_langchain_callback

    target = KaynAgentTarget(
        client,
        mock_api_url=mock_api_url or os.getenv("MOCK_API_URL", "http://mock-api:8000"),
        evaluation_runner=evaluation_runner,
        callback_factory=lambda: create_langchain_callback(client),
    )
    repo = Path(__file__).resolve().parents[3]
    runtime_fingerprint = implementation_fingerprint(repo, evaluated_files(repo))

    async def contract_metric(
        input: Any,
        output: Any,
        expected_output: Any = None,
        tool_calls: list[dict[str, Any]] | None = None,
        conversation: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await evaluate_talonmart_contract(
            input,
            output,
            expected_output,
            tool_calls,
            conversation,
            runtime_fingerprint=runtime_fingerprint,
        )

    endpoint_name = os.getenv("KAYN_AGENT_ENDPOINT_NAME", "talonmart-shopping-agent")
    registered = endpoint(
        name=endpoint_name,
        description="TalonMart shopping Agent evaluation target",
        version="2",
        client=client,
        metadata={"agent": "talonmart", "integration": "deep"},
        request_mapping=_KAYN_REQUEST_MAPPING,
    )(target.invoke)
    m1_endpoint_name = os.getenv("KAYN_M1_AGENT_ENDPOINT_NAME", "").strip()
    if m1_endpoint_name and m1_endpoint_name != endpoint_name:
        endpoint(
            name=m1_endpoint_name,
            description="TalonMart M1 shopping Agent evaluation target",
            version="2",
            client=client,
            metadata={"agent": "talonmart", "integration": "deep", "milestone": "M1"},
            request_mapping=_KAYN_REQUEST_MAPPING,
        )(target.invoke)
    metric(
        name="talonmart_contract_guard",
        version="1.0.0",
        score_type="binary",
        scope=("SINGLE_TURN", "CONVERSATION"),
        threshold=1.0,
        description="Deterministic tool-contract checks for TalonMart evaluation",
        client=client,
    )(contract_metric)
    return target, registered


async def evaluate_talonmart_contract(
    input: Any,
    output: Any,
    expected_output: Any = None,
    tool_calls: list[dict[str, Any]] | None = None,
    conversation: list[dict[str, Any]] | None = None,
    *,
    runtime_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Reject explicit tool-contract violations before an LLM judge can pass them."""

    del input, output, conversation
    contract = expected_output if isinstance(expected_output, dict) else {}
    required = _string_set(contract.get("required_tools"))
    forbidden = _string_set(contract.get("forbidden_tools"))
    successful = {
        _canonical_tool_name(str(call.get("name", "")).strip())
        for call in tool_calls or []
        if isinstance(call, dict)
        and str(call.get("name", "")).strip()
        and str(call.get("status", "succeeded")).casefold()
        not in {"error", "failed", "failure", "cancelled"}
    }
    invoked = {
        _canonical_tool_name(str(call.get("name", "")).strip())
        for call in tool_calls or []
        if isinstance(call, dict) and str(call.get("name", "")).strip()
    }
    missing = sorted(required - successful)
    forbidden_used = sorted(forbidden & invoked)
    passed = not missing and not forbidden_used
    evidence = [
        {
            "type": "tool_contract",
            "missingRequired": missing,
            "forbiddenUsed": forbidden_used,
        }
    ]
    if runtime_fingerprint is not None:
        evidence.append(
            {"type": "runtime_fingerprint", "sha256": runtime_fingerprint}
        )
    return {
        "score": float(passed),
        "passed": passed,
        "reason": (
            "Required and forbidden tool constraints are satisfied"
            if passed
            else "The response violates deterministic tool constraints"
        ),
        "evidence": evidence,
    }


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        _canonical_tool_name(item.strip())
        for item in value
        if isinstance(item, str) and item.strip()
    }


def _canonical_tool_name(value: str) -> str:
    return {
        "search_product_catalog": "product_search",
        "search_products": "product_search",
        "get_product_detail_from_link": "product_snapshot",
        "get_delivery_options": "product_snapshot",
        "get_product_reviews": "product_reviews",
    }.get(value, value)


def _content_policy() -> Any:
    from kayn_sdk import TraceContentPolicy

    return TraceContentPolicy(
        capture_inputs=False,
        capture_outputs=False,
        capture_retrieval_documents=False,
    )


async def serve() -> None:
    """Connect the local Agent to Kayn without exposing an HTTP endpoint."""

    from kayn_sdk import KaynClient, KaynConnector

    get_aimodel_memory_store().initialize()
    client = KaynClient(
        api_token=_required_env("KAYN_API_TOKEN"),
        telemetry_token=os.getenv("KAYN_TELEMETRY_TOKEN") or None,
        base_url=os.getenv("KAYN_BASE_URL", "http://localhost:8080"),
        workspace_id=os.getenv("KAYN_WORKSPACE_ID") or None,
        project_id=_required_env("KAYN_PROJECT_ID"),
        target_id=os.getenv("KAYN_TARGET_ID") or None,
        environment=os.getenv("KAYN_ENVIRONMENT", "local"),
        service_name="talonmart-shopping-agent",
        content_policy=_content_policy(),
    )
    register_kayn_agent_target(client)
    connector = KaynConnector(
        client,
        max_message_bytes=_connector_max_message_bytes(),
    )
    try:
        await connector.serve_forever()
    finally:
        await connector.disconnect()
        close_rag_knowledge_client()
        client.close()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _connector_max_message_bytes() -> int:
    raw_value = os.getenv(
        "KAYN_CONNECTOR_MAX_MESSAGE_BYTES",
        str(_DEFAULT_CONNECTOR_MAX_MESSAGE_BYTES),
    )
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(
            "KAYN_CONNECTOR_MAX_MESSAGE_BYTES must be an integer"
        ) from error
    if not 1_048_576 <= value <= 16 * 1024 * 1024:
        raise RuntimeError(
            "KAYN_CONNECTOR_MAX_MESSAGE_BYTES must be between 1048576 and 16777216"
        )
    return value


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()


__all__ = ["KaynAgentTarget", "register_kayn_agent_target", "serve"]
