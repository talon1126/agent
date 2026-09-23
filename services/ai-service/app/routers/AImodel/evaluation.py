"""Non-streaming evaluation adapter over the production chat workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.routers.AImodel.goal_orchestrator import ShoppingGoalOrchestrator
from app.routers.AImodel.memory import AiModelMemoryStore
from app.routers.AImodel.schemas import AiModelChatRequest, AiModelChatResponse
from app.routers.AImodel.service import (
    AiModelExecutionCapture,
    StreamingAgentRunner,
    stream_chat_events,
)
from app.routers.AImodel.tool_executor import ExecutionCancellation

_MAX_EVALUATION_CONTEXTS = 20
_MAX_CONTEXT_CHARS = 16_000
_MAX_TOOL_INPUT_CHARS = 256
_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_ORDER_PATTERN = re.compile(
    r"(?i)(?:order|订单)\s*[:：#-]?\s*[A-Za-z0-9][A-Za-z0-9_-]{3,}"
)
_EXTERNAL_STEP_TO_TOOL = {
    "product_search": "product_search",
    "snapshot": "product_snapshot",
    "review_fetch": "product_reviews",
    "rag_lookup": "rag_lookup",
}
_RESULT_TO_TOOL = {
    "search_products": "product_search",
    "search_product_catalog": "product_search",
    "get_product_detail_from_link": "product_snapshot",
    "get_product_reviews": "product_reviews",
    "rag_tool": "rag_lookup",
    "search_shopping_guides": "rag_lookup",
    "search_web_with_tavily": "web_search",
}


class AiModelEvaluationError(RuntimeError):
    """Stable evaluation failure that does not expose SSE payload content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AiModelEvaluationResult(BaseModel):
    """Kayn target result produced after the Agent reaches a terminal response."""

    model_config = ConfigDict(extra="forbid")

    output: str
    context: list[str] = Field(default_factory=list)
    metadata: dict[str, Any]
    toolCalls: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_response(
        cls,
        response: AiModelChatResponse,
        *,
        execution_capture: AiModelExecutionCapture | None = None,
    ) -> "AiModelEvaluationResult":
        metadata = response.model_dump(mode="json")
        _redact_action_tokens(metadata)
        contexts, context_refs = _evaluation_contexts(execution_capture)
        evaluation_metadata = _evaluation_metadata(execution_capture, context_refs)
        if evaluation_metadata:
            metadata["evaluation"] = evaluation_metadata
        return cls(
            output=response.answer,
            context=contexts,
            metadata=metadata,
            toolCalls=_evaluation_tool_calls(execution_capture),
        )


def collect_chat_evaluation(
    events: Iterable[str],
    *,
    execution_capture: AiModelExecutionCapture | None = None,
) -> AiModelEvaluationResult:
    """Consume one production SSE stream and return its single validated result."""

    final_response: AiModelChatResponse | None = None
    for raw_event in events:
        event_name, data = _parse_terminal_event(raw_event)
        if event_name is None:
            continue
        if event_name == "error":
            raise AiModelEvaluationError("agent_error")
        if final_response is not None:
            raise AiModelEvaluationError("duplicate_done")
        try:
            final_response = AiModelChatResponse.model_validate(data)
        except ValidationError as error:
            raise AiModelEvaluationError("invalid_done") from error

    if final_response is None:
        raise AiModelEvaluationError("missing_done")
    return AiModelEvaluationResult.from_response(
        final_response,
        execution_capture=execution_capture,
    )


def evaluate_chat_non_streaming(
    request: AiModelChatRequest,
    *,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
    streaming_agent_runner: StreamingAgentRunner | None = None,
    memory_store: AiModelMemoryStore | None = None,
    goal_orchestrator: ShoppingGoalOrchestrator | None = None,
    langchain_callbacks: Iterable[Any] | None = None,
) -> AiModelEvaluationResult:
    """Run the production Agent path while withholding intermediate SSE events."""

    execution_capture = AiModelExecutionCapture()
    return collect_chat_evaluation(
        stream_chat_events(
            request,
            mock_api_url=mock_api_url,
            http_client=http_client,
            streaming_agent_runner=streaming_agent_runner,
            memory_store=memory_store,
            goal_orchestrator=goal_orchestrator,
            execution_capture=execution_capture,
            langchain_callbacks=langchain_callbacks,
        ),
        execution_capture=execution_capture,
    )


