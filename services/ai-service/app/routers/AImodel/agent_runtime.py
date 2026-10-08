"""Production wiring for the bounded shopping-agent execution chain.

This module binds the D-stage contracts to the existing product and RAG
services.  The planner remains declarative, every external read is authorized
immediately before execution, and factual product responses pass through the
grounding verifier before they leave the service boundary.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

import httpx

from .agent_trace import (
    AgentTraceContext,
    AgentTraceEventType,
    AgentTraceStatus,
    record_intent_route,
)
from .candidate_service import (
    CandidatePolicy,
    CandidateReference,
    CandidateSet,
    CandidateSetStatus,
    CandidateSource,
    ExclusionAction,
    apply_hard_filters,
)
from .clarification import ClarificationDecision, ClarificationReason
from .comparison import (
    ComparisonCell,
    ComparisonCellStatus,
    ComparisonMatrix,
    ComparisonRow,
    EvidenceRef,
    EvidenceSourceType,
    ProductComparisonService,
    ReviewBatchClient,
    ReviewCollection,
    ReviewInsightReport,
    ReviewInsightStatus,
    load_comparison_policy,
)
from .feature_normalizer import FeatureNormalizer, load_feature_profiles
from .intent_router import AImodelIntentRoute, load_default_aimodel_intent_router
from .plan_models import AgentPlanValidator, PlanValueType, StepType, load_plan_policy
from .planner import HierarchicalPlanner, PlanningResult, PlanningTaskType
from .product_models import (
    FactStatus,
    FreshnessState,
    ProductSnapshot,
    ProductSpecifications,
)
from .product_snapshot import ProductSnapshotClient
from .ranking import ProductRanker, RankingResult, load_ranking_policy
from .schemas import (
    AiModelAnswerPayload,
    AiModelChatRequest,
    AiModelClarificationOption,
    AiModelClarificationPayload,
    AiModelFallbackPayload,
    AiModelPageContext,
    AiModelProductListPayload,
    AiModelProductRef,
    AiModelRecommendationPayload,
    AiModelRecommendationReason,
    AiModelResponsePayload,
    AiModelToolResult,
)
from .shopping_goal import DecisionStage, GoalField, ShoppingGoal
from .tool_executor import (
    BoundedParallelToolExecutor,
    ExecutionCancellation,
    ExecutionValue,
    PlanExecutionResult,
    PlanExecutionStatus,
    StepExecutionStatus,
    StepExecutionContext,
    StepExecutionError,
    StepHandler,
    StepInvocation,
)
from .tool_policy import AgentToolCall, AgentToolName
from .tools import (
    RagKnowledgeClient,
    build_product_url,
    parse_item_id_from_link,
    rag_tool,
    search_products,
)
from .verifier import (
    ClaimType,
    GroundedResponseDraft,
    GroundingVerifier,
    ResponseClaim,
)


_SUPPORTED_TASKS = frozenset(
    {
        PlanningTaskType.KNOWLEDGE,
        PlanningTaskType.PRODUCT_DETAIL,
        PlanningTaskType.PRODUCT_SEARCH,
        PlanningTaskType.RECOMMEND,
        PlanningTaskType.COMPARE,
    }
)


@dataclass(frozen=True, slots=True)
class AgentEvaluationContext:
    """One bounded evidence block actually used by the deterministic runtime."""

    content: str
    source_type: str
    source_id: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRuntimeResult:
    """One fully executed main-chain result returned to the API layer."""

    payload: AiModelResponsePayload
    planning: PlanningResult
    execution: PlanExecutionResult
    tool_results: tuple[AiModelToolResult, ...] = ()
    evaluation_contexts: tuple[AgentEvaluationContext, ...] = ()


@dataclass(slots=True)
class _TurnState:
    request: AiModelChatRequest
    goal: ShoppingGoal
    route: AImodelIntentRoute
    conversation_id: int
    mock_api_url: str
    http_client: httpx.Client | None
    rag_client: RagKnowledgeClient | None
    trace_context: AgentTraceContext | None
    cancellation: ExecutionCancellation
    tool_results: list[AiModelToolResult] = field(default_factory=list)
    candidates: tuple[CandidateReference, ...] = ()
    snapshot: ProductSnapshot | None = None
    candidate_set: CandidateSet | None = None
    ranking: RankingResult | None = None
    comparison: ComparisonMatrix | None = None
    reviews: tuple[ReviewCollection, ...] = ()
    review_reports: tuple[ReviewInsightReport, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    canonical_draft: GroundedResponseDraft | None = None
    search_query: str | None = None
    requested_review_topics: tuple[str, ...] = ()


class ShoppingAgentRuntime:
    """Plan, authorize, execute, and verify one supported Agent request."""

    def __init__(
        self,
        *,
        planner: HierarchicalPlanner | None = None,
        executor: BoundedParallelToolExecutor | None = None,
        verifier: GroundingVerifier | None = None,
        rag_client: RagKnowledgeClient | None = None,
    ) -> None:
        self._planner = planner or HierarchicalPlanner(
            AgentPlanValidator(load_plan_policy())
        )
        self._executor = executor or BoundedParallelToolExecutor()
        self._verifier = verifier or GroundingVerifier()
        self._rag_client = rag_client

    def run(
        self,
        request: AiModelChatRequest,
        *,
        conversation_id: int,
        goal: ShoppingGoal,
        clarification: ClarificationDecision | None,
        mock_api_url: str,
        http_client: httpx.Client | None = None,
        trace_context: AgentTraceContext | None = None,
        cancellation: ExecutionCancellation | None = None,
    ) -> AgentRuntimeResult | None:
        """Return ``None`` when the route belongs to a legacy-only capability."""

        route, route_candidates = (
            load_default_aimodel_intent_router().route_with_candidates(request.message)
        )
        page_context = _effective_page_context(request)
        route = _recover_goal_backed_route(
            route,
            goal,
            request.message,
            page_context,
        )
        if trace_context is not None:
            record_intent_route(
                trace_context,
                route,
                candidates=route_candidates,
            )
        decision = clarification or _proceed_decision()
        planning = self._planner.plan(
            intent_route=route,
            shopping_goal=goal,
            clarification=decision,
            page_context=page_context,
            trace_context=trace_context,
        )
        if planning.task_type not in _SUPPORTED_TASKS:
            return None
        state = _TurnState(
            request=request,
            goal=goal,
            route=route,
            conversation_id=conversation_id,
            mock_api_url=mock_api_url,
            http_client=http_client,
            rag_client=self._rag_client,
            trace_context=trace_context,
            cancellation=cancellation or ExecutionCancellation(),
        )
        return asyncio.run(
            self._run_plan(
                state,
                planning=planning,
                page_context=page_context,
            )
        )

    async def _run_plan(
        self,
        state: _TurnState,
        *,
        planning: PlanningResult,
        page_context: AiModelPageContext | None,
    ) -> AgentRuntimeResult:
        candidate_ids = _candidate_ids_from_request(state.request, page_context)
        context_inputs: dict[str, ExecutionValue] = {}
        if page_context is not None:
            context_inputs["page_context"] = ExecutionValue(
                value_type=PlanValueType.PAGE_CONTEXT,
                value=page_context,
            )
        execution = await self._executor.execute(
            planning.plan,
            StepExecutionContext(
                user_id=state.request.user_id,
                conversation_id=state.conversation_id,
                request_inputs={
                    "question": ExecutionValue(
                        value_type=PlanValueType.USER_QUERY,
                        value=state.request.message,
                    )
                },
                goal_inputs={
                    "shopping_goal": ExecutionValue(
                        value_type=PlanValueType.SHOPPING_GOAL,
                        value=state.goal,
                    )
                },
                context_inputs=context_inputs,
                candidate_item_ids=candidate_ids,
                cancellation=state.cancellation,
                trace_context=state.trace_context,
            ),
            self._handlers(
                state,
                planning.task_type,
                page_context,
                max_candidates=planning.plan.budget.max_candidates,
            ),
        )
        if execution.status is not PlanExecutionStatus.SUCCESS:
            payload: AiModelResponsePayload = _execution_failure_payload(
                execution,
                state.goal,
            )
        else:
            terminal = execution.steps[-1].output
            if terminal is None or not isinstance(terminal.value, GroundedResponseDraft):
                payload = AiModelFallbackPayload(
                    answer="本轮结果缺少可验证的终态输出，请稍后重试。",
                    reason_code="terminal_output_missing",
                )
            elif planning.task_type is PlanningTaskType.KNOWLEDGE:
                payload = terminal.value.payload
            else:
                payload = await self._verify_product_result(state, terminal.value)
        return AgentRuntimeResult(
            payload=payload,
            planning=planning,
            execution=execution,
            tool_results=tuple(state.tool_results),
            evaluation_contexts=_build_evaluation_contexts(state, payload),
        )

    async def _verify_product_result(
        self,
        state: _TurnState,
        draft: GroundedResponseDraft,
    ) -> AiModelResponsePayload:
        if state.snapshot is None or state.candidate_set is None:
            return AiModelFallbackPayload(
                answer="商品事实不完整，暂时无法给出可靠结果。",
                reason_code="grounding_inputs_missing",
            )
        if state.ranking is None:
            state.ranking = _rank_products(state, state.snapshot, state.candidate_set)

        async def repair(_: object) -> GroundedResponseDraft:
            if state.canonical_draft is None:
                raise RuntimeError("canonical response draft is unavailable")
            return state.canonical_draft

        outcome = await self._verifier.verify_with_repair(
            draft=draft,
            goal=state.goal,
            snapshot=state.snapshot,
            candidate_set=state.candidate_set,
            ranking=state.ranking,
            evidence=state.evidence,
            comparison=state.comparison,
            composer=repair,
            trace_context=state.trace_context,
        )
        return outcome.final_payload

    def _handlers(
        self,
        state: _TurnState,
        task_type: PlanningTaskType,
        page_context: AiModelPageContext | None,
        *,
        max_candidates: int,
    ) -> dict[str, StepHandler]:
        snapshot_client = ProductSnapshotClient(
            state.mock_api_url,
            http_client=state.http_client,
        )
        candidate_policy = (
            CandidatePolicy(minimum_stock=0)
            if task_type is PlanningTaskType.COMPARE
            else CandidatePolicy()
        )
        derived_search_query = _product_search_query(
            state.goal,
            page_context,
            fallback_message=state.request.message,
        )
        derived_search_category = _product_search_category(state.goal)

        async def search(_: StepInvocation) -> ExecutionValue:
            state.search_query = derived_search_query
            result = await asyncio.to_thread(
                search_products,
                derived_search_query,
                mock_api_url=state.mock_api_url,
                category=derived_search_category,
                minimum_results=(2 if task_type is PlanningTaskType.COMPARE else 1),
                http_client=state.http_client,
            )
            state.tool_results.append(result)
            state.candidates = _merge_candidates(
                state.request,
                page_context,
                result,
                limit=max_candidates,
            )
            if not state.candidates:
                raise StepExecutionError("no_candidate")
            return ExecutionValue(
                value_type=PlanValueType.CANDIDATE_REFS,
                value=state.candidates,
            )

        def search_call(_: StepInvocation) -> AgentToolCall:
            return AgentToolCall(
                tool_name=AgentToolName.PRODUCT_SEARCH.value,
                arguments=_scope_arguments(
                    state,
                    query=derived_search_query,
                    category=derived_search_category,
                ),
            )

        async def snapshot(invocation: StepInvocation) -> ExecutionValue:
            item_ids = _snapshot_item_ids(invocation, page_context)
            state.snapshot = await asyncio.to_thread(
                snapshot_client.capture_for_turn,
                turn_id=f"{state.conversation_id}:{invocation.plan_id}",
                item_ids=item_ids,
                trace_context=state.trace_context,
            )
            initial_candidates = state.candidates or tuple(
                CandidateReference(
                    item_id=item_id,
                    sources=(CandidateSource.PAGE,),
                )
                for item_id in state.snapshot.requested_item_ids
            )
            initial_set = apply_hard_filters(
                state.goal,
                state.snapshot,
                policy=candidate_policy,
                candidates=initial_candidates,
                search_query=state.search_query or derived_search_query,
            )
            refresh_reasons = _fact_refresh_reason_codes(initial_set)
            if refresh_reasons:
                refresh_event = (
                    state.trace_context.begin_event(
                        AgentTraceEventType.CONTEXT,
                        stage="fact_refresh",
                        summary={
                            "attempt": 1,
                            "reason_codes": list(refresh_reasons),
                        },
                        related_ids={"snapshot_id": state.snapshot.snapshot_id},
                    )
                    if state.trace_context is not None
                    else None
                )
                refresh_client = ProductSnapshotClient(
                    state.mock_api_url,
                    http_client=state.http_client,
                )
                state.snapshot = await asyncio.to_thread(
                    refresh_client.capture_for_turn,
                    turn_id=(
                        f"{state.conversation_id}:{invocation.plan_id}:refresh-1"
                    ),
                    item_ids=item_ids,
                    trace_context=state.trace_context,
                )
                refreshed_set = apply_hard_filters(
                    state.goal,
                    state.snapshot,
                    policy=candidate_policy,
                    candidates=initial_candidates,
                    search_query=state.search_query or derived_search_query,
                )
                remaining = _fact_refresh_reason_codes(refreshed_set)
                if refresh_event is not None:
                    refresh_event.related_ids["refreshed_snapshot_id"] = (
                        state.snapshot.snapshot_id
                    )
                    refresh_event.finish(
                        AgentTraceStatus.SUCCESS,
                        summary={
                            "remaining_reason_codes": list(remaining),
                        },
                    )
            return ExecutionValue(
                value_type=PlanValueType.PRODUCT_SNAPSHOT,
                value=state.snapshot,
            )

        def snapshot_call(invocation: StepInvocation) -> AgentToolCall:
            return AgentToolCall(
                tool_name=AgentToolName.PRODUCT_SNAPSHOT.value,
                arguments=_scope_arguments(
                    state,
                    item_ids=_snapshot_item_ids(invocation, page_context),
                ),
            )

        async def reviews(invocation: StepInvocation) -> ExecutionValue:
            item_ids = list(_review_item_ids(invocation, page_context)[:5])
            state.reviews = await asyncio.to_thread(
                _fetch_reviews,
                state.mock_api_url,
                item_ids,
                state.http_client,
            )
            return ExecutionValue(
                value_type=PlanValueType.REVIEW_COLLECTION,
                value=state.reviews,
            )

        def reviews_call(invocation: StepInvocation) -> AgentToolCall:
            return AgentToolCall(
                tool_name=AgentToolName.PRODUCT_REVIEWS.value,
                arguments=_scope_arguments(
                    state,
                    item_ids=_review_item_ids(invocation, page_context)[:5],
                ),
            )

        async def knowledge(_: StepInvocation) -> ExecutionValue:
            result = await asyncio.to_thread(
                rag_tool,
                _bounded_rag_query(state.request.message),
                rag_client=state.rag_client,
                collection=(
                    state.route.collection if not state.route.collections else None
                ),
                collections=state.route.collections or None,
            )
            state.tool_results.append(result)
            return ExecutionValue(
                value_type=PlanValueType.KNOWLEDGE_RESULT,
                value=result,
            )

        def knowledge_call(_: StepInvocation) -> AgentToolCall:
            return AgentToolCall(
                tool_name=AgentToolName.RAG_LOOKUP.value,
                arguments=_scope_arguments(
                    state,
                    query=_bounded_rag_query(state.request.message),
                    collections=state.route.collections,
                    top_k=5,
                    no_rerank=False,
                    include_image_base64=False,
                ),
            )

        async def filter_candidates(invocation: StepInvocation) -> ExecutionValue:
            snapshot_value = invocation.inputs["products"].value
            if not isinstance(snapshot_value, ProductSnapshot):
                raise StepExecutionError("invalid_product_snapshot")
            candidates = state.candidates or tuple(
                CandidateReference(
                    item_id=item_id,
                    sources=(CandidateSource.PAGE,),
                )
                for item_id in snapshot_value.requested_item_ids
            )
            state.candidate_set = apply_hard_filters(
                state.goal,
                snapshot_value,
                policy=candidate_policy,
                candidates=candidates,
                search_query=state.search_query or derived_search_query,
            )
            _record_candidate_filter_trace(state)
            return ExecutionValue(
                value_type=PlanValueType.CANDIDATE_SET,
                value=state.candidate_set,
            )

        async def rank(_: StepInvocation) -> ExecutionValue:
            if state.snapshot is None or state.candidate_set is None:
                raise StepExecutionError("ranking_inputs_missing")
            state.ranking = _rank_products(state, state.snapshot, state.candidate_set)
            return ExecutionValue(
                value_type=PlanValueType.RANKING_RESULT,
                value=state.ranking,
            )

        async def compare(_: StepInvocation) -> ExecutionValue:
            if state.snapshot is None or state.ranking is None:
                raise StepExecutionError("comparison_inputs_missing")
            selected = tuple(item.item_id for item in state.ranking.ranked[:5])
            if len(selected) < 2:
                raise StepExecutionError("comparison_candidates_insufficient")
            registry = load_feature_profiles()
            normalized = {
                item_id: FeatureNormalizer(registry).normalize(item)
                for item_id, item in state.snapshot.items_by_id.items()
                if item_id in selected
            }
            service = ProductComparisonService(registry, load_comparison_policy())
            state.comparison = service.build_matrix(
                ranking=state.ranking,
                products=state.snapshot.items_by_id,
                normalized_features=normalized,
                selected_item_ids=selected,
            )
            state.comparison = _augment_comparison_core_facts(
                state.comparison,
                state.snapshot,
            )
            state.evidence = state.comparison.evidence
            return ExecutionValue(
                value_type=PlanValueType.COMPARISON_MATRIX,
                value=state.comparison,
            )

        async def compose(invocation: StepInvocation) -> ExecutionValue:
            state.canonical_draft = _compose_draft(state, task_type, invocation)
            return ExecutionValue(
                value_type=PlanValueType.RESPONSE_DRAFT,
                value=state.canonical_draft,
            )

        return {
            StepType.PRODUCT_SEARCH.value: StepHandler(
                invoke=search,
                tool_call_factory=search_call,
                idempotent=True,
            ),
            StepType.SNAPSHOT.value: StepHandler(
                invoke=snapshot,
                tool_call_factory=snapshot_call,
                idempotent=True,
            ),
            StepType.REVIEW_FETCH.value: StepHandler(
                invoke=reviews,
                tool_call_factory=reviews_call,
                idempotent=True,
            ),
            StepType.RAG_LOOKUP.value: StepHandler(
                invoke=knowledge,
                tool_call_factory=knowledge_call,
                idempotent=True,
            ),
            StepType.FILTER.value: StepHandler(invoke=filter_candidates),
            StepType.RANK.value: StepHandler(invoke=rank),
            StepType.COMPARE.value: StepHandler(invoke=compare),
            StepType.COMPOSE.value: StepHandler(invoke=compose),
        }


def _proceed_decision() -> ClarificationDecision:
    return ClarificationDecision(
        policy_version="agent-runtime-v1",
        should_ask=False,
        may_proceed=True,
        reason=ClarificationReason.NO_CLARIFICATION_NEEDED,
    )


def _recover_goal_backed_route(
    route: AImodelIntentRoute,
    goal: ShoppingGoal,
    message: str,
    page_context: AiModelPageContext | None = None,
) -> AImodelIntentRoute:
    """Resume an existing shopping goal for explicit follow-up commands."""

    if not route.fallback_used or goal.decision_stage not in {
        DecisionStage.SEARCHING,
        DecisionStage.COMPARING,
    }:
        return route
    normalized_message = " ".join(message.casefold().split())
    category = next(
        (
            item
            for item in goal.hard_constraints
            if item.field is GoalField.CATEGORY
        ),
        None,
    )
    category_quote = (
        " ".join(category.evidence.quote.casefold().split())
        if category is not None and category.evidence.quote
        else ""
    )
    retry_request = re.search(r"重试|再试|重新查|列出商品|列出候选", normalized_message)
    if not category_quote or (
        category_quote not in normalized_message and retry_request is None
    ):
        return route
    if page_context is not None and page_context.current_item_id is not None:
        return replace(
            route,
            action="product_api",
            collection=None,
            collections=(),
            domain="support",
            category="presale",
            intent="product_detail",
            confidence=max(route.confidence, 0.85),
            reason="page_item_goal_context_recovered_after_clarification",
            matched_rule="goal_context_recovery",
            rag_enabled=False,
        )
    if retry_request is not None or re.search(
        r"只看|只要|不要|排除|预算|\d+(?:\.\d+)?\s*元\s*(?:以内|以下)|"
        r"不能超过|不超过|最高|至少|必须|找|搜索",
        normalized_message,
    ):
        return replace(
            route,
            action="product_api",
            collection=None,
            collections=(),
            domain="support",
            category="presale",
            intent="catalog_search",
            confidence=max(route.confidence, 0.85),
            reason="goal_constraints_recovered_for_catalog_search",
            matched_rule="goal_context_recovery",
            rag_enabled=False,
        )
    return replace(
        route,
        action="rag",
        collection="shopping_guides",
        collections=("shopping_guides",),
        domain="support",
        category="presale",
        intent="buying_recommendation",
        confidence=max(route.confidence, 0.8),
        reason="goal_context_recovered_after_clarification",
        matched_rule="goal_context_recovery",
        rag_enabled=True,
    )


def _effective_page_context(request: AiModelChatRequest) -> AiModelPageContext | None:
    if request.page_context is not None:
        return request.page_context
    item_ids = tuple(
        item_id
        for link in request.links
        if (item_id := parse_item_id_from_link(link)) is not None
    )
    if not item_ids:
        return None
    return AiModelPageContext(
        page_type="product",
        current_item_id=item_ids[0],
        candidate_refs=[{"item_id": item_id} for item_id in item_ids],
        source_event="request_links",
    )


def _candidate_ids_from_request(
    request: AiModelChatRequest,
    page_context: AiModelPageContext | None,
) -> tuple[str, ...]:
    values: list[str] = []
    if page_context is not None:
        if page_context.current_item_id is not None:
            values.append(str(page_context.current_item_id))
        values.extend(str(item.item_id) for item in page_context.candidate_refs)
    values.extend(
        item_id
        for link in request.links
        if (item_id := parse_item_id_from_link(link)) is not None
    )
    return tuple(dict.fromkeys(values))


def _merge_candidates(
    request: AiModelChatRequest,
    page_context: AiModelPageContext | None,
    search_result: AiModelToolResult,
    *,
    limit: int,
) -> tuple[CandidateReference, ...]:
    sources: dict[str, set[CandidateSource]] = {}
    order: list[str] = []

    def add(item_id: object, source: CandidateSource) -> None:
        normalized = str(item_id).strip()
        if not normalized:
            return
        if normalized not in sources:
            sources[normalized] = set()
            order.append(normalized)
        sources[normalized].add(source)

    for link in request.links:
        if item_id := parse_item_id_from_link(link):
            add(item_id, CandidateSource.EXPLICIT)
    if page_context is not None:
        if page_context.current_item_id is not None:
            add(page_context.current_item_id, CandidateSource.PAGE)
        for item in page_context.candidate_refs:
            add(item.item_id, CandidateSource.PAGE)
    scoped_item_ids = frozenset(sources)
    if search_result.ok:
        for item in search_result.data.get("items", []):
            if isinstance(item, dict) and item.get("item_id") is not None:
                item_id = str(item["item_id"]).strip()
                if not scoped_item_ids or item_id in scoped_item_ids:
                    add(item_id, CandidateSource.SEARCH)
    source_order = {
        CandidateSource.EXPLICIT: 0,
        CandidateSource.PAGE: 1,
        CandidateSource.SEARCH: 2,
    }
    return tuple(
        CandidateReference(
            item_id=item_id,
            sources=tuple(sorted(sources[item_id], key=source_order.__getitem__)),
        )
        for item_id in order[:limit]
    )


def _candidate_refs(value: Any) -> tuple[CandidateReference, ...]:
    if not isinstance(value, (tuple, list)) or not all(
        isinstance(item, CandidateReference) for item in value
    ):
        raise StepExecutionError("invalid_candidate_refs")
    return tuple(value)


def _snapshot_item_ids(
    invocation: StepInvocation,
    page_context: AiModelPageContext | None,
) -> tuple[str, ...]:
    candidate_value = invocation.inputs.get("candidate_refs")
    if candidate_value is not None:
        return tuple(item.item_id for item in _candidate_refs(candidate_value.value))
    if page_context is None or page_context.current_item_id is None:
        raise StepExecutionError("product_reference_missing")
    return (str(page_context.current_item_id),)


def _review_item_ids(
    invocation: StepInvocation,
    page_context: AiModelPageContext | None,
) -> tuple[str, ...]:
    candidate_value = invocation.inputs.get("candidate_refs")
    if candidate_value is not None:
        return tuple(item.item_id for item in _candidate_refs(candidate_value.value))
    if page_context is None:
        raise StepExecutionError("product_reference_missing")
    values: list[str] = []
    if page_context.current_item_id is not None:
        values.append(str(page_context.current_item_id))
    values.extend(str(item.item_id) for item in page_context.candidate_refs)
    item_ids = tuple(dict.fromkeys(values))
    if not item_ids:
        raise StepExecutionError("product_reference_missing")
    return item_ids


def _scope_arguments(state: _TurnState, **arguments: Any) -> dict[str, Any]:
    return {
        "user_id": state.request.user_id,
        "conversation_id": state.conversation_id,
        **arguments,
    }


def _bounded_query(query: str) -> str:
    return " ".join(query.split())[:512].rstrip()


def _product_search_category(goal: ShoppingGoal) -> str | None:
    for item in goal.hard_constraints:
        if item.field is GoalField.CATEGORY:
            category = " ".join(str(item.value).strip().split())
            return category or None
    return None


_SEARCH_QUERY_FIELDS = frozenset(
    {
        GoalField.BRAND,
        GoalField.CATEGORY,
        GoalField.SPECIFICATION,
        GoalField.USAGE_SCENARIO,
    }
)
_QUERY_PREFIX = re.compile(
    r"^(?:(?:请|麻烦)?(?:帮我|给我)?(?:想要|想买|购买|找|搜索|查找|推荐|看看)"
    r"(?:一款|一个|一些|一下)?\s*)+"
)
_QUERY_SUFFIX = re.compile(r"(?:有吗|吗|呢|吧)?[?？!！.。]*$")


def _product_search_query(
    goal: ShoppingGoal,
    page_context: AiModelPageContext | None,
    *,
    fallback_message: str,
) -> str:
    """Build a bounded catalog query from normalized goal facts and provenance."""

    raw_parts: list[object] = []
    if page_context is not None and page_context.search_query:
        raw_parts.append(page_context.search_query)
    for collection in (goal.hard_constraints, goal.preferences):
        for item in collection:
            if item.field not in _SEARCH_QUERY_FIELDS:
                continue
            if item.field is GoalField.CATEGORY and item.evidence.quote:
                raw_parts.append(item.evidence.quote)
            else:
                raw_parts.append(item.value)

    parts: list[str] = []
    seen: set[str] = set()
    for raw in raw_parts:
        part = " ".join(str(raw).strip().split())
        folded = part.casefold()
        if part and folded not in seen:
            parts.append(part)
            seen.add(folded)
    if parts:
        return _bounded_query(" ".join(parts))

    normalized = _bounded_query(fallback_message)
    normalized = _QUERY_PREFIX.sub("", normalized).strip()
    normalized = _QUERY_SUFFIX.sub("", normalized).strip(" ,，;；")
    return _bounded_query(normalized or fallback_message)


def _record_candidate_filter_trace(state: _TurnState) -> None:
    if state.trace_context is None or state.candidate_set is None:
        return
    candidate_set = state.candidate_set
    event = state.trace_context.begin_event(
        AgentTraceEventType.FILTER,
        stage="candidate_filter",
        summary={
            "recalled_count": len(candidate_set.recalled),
            "eligible_count": len(candidate_set.eligible),
            "excluded_count": len(candidate_set.excluded),
            "failure_count": len(candidate_set.failures),
            "reason_counts": [
                item.model_dump(mode="json") for item in candidate_set.reason_counts
            ],
            "suggestions": [
                item.model_dump(mode="json") for item in candidate_set.suggestions
            ],
        },
        related_ids={"snapshot_id": candidate_set.snapshot_id or "none"},
    )
    event.finish(AgentTraceStatus.SUCCESS)


def _fact_refresh_reason_codes(candidate_set: CandidateSet) -> tuple[str, ...]:
    codes = {
        reason.code
        for candidate in candidate_set.excluded
        for reason in candidate.reasons
        if reason.action is ExclusionAction.REFRESH_FACT
        or reason.code.endswith(("_missing", "_stale", "_unknown"))
    }
    return tuple(sorted(codes))


_CONFIRMED_MISMATCH_CODES = frozenset(
    {
        "brand_excluded",
        "brand_not_included",
        "budget_above_maximum",
        "budget_below_minimum",
        "currency_mismatch",
        "category_mismatch",
        "delivery_after_deadline",
        "delivery_unavailable",
        "insufficient_stock",
        "specification_mismatch",
    }
)


def _confirmed_mismatch_reason_codes(
    candidate_set: CandidateSet,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                reason.code
                for candidate in candidate_set.excluded
                for reason in candidate.reasons
                if reason.code in _CONFIRMED_MISMATCH_CODES
            }
        )
    )


def _known_mismatch_draft(
    state: _TurnState,
    reason_codes: tuple[str, ...],
) -> GroundedResponseDraft:
    budget_conflict = any(code.startswith("budget_") for code in reason_codes)
    page_context = _effective_page_context(state.request)
    if (
        budget_conflict
        and page_context is not None
        and page_context.current_item_id is not None
    ):
        return GroundedResponseDraft(
            payload=AiModelClarificationPayload(
                answer=(
                    "当前指定商品的已核验价格超出预算。请选择提高预算，"
                    "或保留预算并改看其他商品。"
                ),
                options=[
                    AiModelClarificationOption(
                        option_id="relax_budget",
                        label="提高预算",
                        value="budget_max:relax",
                    ),
                    AiModelClarificationOption(
                        option_id="change_candidate",
                        label="更换商品",
                        value="candidate:alternative",
                    ),
                ],
            )
        )
    constraints = _goal_constraint_summary(state.goal)
    retained = f"已核验条件：{constraints}。" if constraints else ""
    if "currency_mismatch" in reason_codes:
        return GroundedResponseDraft(
            payload=AiModelFallbackPayload(
                answer=(
                    "商品价格币种与 CNY 预算不一致，无法直接比较金额。"
                    f"{retained}请提供同币种价格后重试。"
                ),
                reason_code="currency_mismatch",
            )
        )
    return GroundedResponseDraft(
        payload=AiModelFallbackPayload(
            answer=(
                "当前候选与已确认的硬约束冲突，不能据此推荐无关商品。"
                f"{retained}请调整条件或更换候选后再试。"
            ),
            reason_code="confirmed_constraint_mismatch",
        )
    )


_STEP_LABELS = {
    StepType.PRODUCT_SEARCH.value: "商品搜索",
    StepType.SNAPSHOT.value: "商品详情读取",
    StepType.REVIEW_FETCH.value: "商品评论读取",
    StepType.RAG_LOOKUP.value: "知识检索",
    StepType.FILTER.value: "硬约束筛选",
    StepType.RANK.value: "候选排序",
    StepType.COMPARE.value: "商品比较",
    StepType.COMPOSE.value: "结果生成",
}
_GOAL_FIELD_LABELS = {
    GoalField.BRAND: "品牌",
    GoalField.BUDGET_MIN: "最低预算",
    GoalField.BUDGET_MAX: "预算上限",
    GoalField.CATEGORY: "品类",
    GoalField.DELIVERY_DEADLINE: "送达时间",
    GoalField.QUANTITY: "数量",
    GoalField.SPECIFICATION: "规格",
    GoalField.USAGE_SCENARIO: "使用场景",
}


def _goal_constraint_summary(
    goal: ShoppingGoal,
    *,
    include_category: bool = True,
) -> str:
    parts: list[str] = []
    for item in goal.hard_constraints:
        if item.field is GoalField.CATEGORY and not include_category:
            continue
        label = _GOAL_FIELD_LABELS.get(item.field, item.field.value)
        if item.field is GoalField.SPECIFICATION and item.attribute:
            label = item.attribute
        parts.append(f"{label} {item.value}")
    for item in goal.exclusions:
        label = _GOAL_FIELD_LABELS.get(item.field, item.field.value)
        parts.append(f"排除{label} {item.value}")
    return "、".join(parts[:4])


def _execution_failure_payload(
    execution: PlanExecutionResult,
    goal: ShoppingGoal,
) -> AiModelFallbackPayload:
    failed = next(
        (
            step
            for step in execution.steps
            if step.status
            in {
                StepExecutionStatus.FAILED,
                StepExecutionStatus.TIMED_OUT,
                StepExecutionStatus.CANCELLED,
            }
        ),
        None,
    )
    step_type = failed.step_type if failed is not None else "execution"
    step_label = _STEP_LABELS.get(step_type, "任务执行")
    error_code = (
        failed.error_code if failed is not None else execution.stop_reason.value
    )
    if failed is not None and (
        failed.status is StepExecutionStatus.TIMED_OUT or error_code == "step_timeout"
    ):
        cause = f"{step_label}超时"
    elif error_code == "no_candidate":
        cause = f"{step_label}没有返回可用候选"
    elif error_code == "product_reference_missing":
        cause = "缺少需要查询的商品"
    elif execution.stop_reason.value == "tool_denied":
        cause = f"{step_label}未通过工具权限校验"
    else:
        cause = f"{step_label}未成功完成"
    constraints = _goal_constraint_summary(goal)
    retained = f"已保留条件：{constraints}。" if constraints else ""
    answer = (
        f"{cause}，本轮没有生成未经核验的结果。{retained}"
        "你可以直接回复“重试”，我会沿用这些条件继续执行。"
    )
    return AiModelFallbackPayload(
        answer=answer,
        reason_code=f"plan_{execution.stop_reason.value}_{step_type}",
    )


def _required_fact_fallback(
    state: _TurnState,
    reason_codes: tuple[str, ...],
) -> AiModelFallbackPayload:
    labels: list[str] = []
    for code in reason_codes:
        field = code.split("_", maxsplit=1)[0]
        label = {
            "delivery": "履约信息",
            "price": "价格",
            "rating": "评分",
            "specification": "规格",
            "stock": "库存",
        }.get(field, "商品事实")
        if label not in labels:
            labels.append(label)
    missing = "、".join(labels) or "必要商品事实"
    constraints = _goal_constraint_summary(state.goal)
    retained = f"已保留条件：{constraints}。" if constraints else ""
    return AiModelFallbackPayload(
        answer=(
            f"商品事实刷新后仍缺少可核验的{missing}，暂时不能给出可靠结论。"
            f"{retained}你可以直接回复“重试”，我会按原条件重新读取商品事实。"
        ),
        reason_code="required_fact_unavailable",
    )


def _bounded_rag_query(query: str) -> str:
    return " ".join(query.split())[:2_000].rstrip()


def _fetch_reviews(
    base_url: str,
    item_ids: list[str],
    http_client: httpx.Client | None,
) -> tuple[ReviewCollection, ...]:
    if http_client is not None:
        return ReviewBatchClient(base_url, http_client).fetch(item_ids)
    with httpx.Client(timeout=10) as client:
        return ReviewBatchClient(base_url, client).fetch(item_ids)


def _rank_products(
    state: _TurnState,
    snapshot: ProductSnapshot,
    candidate_set: CandidateSet,
) -> RankingResult:
    registry = load_feature_profiles()
    products = snapshot.items_by_id
    normalized = {
        item.item_id: FeatureNormalizer(registry).normalize(item)
        for item in products.values()
    }
    return ProductRanker(
        load_ranking_policy(registry),
        registry,
    ).rank(
        candidate_set=candidate_set,
        products=products,
        normalized_features=normalized,
        goal=state.goal,
        trace_context=state.trace_context,
    )


def _product_ref(item_id: str, snapshot: ProductSnapshot) -> AiModelProductRef:
    product = snapshot.items_by_id[item_id]
    name = (
        str(product.name.value)
        if product.name.status is FactStatus.KNOWN and product.name.value is not None
        else item_id
    )
    return AiModelProductRef(
        item_id=item_id,
        item_name=name,
        url=build_product_url(item_id),
    )


def _ranking_evidence(
    ranking: RankingResult,
    item_id: str,
) -> EvidenceRef:
    assert ranking.snapshot_id is not None
    return EvidenceRef(
        evidence_id=f"rank:{ranking.snapshot_id}:{item_id}",
        source_type=EvidenceSourceType.RANKING_SCORE,
        source_id=f"{ranking.policy_version}:{item_id}",
        item_id=item_id,
        snapshot_id=ranking.snapshot_id,
        title=f"排序依据 {item_id}",
    )


def _is_review_summary(route: AImodelIntentRoute) -> bool:
    return (route.intent or "").strip().casefold() in {
        "product_review",
        "product_reviews",
        "review",
        "review_summary",
        "reviews",
    }


def _requested_review_topics(message: str) -> tuple[str, ...]:
    topics: list[str] = []
    for pattern in (
        r"(?:评论|评价)(?:中的|里的|中)?(?P<topic>[\u4e00-\u9fffA-Za-z0-9]{2,12})(?:反馈|表现|问题|情况)",
        r"只(?:补充|总结|看)(?P<topic>[\u4e00-\u9fffA-Za-z0-9]{2,12})(?:主题|评价|评论)",
    ):
        for match in re.finditer(pattern, message):
            topic = match.group("topic").strip()
            if topic and topic not in topics:
                topics.append(topic)
    for topic in ("便携性", "低分", "负面", "优点", "缺点"):
        if topic in message and topic not in topics:
            topics.append(topic)
    return tuple(topics[:6])


def _filter_requested_review_insights(
    insights: list[Any],
    requested_topics: tuple[str, ...],
) -> tuple[list[Any], tuple[str, ...]]:
    attribute_topics = tuple(
        topic
        for topic in requested_topics
        if topic not in {"低分", "负面", "优点", "缺点"}
    )
    if not attribute_topics:
        return insights, ()
    matched = [
        insight
        for insight in insights
        if any(
            topic.casefold() in insight.label.casefold()
            or insight.label.casefold() in topic.casefold()
            or topic.casefold() == insight.topic_code.casefold()
            for topic in attribute_topics
        )
    ]
    return matched, attribute_topics


def _review_summary_draft(state: _TurnState) -> GroundedResponseDraft:
    assert state.snapshot is not None
    service = ProductComparisonService(
        load_feature_profiles(),
        load_comparison_policy(),
    )
    reports = tuple(service.summarize_reviews(item) for item in state.reviews)
    state.requested_review_topics = _requested_review_topics(state.request.message)
    state.review_reports = reports
    state.evidence = tuple(
        evidence for report in reports for evidence in report.evidence
    )
    if not reports:
        return GroundedResponseDraft(
            payload=AiModelFallbackPayload(
                answer=(
                    "评论服务没有返回当前商品的可核验评论。你可以直接回复“重试”，"
                    "我会继续查询同一商品。"
                ),
                reason_code="review_unavailable",
            )
        )

    all_insights = [insight for report in reports for insight in report.insights]
    insights, attribute_topics = _filter_requested_review_insights(
        all_insights,
        state.requested_review_topics,
    )
    sample_count = sum(report.sample_count for report in reports)
    if insights:
        insight_text = "；".join(insight.summary for insight in insights[:6])
        answer = f"基于本次读取的 {sample_count} 条评论：{insight_text}。"
    elif attribute_topics and all_insights:
        answer = (
            f"本次读取了 {sample_count} 条评论，但没有形成关于"
            f"“{'、'.join(attribute_topics)}”的达到阈值主题，因此不扩展到无关评价。"
        )
    elif any(report.status is ReviewInsightStatus.LOW_SAMPLE for report in reports):
        answer = (
            f"本次只读取到 {sample_count} 条评论，低于形成稳定主题所需的样本量，"
            "暂不放大个别评价。"
        )
    elif all(report.status is ReviewInsightStatus.NO_REVIEWS for report in reports):
        topic_text = (
            f"“{'、'.join(state.requested_review_topics)}”"
            if state.requested_review_topics
            else "指定主题"
        )
        answer = f"当前商品没有可用于总结{topic_text}的评论，因此无法判断该主题。"
    else:
        answer = f"本次读取了 {sample_count} 条评论，但没有形成达到阈值的集中主题。"

    return GroundedResponseDraft(
        payload=AiModelAnswerPayload(
            answer=answer,
            products=[],
            evidence=[item.to_payload() for item in state.evidence],
        )
    )


def _has_decision_constraints(goal: ShoppingGoal) -> bool:
    return bool(goal.exclusions) or any(
        item.field is not GoalField.CATEGORY for item in goal.hard_constraints
    )


def _recommendation_reason(goal: ShoppingGoal, rank: int) -> str:
    constraints = _goal_constraint_summary(goal, include_category=False)
    if constraints:
        return f"满足已核验条件：{constraints}；通过硬约束过滤，排序第 {rank}。"
    return f"通过硬约束过滤，排序第 {rank}。"


def _recommendation_content(
    item_id: str,
    snapshot: ProductSnapshot,
    goal: ShoppingGoal,
    rank: int,
    ranking_evidence: EvidenceRef,
) -> tuple[str, tuple[EvidenceRef, ...], tuple[ResponseClaim, ...]]:
    product = snapshot.items_by_id[item_id]
    details: list[str] = []
    evidence = [ranking_evidence]
    claims = [
        ResponseClaim(
            claim_id=f"rank-{rank}",
            claim_type=ClaimType.RANK,
            item_id=item_id,
            value=rank,
            evidence_ids=(ranking_evidence.evidence_id,),
        )
    ]
    if (
        product.current_price.status is FactStatus.KNOWN
        and product.current_price.freshness.state is FreshnessState.FRESH
        and product.current_price.value is not None
        and product.currency.status is FactStatus.KNOWN
        and product.currency.value is not None
    ):
        price_evidence = EvidenceRef(
            evidence_id=f"fact:{snapshot.snapshot_id}:{item_id}:current_price",
            source_type=EvidenceSourceType.PRODUCT_FACT,
            source_id=f"{item_id}.current_price",
            item_id=item_id,
            snapshot_id=snapshot.snapshot_id,
            title=f"{product.name.value} 价格",
        )
        details.append(
            f"价格 {product.current_price.value} {product.currency.value}"
        )
        evidence.append(price_evidence)
        claims.append(
            ResponseClaim(
                claim_id=f"recommend-{rank}-price",
                claim_type=ClaimType.PRICE,
                item_id=item_id,
                value=product.current_price.value,
                evidence_ids=(price_evidence.evidence_id,),
            )
        )
    specs = product.specifications.value
    if (
        product.specifications.status is FactStatus.KNOWN
        and product.specifications.freshness.state is FreshnessState.FRESH
        and isinstance(specs, ProductSpecifications)
    ):
        summary = next((part.value for part in specs.values if part.key == "summary"), None)
        if summary:
            spec_evidence = EvidenceRef(
                evidence_id=f"fact:{snapshot.snapshot_id}:{item_id}:specifications.summary",
                source_type=EvidenceSourceType.PRODUCT_FACT,
                source_id=f"{item_id}.specifications.summary",
                item_id=item_id,
                snapshot_id=snapshot.snapshot_id,
                title=f"{product.name.value} 规格",
            )
            details.append(f"规格 {summary}")
            evidence.append(spec_evidence)
            claims.append(
                ResponseClaim(
                    claim_id=f"recommend-{rank}-spec",
                    claim_type=ClaimType.SPECIFICATION,
                    item_id=item_id,
                    field="summary",
                    value=summary,
                    evidence_ids=(spec_evidence.evidence_id,),
                )
            )
    details.append(_recommendation_reason(goal, rank))
    return "，".join(details), tuple(evidence), tuple(claims)


def _augment_comparison_core_facts(
    matrix: ComparisonMatrix,
    snapshot: ProductSnapshot,
) -> ComparisonMatrix:
    existing_keys = {row.feature_key for row in matrix.rows}
    evidence = list(matrix.evidence)
    rows: list[ComparisonRow] = []
    for feature_key, label in (("current_price", "价格"), ("spec", "规格")):
        if feature_key in existing_keys:
            continue
        cells: list[ComparisonCell] = []
        for column in matrix.columns:
            product = snapshot.items_by_id[column.item_id]
            fact = (
                product.current_price
                if feature_key == "current_price"
                else product.specifications
            )
            evidence_id = f"fact:{snapshot.snapshot_id}:{column.item_id}:{feature_key}"
            evidence.append(
                EvidenceRef(
                    evidence_id=evidence_id,
                    source_type=EvidenceSourceType.PRODUCT_FACT,
                    source_id=f"{column.item_id}.{feature_key}",
                    item_id=column.item_id,
                    snapshot_id=snapshot.snapshot_id,
                    title=f"{product.name.value} {label}",
                )
            )
            value: object | None = fact.value
            if feature_key == "spec" and isinstance(value, ProductSpecifications):
                value = next(
                    (
                        item.value
                        for item in value.values
                        if item.key.casefold() == "summary"
                    ),
                    None,
                )
            known = (
                fact.status is FactStatus.KNOWN
                and fact.freshness.state is FreshnessState.FRESH
                and value is not None
            )
            display_value = None
            if known and feature_key == "current_price":
                currency = (
                    str(product.currency.value)
                    if product.currency.status is FactStatus.KNOWN
                    and product.currency.value is not None
                    else ""
                )
                display_value = f"{value} {currency}".strip()
            elif known:
                display_value = str(value)
            cells.append(
                ComparisonCell(
                    item_id=column.item_id,
                    status=(
                        ComparisonCellStatus.KNOWN
                        if known
                        else ComparisonCellStatus.UNKNOWN
                    ),
                    normalized_value=value if known else None,
                    display_value=display_value,
                    source_version=fact.source.source_version,
                    freshness=fact.freshness.state,
                    evidence_id=evidence_id,
                )
            )
        rows.append(
            ComparisonRow(
                feature_key=feature_key,
                label=label,
                cells=tuple(cells),
            )
        )
    if not rows:
        return matrix
    return ComparisonMatrix.model_validate(
        {
            **matrix.model_dump(mode="python"),
            "rows": [
                *(row.model_dump(mode="python") for row in rows),
                *(row.model_dump(mode="python") for row in matrix.rows),
            ],
            "evidence": [item.model_dump(mode="python") for item in evidence],
        }
    )


def _comparison_answer_and_claims(
    matrix: ComparisonMatrix,
) -> tuple[str, tuple[ResponseClaim, ...]]:
    rows = {
        row.feature_key: row
        for row in matrix.rows
        if row.feature_key in {"current_price", "spec"}
    }
    parts: list[str] = []
    claims: list[ResponseClaim] = []
    for column_index, column in enumerate(matrix.columns):
        values: list[str] = []
        for feature_key in ("current_price", "spec"):
            row = rows.get(feature_key)
            if row is None:
                continue
            cell = row.cells[column_index]
            if (
                cell.status is not ComparisonCellStatus.KNOWN
                or cell.display_value is None
                or cell.evidence_id is None
            ):
                continue
            values.append(f"{row.label} {cell.display_value}")
            claims.append(
                ResponseClaim(
                    claim_id=f"compare-{column_index + 1}-{feature_key}",
                    claim_type=(
                        ClaimType.PRICE
                        if feature_key == "current_price"
                        else ClaimType.COMPARISON_CELL
                    ),
                    item_id=column.item_id,
                    field=(feature_key if feature_key != "current_price" else None),
                    value=(
                        cell.normalized_value
                        if feature_key == "current_price"
                        else cell.display_value
                    ),
                    evidence_ids=(cell.evidence_id,),
                )
            )
        parts.append(
            f"{column.item_name}：{'，'.join(values)}"
            if values
            else f"{column.item_name}：相关事实暂不可核验"
        )
    answer = "；".join(parts) + "。"
    price_row = rows.get("current_price")
    if price_row is not None and len(matrix.columns) > 1:
        prices = [cell.normalized_value for cell in price_row.cells]
        currencies = [
            cell.display_value.rsplit(" ", 1)[-1]
            if cell.display_value is not None else None
            for cell in price_row.cells
        ]
        if all(isinstance(price, (int, float, Decimal)) for price in prices) and len(set(currencies)) == 1:
            lowest = min(range(len(prices)), key=lambda index: Decimal(str(prices[index])))
            if len(set(prices)) > 1:
                answer += f"按已核验价格，{matrix.columns[lowest].item_name}更低。"
    return answer, tuple(claims)


def _compose_draft(
    state: _TurnState,
    task_type: PlanningTaskType,
    invocation: StepInvocation,
) -> GroundedResponseDraft:
    if task_type is PlanningTaskType.KNOWLEDGE:
        result = invocation.inputs["knowledge"].value
        if not isinstance(result, AiModelToolResult) or not result.ok:
            payload: AiModelResponsePayload = AiModelFallbackPayload(
                answer="当前知识证据不足，暂时无法给出可靠答复。",
                reason_code="knowledge_unavailable",
            )
        else:
            content = str(result.data.get("content") or "").strip()
            payload = (
                AiModelAnswerPayload(answer=content)
                if content
                else AiModelFallbackPayload(
                    answer="当前知识库没有找到足够依据。",
                    reason_code="knowledge_empty",
                )
            )
        return GroundedResponseDraft(payload=payload)

    if state.snapshot is None:
        return GroundedResponseDraft(
            payload=AiModelFallbackPayload(
                answer="未能取得本轮商品事实。",
                reason_code="snapshot_unavailable",
            )
        )
    if _is_review_summary(state.route):
        review_candidates = state.candidates or tuple(
            CandidateReference(
                item_id=item_id,
                sources=(CandidateSource.PAGE,),
            )
            for item_id in state.snapshot.requested_item_ids
        )
        state.candidate_set = apply_hard_filters(
            state.goal,
            state.snapshot,
            policy=CandidatePolicy(minimum_stock=0),
            candidates=review_candidates,
        )
        state.ranking = _rank_products(state, state.snapshot, state.candidate_set)
        return _review_summary_draft(state)
    if state.candidate_set is None:
        candidates = state.candidates or tuple(
            CandidateReference(
                item_id=item_id,
                sources=(CandidateSource.PAGE,),
            )
            for item_id in state.snapshot.requested_item_ids
        )
        state.candidate_set = apply_hard_filters(
            state.goal,
            state.snapshot,
            candidates=candidates,
        )
    if state.candidate_set.status is CandidateSetStatus.NO_CANDIDATE:
        mismatch_reasons = _confirmed_mismatch_reason_codes(state.candidate_set)
        if mismatch_reasons:
            return _known_mismatch_draft(state, mismatch_reasons)
        unavailable_reasons = _fact_refresh_reason_codes(state.candidate_set)
        if unavailable_reasons:
            return GroundedResponseDraft(
                payload=_required_fact_fallback(state, unavailable_reasons)
            )
        return GroundedResponseDraft(
            payload=AiModelFallbackPayload(
                answer="没有商品同时满足当前硬约束，请调整条件后再试。",
                reason_code="no_candidate",
            )
        )
    if state.ranking is None:
        state.ranking = _rank_products(state, state.snapshot, state.candidate_set)

    if task_type is PlanningTaskType.COMPARE:
        if state.comparison is None:
            raise StepExecutionError("comparison_missing")
        products = {
            item_id: _product_ref(item_id, state.snapshot)
            for item_id in state.snapshot.items_by_id
        }
        answer, claims = _comparison_answer_and_claims(state.comparison)
        return GroundedResponseDraft(
            payload=state.comparison.to_payload(
                answer=answer,
                products=products,
            ),
            claims=claims,
        )

    eligible_ids = [item.item_id for item in state.candidate_set.eligible]
    if task_type is PlanningTaskType.RECOMMEND or (
        task_type is PlanningTaskType.PRODUCT_DETAIL
        and _has_decision_constraints(state.goal)
    ):
        ranked = state.ranking.ranked[:3]
        reasons: list[AiModelRecommendationReason] = []
        evidence: list[EvidenceRef] = []
        claims: list[ResponseClaim] = []
        answer_lines: list[str] = []
        for item in ranked:
            ranking_evidence = _ranking_evidence(state.ranking, item.item_id)
            reason, item_evidence, item_claims = _recommendation_content(
                item.item_id,
                state.snapshot,
                state.goal,
                item.rank,
                ranking_evidence,
            )
            product = _product_ref(item.item_id, state.snapshot)
            reasons.append(
                AiModelRecommendationReason(
                    item_id=item.item_id,
                    reason=reason,
                    evidence_ids=[part.evidence_id for part in item_evidence],
                )
            )
            answer_lines.append(f"{item.rank}. {product.item_name}：{reason}")
            evidence.extend(item_evidence)
            claims.extend(item_claims)
        state.evidence = tuple(evidence)
        return GroundedResponseDraft(
            payload=AiModelRecommendationPayload(
                answer="\n".join(answer_lines),
                candidates=[
                    _product_ref(item.item_id, state.snapshot) for item in ranked
                ],
                recommendations=reasons,
                evidence=[item.to_payload() for item in evidence],
            ),
            claims=tuple(claims),
        )

    selected_ids = eligible_ids[:20]
    list_evidence: list[EvidenceRef] = []
    list_claims: list[ResponseClaim] = []
    answer_lines: list[str] = []
    for item_id in selected_ids:
        product = state.snapshot.items_by_id[item_id]
        name = _product_ref(item_id, state.snapshot).item_name
        price = product.current_price
        currency = product.currency
        if (
            price.status is FactStatus.KNOWN
            and price.freshness.state is FreshnessState.FRESH
            and price.value is not None
            and currency.status is FactStatus.KNOWN
            and currency.value is not None
        ):
            fact_evidence = EvidenceRef(
                evidence_id=f"fact:{state.snapshot.snapshot_id}:{item_id}:current_price",
                source_type=EvidenceSourceType.PRODUCT_FACT,
                source_id=f"{item_id}.current_price",
                item_id=item_id,
                snapshot_id=state.snapshot.snapshot_id,
                title=f"{name} 价格",
            )
            list_evidence.append(fact_evidence)
            list_claims.append(
                ResponseClaim(
                    claim_id=f"list-{item_id}-price",
                    claim_type=ClaimType.PRICE,
                    item_id=item_id,
                    value=price.value,
                    evidence_ids=(fact_evidence.evidence_id,),
                )
            )
            answer_lines.append(f"{name}：{price.value} {currency.value}")
        else:
            answer_lines.append(f"{name}：价格待核验")
    state.evidence = tuple(list_evidence)
    return GroundedResponseDraft(
        payload=AiModelProductListPayload(
            answer="；".join(answer_lines) + "。",
            products=[_product_ref(item_id, state.snapshot) for item_id in selected_ids],
            evidence=[item.to_payload() for item in list_evidence],
        ),
        claims=tuple(list_claims),
    )


def _build_evaluation_contexts(
    state: _TurnState,
    payload: AiModelResponsePayload,
) -> tuple[AgentEvaluationContext, ...]:
    """Project only evidence consumed by this turn into Kayn-ready contexts."""

    contexts: list[AgentEvaluationContext] = []
    for result in state.tool_results:
        if result.tool not in {"rag_tool", "search_shopping_guides"} or not result.ok:
            continue
        content = str(result.data.get("content") or "").strip()
        trace_id = str(result.data.get("trace_id") or "").strip()
        if content:
            contexts.append(
                AgentEvaluationContext(
                    content=content,
                    source_type="rag_final_context",
                    source_id=trace_id or "rag-context",
                    title="RAG final context",
                )
            )

    for report in state.review_reports:
        contexts.append(
            AgentEvaluationContext(
                content=json.dumps(
                    {
                        "item_id": report.item_id,
                        "status": report.status.value,
                        "sample_count": report.sample_count,
                        "reported_review_count": report.reported_review_count,
                        "requested_topics": list(state.requested_review_topics),
                        "insights": [
                            {
                                "topic": insight.topic_code,
                                "kind": insight.kind.value,
                                "summary": insight.summary,
                                "positive_count": insight.positive_count,
                                "negative_count": insight.negative_count,
                                "evidence_ids": list(insight.evidence_ids),
                            }
                            for insight in report.insights
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                source_type="review_summary",
                source_id=f"{report.policy_version}:{report.item_id}",
                title=f"Review summary {report.item_id}",
            )
        )

    snapshot = state.snapshot
    if snapshot is None:
        return tuple(contexts)
    selected_ids = {
        str(product.item_id)
        for product in payload.recommended_products()
        if str(product.item_id) in snapshot.items_by_id
    }
    for item_id in snapshot.requested_item_ids:
        if item_id not in selected_ids:
            continue
        item = snapshot.items_by_id[item_id]
        facts = {
            name: _evaluation_fact(getattr(item, name))
            for name in (
                "name",
                "category",
                "brand",
                "current_price",
                "currency",
                "stock",
                "specifications",
                "rating",
                "review_count",
                "delivery",
            )
        }
        contexts.append(
            AgentEvaluationContext(
                content=json.dumps(
                    {
                        "item_id": item_id,
                        "snapshot_id": snapshot.snapshot_id,
                        "source_version": snapshot.source_version,
                        "facts": facts,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                source_type="product_snapshot",
                source_id=f"{snapshot.snapshot_id}:{item_id}",
                title=f"Product facts {item_id}",
            )
        )
    return tuple(contexts)


def _evaluation_fact(fact: Any) -> dict[str, Any]:
    value = fact.value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
    elif value is not None and not isinstance(value, (str, int, float, bool, dict, list)):
        value = str(value)
    return {
        "status": fact.status.value,
        "value": value,
        "source_version": fact.source.source_version,
        "freshness": fact.freshness.state.value,
    }


def run_shopping_agent_main_chain(
    request: AiModelChatRequest,
    *,
    conversation_id: int,
    goal: ShoppingGoal,
    clarification: ClarificationDecision | None,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
    trace_context: AgentTraceContext | None = None,
    cancellation: ExecutionCancellation | None = None,
) -> AgentRuntimeResult | None:
    """Construct the default runtime and execute one request."""

    return ShoppingAgentRuntime().run(
        request,
        conversation_id=conversation_id,
        goal=goal,
        clarification=clarification,
        mock_api_url=mock_api_url,
        http_client=http_client,
        trace_context=trace_context,
        cancellation=cancellation,
    )
