"""Production wiring for the bounded shopping-agent execution chain.

This module binds the D-stage contracts to the existing product and RAG
services.  The planner remains declarative, every external read is authorized
immediately before execution, and factual product responses pass through the
grounding verifier before they leave the service boundary.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from .agent_trace import AgentTraceContext, record_intent_route
from .candidate_service import (
    CandidateReference,
    CandidateSet,
    CandidateSetStatus,
    CandidateSource,
    apply_hard_filters,
)
from .clarification import ClarificationDecision, ClarificationReason
from .comparison import (
    ComparisonMatrix,
    EvidenceRef,
    EvidenceSourceType,
    ProductComparisonService,
    ReviewBatchClient,
    ReviewCollection,
    load_comparison_policy,
)
from .feature_normalizer import FeatureNormalizer, load_feature_profiles
from .intent_router import AImodelIntentRoute, load_default_aimodel_intent_router
from .plan_models import AgentPlanValidator, PlanValueType, StepType, load_plan_policy
from .planner import HierarchicalPlanner, PlanningResult, PlanningTaskType
from .product_models import FactStatus, ProductSnapshot
from .product_snapshot import ProductSnapshotClient
from .ranking import ProductRanker, RankingResult, load_ranking_policy
from .schemas import (
    AiModelAnswerPayload,
    AiModelChatRequest,
    AiModelFallbackPayload,
    AiModelPageContext,
    AiModelProductListPayload,
    AiModelProductRef,
    AiModelRecommendationPayload,
    AiModelRecommendationReason,
    AiModelResponsePayload,
    AiModelToolResult,
)
from .shopping_goal import ShoppingGoal
from .tool_executor import (
    BoundedParallelToolExecutor,
    ExecutionValue,
    PlanExecutionResult,
    PlanExecutionStatus,
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
class AgentRuntimeResult:
    """One fully executed main-chain result returned to the API layer."""

    payload: AiModelResponsePayload
    planning: PlanningResult
    execution: PlanExecutionResult
    tool_results: tuple[AiModelToolResult, ...] = ()


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
    tool_results: list[AiModelToolResult] = field(default_factory=list)
    candidates: tuple[CandidateReference, ...] = ()
    snapshot: ProductSnapshot | None = None
    candidate_set: CandidateSet | None = None
    ranking: RankingResult | None = None
    comparison: ComparisonMatrix | None = None
    reviews: tuple[ReviewCollection, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    canonical_draft: GroundedResponseDraft | None = None


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
    ) -> AgentRuntimeResult | None:
        """Return ``None`` when the route belongs to a legacy-only capability."""

        route, route_candidates = (
            load_default_aimodel_intent_router().route_with_candidates(request.message)
        )
        if trace_context is not None:
            record_intent_route(
                trace_context,
                route,
                candidates=route_candidates,
            )
        decision = clarification or _proceed_decision()
        page_context = _effective_page_context(request)
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
            payload: AiModelResponsePayload = AiModelFallbackPayload(
                answer="本轮任务未能安全完成，请稍后重试或缩小商品范围。",
                reason_code=f"plan_{execution.stop_reason.value}",
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

        async def search(_: StepInvocation) -> ExecutionValue:
            result = await asyncio.to_thread(
                search_products,
                _bounded_query(state.request.message),
                mock_api_url=state.mock_api_url,
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
                    query=_bounded_query(state.request.message),
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
            candidates = _candidate_refs(invocation.inputs["candidate_refs"].value)
            item_ids = [candidate.item_id for candidate in candidates[:5]]
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
            candidates = _candidate_refs(invocation.inputs["candidate_refs"].value)
            return AgentToolCall(
                tool_name=AgentToolName.PRODUCT_REVIEWS.value,
                arguments=_scope_arguments(
                    state,
                    item_ids=tuple(item.item_id for item in candidates[:5]),
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
                candidates=candidates,
                search_query=_bounded_query(state.request.message),
            )
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
    if search_result.ok:
        for item in search_result.data.get("items", []):
            if isinstance(item, dict) and item.get("item_id") is not None:
                add(item["item_id"], CandidateSource.SEARCH)
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


def _scope_arguments(state: _TurnState, **arguments: Any) -> dict[str, Any]:
    return {
        "user_id": state.request.user_id,
        "conversation_id": state.conversation_id,
        **arguments,
    }


def _bounded_query(query: str) -> str:
    return " ".join(query.split())[:512].rstrip()


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
        return GroundedResponseDraft(
            payload=state.comparison.to_payload(
                answer="已按同一事实快照生成商品对比。",
                products=products,
            )
        )

    eligible_ids = [item.item_id for item in state.candidate_set.eligible]
    if task_type is PlanningTaskType.RECOMMEND:
        ranked = state.ranking.ranked[:3]
        evidence = tuple(_ranking_evidence(state.ranking, item.item_id) for item in ranked)
        evidence_by_item = {item.item_id: item for item in evidence}
        state.evidence = evidence
        return GroundedResponseDraft(
            payload=AiModelRecommendationPayload(
                answer="以下商品通过了硬约束过滤，并按确定性规则排序。",
                candidates=[
                    _product_ref(item.item_id, state.snapshot) for item in ranked
                ],
                recommendations=[
                    AiModelRecommendationReason(
                        item_id=item.item_id,
                        reason=f"通过硬约束过滤，排序第 {item.rank}。",
                        evidence_ids=[evidence_by_item[item.item_id].evidence_id],
                    )
                    for item in ranked
                ],
                evidence=[item.to_payload() for item in evidence],
            ),
            claims=tuple(
                ResponseClaim(
                    claim_id=f"rank-{index}",
                    claim_type=ClaimType.RANK,
                    item_id=item.item_id,
                    value=item.rank,
                    evidence_ids=(evidence_by_item[item.item_id].evidence_id,),
                )
                for index, item in enumerate(ranked, start=1)
            ),
        )

    selected_ids = eligible_ids[:20]
    return GroundedResponseDraft(
        payload=AiModelProductListPayload(
            answer=(
                "已读取当前商品的可验证信息。"
                if task_type is PlanningTaskType.PRODUCT_DETAIL
                else "已找到符合当前硬约束的商品。"
            ),
            products=[_product_ref(item_id, state.snapshot) for item_id in selected_ids],
        )
    )


def run_shopping_agent_main_chain(
    request: AiModelChatRequest,
    *,
    conversation_id: int,
    goal: ShoppingGoal,
    clarification: ClarificationDecision | None,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
    trace_context: AgentTraceContext | None = None,
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
    )
