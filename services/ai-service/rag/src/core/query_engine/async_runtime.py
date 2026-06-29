"""Run the online RAG query pipeline through an async orchestration boundary.

``AsyncQueryRuntime`` is the Phase I3 counterpart to the existing synchronous
``QueryRuntime`` in ``src.scripts.query``. It preserves the same public response
shape, trace stages, and debug snapshots while exposing an awaitable ``execute``
method for MCP, CLI, and evaluation callers. I3 intentionally keeps the first
implementation single-collection compatible; I4 will replace the collection
retrieval internals with true multi-collection concurrency.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from src.core.config import RAG_ROOT, RagSettings
from src.core.query_engine.dense_route import DenseRoute
from src.core.query_engine.hybrid_engine import HybridSearch
from src.core.query_engine.intent_router import (
    IntentRoute,
    IntentRouter,
    load_collection_profiles,
    load_intent_rules,
)
from src.core.query_engine.query_processor import ProcessedQuery, QueryProcessor
from src.core.query_engine.reranker import RerankController, RerankOutcome
from src.core.query_engine.self_rag_controller import SelfRagController, SelfRagDecision
from src.core.query_engine.sparse_route import SparseRoute
from src.core.query_engine.trace_snapshots import candidate_snapshots
from src.core.response import (
    EvidenceContextOptimizer,
    KnowledgeHubResponse,
    KnowledgeHubResponseBuilder,
    MultimodalAssembler,
)
from src.core.trace import TraceContext, TraceController
from src.core.trace.trace_controller import TraceSink
from src.core.types import RetrievalResult
from src.libs.embedding import EmbeddingFactory
from src.libs.llm import LLMFactory
from src.libs.reranker import RerankerFactory
from src.libs.vector_store import VectorStoreFactory
from src.storage.bm25_storage import BM25Storage
from src.storage.image_storage import ImageStorage
from src.storage.postgres import PostgresPool
from src.storage.repositories import CollectionProfileRepository, TraceRepository
from src.storage.trace_log_storage import build_trace_writer


@dataclass(frozen=True, slots=True)
class AsyncQueryExecutionResult:
    """Capture public output and trace-safe stage snapshots for one async query.

    The field names intentionally match ``QueryExecutionResult`` so CLI verbose
    rendering and later MCP/evaluation callers can consume either runtime with
    duck typing instead of branching on a concrete result class.
    """

    processed_query: ProcessedQuery
    intent_route: IntentRoute
    dense_results: tuple[RetrievalResult, ...]
    sparse_results: tuple[RetrievalResult, ...]
    fused_results: tuple[RetrievalResult, ...]
    filtered_results: tuple[RetrievalResult, ...]
    final_results: tuple[RetrievalResult, ...]
    self_rag_decision: SelfRagDecision | None
    response: KnowledgeHubResponse
    rerank_applied: bool
    fallback_used: bool


class AsyncQueryRuntime:
    """Execute the configured online query path through async stage boundaries."""

    def __init__(
        self,
        *,
        query_processor: QueryProcessor,
        hybrid_search: HybridSearch,
        rerank_controller: RerankController | None,
        response_builder: KnowledgeHubResponseBuilder,
        self_rag_controller: SelfRagController | None = None,
        intent_router: IntentRouter | None = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        """Configure already-created async-query collaborators.

        Args:
            query_processor: User-query normalization component.
            hybrid_search: Single-collection Dense/Sparse/RRF/filter boundary.
            rerank_controller: Optional rerank/fallback controller.
            response_builder: Public response assembler.
            self_rag_controller: Optional post-rerank evidence gate.
            intent_router: Optional collection router executed after query
                preprocessing.
            trace_sink: Optional finished-trace sink.
        """

        self._query_processor = query_processor
        self._intent_router = intent_router
        self._hybrid_search = hybrid_search
        self._rerank_controller = rerank_controller
        self._response_builder = response_builder
        self._self_rag_controller = self_rag_controller
        self._trace_sink = trace_sink

    async def execute(
        self,
        query: str,
        *,
        collection: str,
        top_k: int,
        no_rerank: bool,
        trace_id: str,
        request_source: str = "query_cli",
    ) -> AsyncQueryExecutionResult:
        """Run the complete async query path and return public/debug projections.

        Args:
            query: Raw user question.
            collection: Collection selected by caller or settings.
            top_k: Positive final result limit.
            no_rerank: Explicit caller request to bypass reranking.
            trace_id: Stable query identifier included in every citation.
            request_source: Calling surface written to query trace metadata.

        Returns:
            Public response plus immutable stage snapshots matching the sync
            runtime contract.

        Side Effects:
            Calls configured providers/storage and flushes one query trace when
            a sink is configured.
        """

        trace_context = TraceContext.query(
            trace_id=trace_id,
            collection=collection,
            raw_query=query,
            request_source=request_source,
        )
        trace_controller = TraceController(trace_context, sink=self._trace_sink)
        hybrid = None
        final_results: list[RetrievalResult] = []
        self_rag_decision: SelfRagDecision | None = None
        rerank_applied = False
        fallback_used = False
        try:
            processed = await self._process_query(
                query=query,
                collection=collection,
                top_k=top_k,
                trace_controller=trace_controller,
            )
            intent_route = await self._route_intent(
                query=query,
                processed=processed,
                trace_controller=trace_controller,
            )
            if intent_route.collection != processed.collection:
                processed = processed.model_copy(update={"collection": intent_route.collection})
                trace_controller.context.collection = intent_route.collection

            hybrid = await self._search_single_collection(
                processed,
                trace_controller=trace_controller,
            )
            rerank_applied = not no_rerank and self._rerank_controller is not None
            rerank_fallback = False
            if rerank_applied:
                outcome = await self._rerank(
                    processed.normalized_query,
                    hybrid.results,
                    top_k=top_k,
                    trace_controller=trace_controller,
                )
                final_results = outcome.results
                rerank_fallback = outcome.fallback_used
            else:
                final_results = [
                    candidate.model_copy(deep=True)
                    for candidate in hybrid.results[:top_k]
                ]
                trace_controller.record_stage(
                    "rerank",
                    duration_ms=0,
                    method="rerank_or_fallback",
                    provider="none",
                    candidate_count=len(final_results),
                    status="skipped",
                    details={
                        "top_k": top_k,
                        "reason": "disabled_by_request"
                        if no_rerank
                        else "reranker_unavailable",
                        "before_candidates": candidate_snapshots(hybrid.results),
                        "after_candidates": candidate_snapshots(final_results),
                    },
                )

            if (
                self._self_rag_controller is not None
                and rerank_applied
                and not rerank_fallback
            ):
                self_rag_decision = await self._apply_self_rag(
                    processed.normalized_query,
                    final_results,
                    trace_controller=trace_controller,
                )
                final_results = self_rag_decision.selected_results
            elif self._self_rag_controller is not None:
                trace_controller.record_stage(
                    "self_rag",
                    duration_ms=0,
                    method="score_gate_or_llm_judge",
                    provider="SelfRagController",
                    candidate_count=len(final_results),
                    status="skipped",
                    details={
                        "reason": "rerank_scores_unavailable",
                        "selected_chunk_ids": [
                            candidate.chunk_id for candidate in final_results
                        ],
                    },
                )

            response = await self._build_response(
                final_results,
                trace_id=trace_id,
                query=processed.normalized_query,
                trace_controller=trace_controller,
            )
            self_rag_fallback = (
                self_rag_decision is not None
                and self_rag_decision.fallback_action is not None
            )
            fallback_used = hybrid.fallback_used or rerank_fallback or self_rag_fallback
            trace_controller.flush_query(
                status="degraded" if fallback_used else "success",
                query_result=_query_result_snapshot(response, final_results),
                top_score=_top_score(final_results),
                candidate_count_by_stage=_candidate_counts(
                    dense_results=hybrid.dense_results,
                    sparse_results=hybrid.sparse_results,
                    fused_results=hybrid.fused_results,
                    filtered_results=hybrid.results,
                    rerank_results=outcome.results if rerank_applied else final_results,
                    self_rag_results=final_results if self_rag_decision is not None else None,
                    final_results=final_results,
                ),
                fallback_used=fallback_used,
                empty_result=response.is_empty,
            )
            return AsyncQueryExecutionResult(
                processed_query=processed,
                intent_route=intent_route,
                dense_results=tuple(hybrid.dense_results),
                sparse_results=tuple(hybrid.sparse_results),
                fused_results=tuple(hybrid.fused_results),
                filtered_results=tuple(hybrid.results),
                final_results=tuple(final_results),
                self_rag_decision=self_rag_decision,
                response=response,
                rerank_applied=rerank_applied,
                fallback_used=fallback_used,
            )
        except Exception as error:
            if trace_controller.context.finished_at is None:
                trace_controller.flush_query(
                    status="failed",
                    query_result=_empty_query_result(),
                    top_score=_top_score(final_results),
                    candidate_count_by_stage=_candidate_counts(
                        dense_results=hybrid.dense_results if hybrid else [],
                        sparse_results=hybrid.sparse_results if hybrid else [],
                        fused_results=hybrid.fused_results if hybrid else [],
                        filtered_results=hybrid.results if hybrid else [],
                        rerank_results=final_results,
                        self_rag_results=final_results
                        if self_rag_decision is not None
                        else None,
                        final_results=final_results,
                    ),
                    fallback_used=fallback_used,
                    error={
                        "error_type": type(error).__name__,
                        "message": str(error),
                    },
                    empty_result=not final_results,
                )
            raise

    async def _process_query(
        self,
        *,
        query: str,
        collection: str,
        top_k: int,
        trace_controller: TraceController,
    ) -> ProcessedQuery:
        """Normalize, rewrite, tokenize, and trace one query-processing stage."""

        started = perf_counter()
        try:
            processed = await asyncio.to_thread(
                self._query_processor.process,
                query,
                collection=collection,
                top_k=top_k,
            )
        except Exception as error:
            trace_controller.record_stage(
                "query_processing",
                duration_ms=(perf_counter() - started) * 1000,
                input_summary={"raw_query": query},
                output_summary={},
                method="normalize_rewrite_tokenize",
                provider=type(self._query_processor).__name__,
                status="failed",
                error={"error_type": type(error).__name__, "message": str(error)},
            )
            raise
        trace_controller.record_stage(
            "query_processing",
            duration_ms=(perf_counter() - started) * 1000,
            input_summary={"raw_query": query},
            output_summary={
                "normalized_query": processed.normalized_query,
                "keywords": list(processed.keywords),
                "collection": processed.collection,
                "top_k": processed.top_k,
                "rewrite_applied": processed.rewrite_applied,
            },
            method="normalize_rewrite_tokenize",
            provider=type(self._query_processor).__name__,
        )
        return processed

    async def _route_intent(
        self,
        *,
        query: str,
        processed: ProcessedQuery,
        trace_controller: TraceController,
    ) -> IntentRoute:
        """Route the processed query and record the compatible trace stage."""

        started = perf_counter()
        if self._intent_router is not None:
            intent_route = await asyncio.to_thread(self._intent_router.route, query, processed)
            route_status = "success"
            route_details = intent_route.to_trace_details()
        else:
            intent_route = IntentRoute(
                collection=processed.collection,
                collections=(processed.collection,),
                domain_intent="default",
                complexity="simple",
                retrieval_strategy="hybrid",
                confidence=0.0,
                method="disabled",
                provider="IntentRouter",
                reason="intent_router_disabled",
            )
            route_status = "skipped"
            route_details = intent_route.to_trace_details()
        trace_controller.record_stage(
            "intent_routing",
            duration_ms=(perf_counter() - started) * 1000,
            input_summary={
                "normalized_query": processed.normalized_query,
                "keyword_count": len(processed.keywords),
            },
            output_summary={
                "collection": intent_route.collection,
                "collections": list(intent_route.collections),
                "domain_intent": intent_route.domain_intent,
                "retrieval_strategy": intent_route.retrieval_strategy,
                "confidence": intent_route.confidence,
            },
            method=intent_route.method,
            provider=intent_route.provider or "IntentRouter",
            status=route_status,
            details=route_details,
        )
        return intent_route

    async def _search_single_collection(
        self,
        query: ProcessedQuery,
        *,
        trace_controller: TraceController,
    ) -> Any:
        """Run one collection's hybrid retrieval boundary asynchronously."""

        return await asyncio.to_thread(
            self._hybrid_search.search,
            query,
            filters={"collection": query.collection},
            trace_context=trace_controller.context,
        )

    async def _rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int,
        trace_controller: TraceController,
    ) -> RerankOutcome:
        """Run rerank orchestration without blocking async callers."""

        if self._rerank_controller is None:
            raise RuntimeError("rerank controller is unavailable")
        return await asyncio.to_thread(
            self._rerank_controller.rerank_with_outcome,
            query,
            candidates,
            top_k=top_k,
            trace_context=trace_controller.context,
        )

    async def _apply_self_rag(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        trace_controller: TraceController,
    ) -> SelfRagDecision:
        """Run Self-RAG gating after rerank and before response assembly."""

        if self._self_rag_controller is None:
            raise RuntimeError("self-rag controller is unavailable")
        return await asyncio.to_thread(
            self._self_rag_controller.evaluate,
            query,
            candidates,
            trace_context=trace_controller.context,
        )

    async def _build_response(
        self,
        candidates: Sequence[RetrievalResult],
        *,
        trace_id: str,
        query: str,
        trace_controller: TraceController,
    ) -> KnowledgeHubResponse:
        """Build the public response and record the response trace stage."""

        started = perf_counter()
        response = await asyncio.to_thread(
            self._response_builder.build,
            candidates,
            trace_id=trace_id,
            query=query,
        )
        trace_controller.record_stage(
            "response",
            duration_ms=(perf_counter() - started) * 1000,
            input_summary={"final_result_count": len(candidates)},
            output_summary={
                "citation_count": len(response.citations),
                "image_count": len(response.images),
                "is_empty": response.is_empty,
            },
            method="citation_multimodal_build",
            provider=type(self._response_builder).__name__,
            candidate_count=len(candidates),
        )
        return response


