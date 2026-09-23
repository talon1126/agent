"""Standalone Kayn Connector target for the TalonMart shopping Agent."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

from app.routers.AImodel.evaluation import (
    AiModelEvaluationResult,
    evaluate_chat_non_streaming_async,
)
from app.routers.AImodel.kayn_trace import export_agent_trace_to_kayn
from app.routers.AImodel.memory import get_aimodel_memory_store
from app.routers.AImodel.schemas import AiModelChatRequest
from app.routers.AImodel.service import AiModelExecutionCapture
from app.routers.AImodel.tools import close_rag_knowledge_client

EvaluationRunner = Callable[..., Awaitable[tuple[AiModelEvaluationResult, AiModelExecutionCapture]]]
CallbackFactory = Callable[[], Any]


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
    ) -> AiModelEvaluationResult:
        request = AiModelChatRequest.model_validate(
            {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "message": input,
                "links": links or [],
                "request_version": "v2" if page_context is not None else "v1",
                "page_context": page_context,
            }
        )
        callbacks = (
            [self._callback_factory()]
            if self._callback_factory is not None
            else []
        )
        result, capture = await self._evaluation_runner(
            request,
            mock_api_url=self._mock_api_url,
            langchain_callbacks=callbacks,
        )
        if capture.trace_context is not None:
            export_agent_trace_to_kayn(self._client, capture.trace_context)
        return result


def register_kayn_agent_target(
    client: Any,
    *,
    mock_api_url: str | None = None,
    evaluation_runner: EvaluationRunner = evaluate_chat_non_streaming_async,
) -> tuple[KaynAgentTarget, Any]:
    """Register one async endpoint and keep SDK imports outside the web process."""

    from kayn_sdk import endpoint
    from kayn_sdk.integrations.langchain import create_langchain_callback

    target = KaynAgentTarget(
        client,
        mock_api_url=mock_api_url or os.getenv("MOCK_API_URL", "http://mock-api:8000"),
        evaluation_runner=evaluation_runner,
        callback_factory=lambda: create_langchain_callback(client),
    )
    registered = endpoint(
        name=os.getenv("KAYN_AGENT_ENDPOINT_NAME", "talonmart-shopping-agent"),
        description="TalonMart shopping Agent evaluation target",
        version="1",
        client=client,
        metadata={"agent": "talonmart", "integration": "deep"},
    )(target.invoke)
    return target, registered


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
    connector = KaynConnector(client)
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


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()


__all__ = ["KaynAgentTarget", "register_kayn_agent_target", "serve"]