async def evaluate_chat_non_streaming_async(
    request: AiModelChatRequest,
    *,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
    streaming_agent_runner: StreamingAgentRunner | None = None,
    memory_store: AiModelMemoryStore | None = None,
    goal_orchestrator: ShoppingGoalOrchestrator | None = None,
    langchain_callbacks: Iterable[Any] | None = None,
) -> tuple[AiModelEvaluationResult, AiModelExecutionCapture]:
    """Expose the production stream through an async SDK-compatible boundary."""

    execution_capture = AiModelExecutionCapture()
    cancellation = ExecutionCancellation()
    try:
        result = await asyncio.to_thread(
            collect_chat_evaluation,
            stream_chat_events(
                request,
                mock_api_url=mock_api_url,
                http_client=http_client,
                streaming_agent_runner=streaming_agent_runner,
                memory_store=memory_store,
                goal_orchestrator=goal_orchestrator,
                execution_capture=execution_capture,
                langchain_callbacks=langchain_callbacks,
                execution_cancellation=cancellation,
            ),
            execution_capture=execution_capture,
        )
    except asyncio.CancelledError:
        cancellation.cancel()
        raise
    return result, execution_capture


def _evaluation_contexts(
    capture: AiModelExecutionCapture | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    if capture is None:
        return [], []
    candidates: list[tuple[str, dict[str, Any]]] = []
    runtime = capture.runtime_result
    if runtime is not None:
        candidates.extend(
            (
                item.content,
                {
                    "sourceType": item.source_type,
                    "sourceId": item.source_id,
                    "title": item.title,
                },
            )
            for item in runtime.evaluation_contexts
        )
    for result in capture.tool_results:
        if not result.ok:
            continue
        if result.tool in {"rag_tool", "search_shopping_guides"}:
            content = str(result.data.get("content") or "").strip()
            if content:
                candidates.append(
                    (
                        content,
                        {
                            "sourceType": "rag_final_context",
                            "sourceId": str(result.data.get("trace_id") or "rag-context"),
                            "title": "RAG final context",
                        },
                    )
                )
        elif result.tool == "search_web_with_tavily":
            for index, content in enumerate(result.data.get("contents") or (), 1):
                if isinstance(content, str) and content.strip():
                    candidates.append(
                        (
                            content.strip(),
                            {
                                "sourceType": "web_search",
                                "sourceId": f"web-result-{index}",
                                "title": None,
                            },
                        )
                    )

    contexts: list[str] = []
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for content, reference in candidates:
        bounded = content[:_MAX_CONTEXT_CHARS].rstrip()
        fingerprint = hashlib.sha256(bounded.encode("utf-8")).hexdigest()
        if not bounded or fingerprint in seen:
            continue
        seen.add(fingerprint)
        contexts.append(bounded)
        refs.append({**reference, "sha256": fingerprint, "chars": len(bounded)})
        if len(contexts) >= _MAX_EVALUATION_CONTEXTS:
            break
    return contexts, refs


def _evaluation_metadata(
    capture: AiModelExecutionCapture | None,
    context_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    if capture is None:
        return {}
    metadata: dict[str, Any] = {"contextRefs": context_refs}
    trace_context = capture.trace_context
    if trace_context is not None:
        metadata.update(
            {
                "agentTraceId": trace_context.trace_id,
                "agentTraceStatus": trace_context.status.value,
                "queryTraceIds": list(trace_context.query_trace_ids),
            }
        )
    runtime = capture.runtime_result
    if runtime is not None:
        metadata["execution"] = {
            "taskType": runtime.planning.task_type.value,
            "planId": runtime.execution.plan_id,
            "status": runtime.execution.status.value,
            "stopReason": runtime.execution.stop_reason.value,
            "durationMs": round(runtime.execution.duration_ms, 3),
            "steps": [
                {
                    "stepId": step.step_id,
                    "stepType": step.step_type,
                    "status": step.status.value,
                    "attemptCount": step.attempt_count,
                    "durationMs": round(step.duration_ms, 3),
                    "errorCode": step.error_code,
                }
                for step in runtime.execution.steps
            ],
        }
    retrieval_evidence = _retrieval_evidence(capture.tool_results)
    if retrieval_evidence:
        metadata["retrievalEvidence"] = retrieval_evidence
    return metadata


def _retrieval_evidence(
    tool_results: tuple[Any, ...],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for result in tool_results:
        if result.tool not in {"rag_tool", "search_shopping_guides"} or not result.ok:
            continue
        citations = []
        for citation in result.data.get("citations") or ():
            if not isinstance(citation, dict):
                continue
            citations.append(
                {
                    key: citation.get(key)
                    for key in (
                        "document_id",
                        "chunk_id",
                        "title",
                        "section_path",
                        "score",
                        "trace_id",
                    )
                    if citation.get(key) is not None
                }
            )
        evidence.append(
            {
                "queryTraceId": result.data.get("trace_id"),
                "citations": citations[:_MAX_EVALUATION_CONTEXTS],
            }
        )
    return evidence


def _evaluation_tool_calls(
    capture: AiModelExecutionCapture | None,
) -> list[dict[str, Any]]:
    if capture is None:
        return []
    calls: list[dict[str, Any]] = []
    remaining = list(capture.tool_results)
    runtime = capture.runtime_result
    if runtime is not None:
        for step in runtime.execution.steps:
            name = _EXTERNAL_STEP_TO_TOOL.get(step.step_type)
            if name is None:
                continue
            result = _take_matching_result(remaining, name)
            call = {
                "name": name,
                "arguments": _tool_arguments(result),
                "status": _tool_status(step.status.value),
                "stepId": step.step_id,
                "attemptCount": step.attempt_count,
                "durationMs": round(step.duration_ms, 3),
            }
            if step.error_code:
                call["errorCode"] = step.error_code
            if result is not None:
                call["resultSummary"] = _tool_result_summary(result)
            calls.append(call)
    for result in remaining:
        calls.append(
            {
                "name": _RESULT_TO_TOOL.get(result.tool, result.tool),
                "arguments": _tool_arguments(result),
                "status": "succeeded" if result.ok else "failed",
                "resultSummary": _tool_result_summary(result),
            }
        )
    return calls


def _take_matching_result(results: list[Any], tool_name: str) -> Any | None:
    for index, result in enumerate(results):
        if _RESULT_TO_TOOL.get(result.tool, result.tool) == tool_name:
            return results.pop(index)
    return None


def _tool_arguments(result: Any | None) -> dict[str, Any]:
    if result is None:
        return {}
    arguments: dict[str, Any] = {}
    canonical_name = _RESULT_TO_TOOL.get(result.tool, result.tool)
    if canonical_name in {"product_search", "rag_lookup", "web_search"}:
        arguments["query"] = _redact_tool_input(result.input)
    elif result.input:
        arguments["inputSha256"] = hashlib.sha256(
            result.input.encode("utf-8")
        ).hexdigest()
    if result.item_id:
        arguments["itemId"] = result.item_id
    return arguments


def _tool_result_summary(result: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {"ok": bool(result.ok)}
    data = result.data if isinstance(result.data, dict) else {}
    if result.tool in {"rag_tool", "search_shopping_guides"}:
        summary.update(
            {
                "queryTraceId": data.get("trace_id"),
                "citationCount": len(data.get("citations") or ()),
                "isEmpty": bool(data.get("is_empty", True)),
            }
        )
    elif result.tool in {"search_products", "search_product_catalog"}:
        summary["itemCount"] = len(data.get("items") or ())
    elif result.tool == "search_web_with_tavily":
        summary["resultCount"] = int(data.get("result_count") or 0)
    if result.error:
        summary["errorCode"] = _safe_error_code(result.error)
    return summary


def _tool_status(status: str) -> str:
    if status == "success":
        return "succeeded"
    if status == "skipped":
        return "skipped"
    return "failed"


def _redact_tool_input(value: str) -> str:
    bounded = " ".join(value.split())[:_MAX_TOOL_INPUT_CHARS]
    bounded = _PHONE_PATTERN.sub("[REDACTED_PHONE]", bounded)
    bounded = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", bounded)
    return _ORDER_PATTERN.sub("[REDACTED_ORDER]", bounded)


def _safe_error_code(error: str) -> str:
    prefix = error.split(":", 1)[0].strip().casefold()
    normalized = re.sub(r"[^a-z0-9_]+", "_", prefix).strip("_")
    return (normalized or "tool_error")[:64]


def _parse_terminal_event(raw_event: str) -> tuple[str | None, dict[str, Any] | None]:
    event_name: str | None = None
    data_lines: list[str] = []
    for line in raw_event.splitlines():
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())

    if event_name not in {"done", "error"}:
        return None, None
    if not data_lines:
        raise AiModelEvaluationError("invalid_terminal_event")
    try:
        data = json.loads("\n".join(data_lines))
    except (TypeError, ValueError) as error:
        raise AiModelEvaluationError("invalid_terminal_event") from error
    if not isinstance(data, dict):
        raise AiModelEvaluationError("invalid_terminal_event")
    return event_name, data


def _redact_action_tokens(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "action_token":
                value[key] = "[REDACTED]"
            else:
                _redact_action_tokens(child)
    elif isinstance(value, list):
        for child in value:
            _redact_action_tokens(child)


__all__ = [
    "AiModelEvaluationError",
    "AiModelEvaluationResult",
    "collect_chat_evaluation",
    "evaluate_chat_non_streaming",
    "evaluate_chat_non_streaming_async",
]