def build_async_query_runtime(
    settings: RagSettings,
    pool: PostgresPool,
    no_rerank: bool = False,
) -> AsyncQueryRuntime:
    """Compose the production async query pipeline from settings.

    Args:
        settings: Validated runtime settings.
        pool: Open PostgreSQL pool shared by storage adapters.
        no_rerank: When true, do not construct reranker dependencies.

    Returns:
        A complete ``AsyncQueryRuntime`` ready for awaitable execution.
    """

    query_processor = QueryProcessor(settings=settings)
    embedding = EmbeddingFactory.create(settings=settings)
    vector_store = VectorStoreFactory.create(settings=settings, pool=pool)
    hybrid_search = HybridSearch(
        settings=settings,
        dense_route=DenseRoute(
            settings=settings,
            query_processor=query_processor,
            embedding=embedding,
            vector_store=vector_store,
        ),
        sparse_route=SparseRoute(
            settings=settings,
            query_processor=query_processor,
            bm25_indexer=BM25Storage(pool),
            vector_store=vector_store,
        ),
    )
    rerank_controller: RerankController | None = None
    self_rag_controller: SelfRagController | None = None
    if settings.rerank.enabled and not no_rerank:
        reranker_options: dict[str, Any] = {}
        provider_settings = settings.rerank.providers.get(settings.rerank.default)
        llm_provider = (
            getattr(provider_settings, "llm_provider", None)
            if provider_settings is not None
            else None
        )
        if isinstance(llm_provider, str) and llm_provider.strip():
            reranker_options["llm_client"] = LLMFactory.create(
                settings=settings,
                provider=llm_provider,
            )
        rerank_controller = RerankController(
            settings=settings,
            reranker=RerankerFactory.create(settings=settings, **reranker_options),
        )
        self_rag_settings = getattr(settings, "self_rag", None)
        if self_rag_settings is not None and self_rag_settings.enabled:
            self_rag_controller = SelfRagController(
                settings=settings,
                llm_client=LLMFactory.create(
                    settings=settings,
                    provider=self_rag_settings.judge_llm_provider,
                ),
            )

    optimizer_settings = settings.response.evidence_context_optimizer
    evidence_context_optimizer = None
    if optimizer_settings.enabled:
        evidence_context_optimizer = EvidenceContextOptimizer(
            llm_client=LLMFactory.create(
                settings=settings,
                provider=optimizer_settings.llm_provider,
            ),
            prompt_path=optimizer_settings.prompt_path,
        )

    return AsyncQueryRuntime(
        query_processor=query_processor,
        hybrid_search=hybrid_search,
        intent_router=_build_intent_router(settings, embedding, pool),
        rerank_controller=rerank_controller,
        self_rag_controller=self_rag_controller,
        response_builder=KnowledgeHubResponseBuilder(
            multimodal_assembler=MultimodalAssembler(
                resolver=ImageStorage(
                    pool,
                    root_dir=_resolve_runtime_path(settings.ingestion.image_dir),
                )
            ),
            evidence_context_optimizer=evidence_context_optimizer,
            fallback_to_raw_content=optimizer_settings.fallback_to_raw,
        ),
        trace_sink=_trace_sink_from_settings(settings, pool),
    )


def _build_intent_router(
    settings: RagSettings,
    embedding: Any,
    pool: PostgresPool,
) -> IntentRouter | None:
    """Create the optional intent router for async query execution."""

    router_settings = getattr(settings, "intent_router", None)
    if router_settings is None or not router_settings.enabled:
        return None
    return IntentRouter(
        rules=load_intent_rules(_resolve_runtime_path(router_settings.rules_path)),
        profiles=load_collection_profiles(
            _resolve_runtime_path(router_settings.collection_profiles_path)
        ),
        embedding_client=embedding,
        profile_repository=CollectionProfileRepository(pool),
        default_collection=settings.retrieval.filters.default_collection,
        rule_threshold=router_settings.rule_threshold,
        semantic_threshold=router_settings.semantic_threshold,
    )


def _trace_sink_from_settings(settings: RagSettings, pool: PostgresPool) -> TraceSink | None:
    """Create the configured async-query trace sink when available."""

    observability = getattr(settings, "observability", None)
    trace_path = getattr(observability, "trace_jsonl_path", None)
    persist_to_postgresql = bool(getattr(observability, "persist_to_postgresql", False))
    return build_trace_writer(
        jsonl_path=_resolve_runtime_path(trace_path) if trace_path else None,
        repository=TraceRepository(pool) if persist_to_postgresql else None,
    )


def _resolve_runtime_path(path: str | Path) -> Path:
    """Resolve settings paths independently of the launching shell directory."""

    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (RAG_ROOT / candidate).resolve()


def _query_result_snapshot(
    response: KnowledgeHubResponse,
    results: Sequence[RetrievalResult],
) -> dict[str, Any]:
    """Build the compact public result snapshot persisted with query traces."""

    return {
        "contexts": [
            {"chunk_id": result.chunk_id, "score": result.score, "rank": rank}
            for rank, result in enumerate(results, start=1)
        ],
        "content": response.content,
        "citations": [
            {
                "document_id": citation.document_id,
                "chunk_id": citation.chunk_id,
                "title": citation.title,
                "section_path": list(citation.section_path),
                "score": citation.score,
                "trace_id": citation.trace_id,
            }
            for citation in response.citations
        ],
        "images": [
            {
                "image_id": image.image_id,
                "chunk_ids": list(image.chunk_ids),
                "quality_status": image.quality_status,
            }
            for image in response.images
        ],
    }


def _empty_query_result() -> dict[str, Any]:
    """Return a valid empty result snapshot for failed async queries."""

    return {"contexts": [], "content": "", "citations": [], "images": []}


def _top_score(results: Sequence[RetrievalResult]) -> float | None:
    """Return the first final result's score for compact summary reporting."""

    return results[0].score if results else None


def _candidate_counts(
    *,
    dense_results: Sequence[RetrievalResult],
    sparse_results: Sequence[RetrievalResult],
    fused_results: Sequence[RetrievalResult],
    filtered_results: Sequence[RetrievalResult],
    rerank_results: Sequence[RetrievalResult],
    self_rag_results: Sequence[RetrievalResult] | None,
    final_results: Sequence[RetrievalResult],
) -> dict[str, int]:
    """Build candidate count summary aligned with the query trace contract."""

    counts = {
        "dense": len(dense_results),
        "sparse": len(sparse_results),
        "fusion": len(fused_results),
        "filter": len(filtered_results),
        "rerank": len(rerank_results),
    }
    if self_rag_results is not None:
        counts["self_rag"] = len(self_rag_results)
    return counts
