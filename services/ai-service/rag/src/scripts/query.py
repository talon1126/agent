"""Run the complete local hybrid retrieval pipeline from the command line.

This module is the Phase D operator and developer entry point for online RAG
queries. It loads validated settings, opens PostgreSQL once, composes
QueryProcessor, Dense Route, persisted BM25 Sparse Route, RRF fusion,
collection filtering, optional reranking, citation construction, and
multimodal response assembly, then prints one JSON document.

The script exposes trace-safe summaries under ``--verbose``. It never serializes
raw retrieval metadata, vectors, provider responses, Prompt content, or internal
tool payloads. It also does not generate a final shopping answer; callers receive
ranked knowledge evidence for Agent summarization.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from dotenv import find_dotenv, load_dotenv

from src.core.config import (
    RAG_ROOT,
    DatabaseSettings,
    RagSettings,
    load_settings,
)
from src.core.query_engine import (
    DenseRoute,
    HybridSearch,
    IntentRoute,
    IntentRouter,
    ProcessedQuery,
    QueryProcessor,
    RerankController,
    SelfRagController,
    SelfRagDecision,
    SparseRoute,
    load_collection_profiles,
    load_intent_rules,
)
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
from src.storage.postgres import PostgresPool, init_schema
from src.storage.repositories import CollectionProfileRepository, TraceRepository
from src.storage.trace_log_storage import build_trace_writer

SettingsLoader = Callable[[], RagSettings]
PoolFactory = Callable[[DatabaseSettings], PostgresPool]
SchemaInitializer = Callable[[PostgresPool], None]
RuntimeBuilder = Callable[[RagSettings, PostgresPool, bool], "QueryRuntime"]
TraceIdFactory = Callable[[], str]
MessageWriter = Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class QueryExecutionResult:
    """Capture public output and trace-safe stage snapshots for one CLI query.

    Attributes:
        processed_query: Immutable normalized query produced before retrieval.
        intent_route: Collection-routing decision applied before retrieval.
        dense_results: Dense candidates in provider order.
        sparse_results: Sparse candidates in BM25 order.
        fused_results: RRF candidates before metadata filtering.
        filtered_results: Candidates allowed to enter reranking.
        final_results: Self-RAG-gated final candidates.
        self_rag_decision: Optional Self-RAG gate decision applied after rerank.
        response: Public knowledge response containing no internal metadata.
        rerank_applied: Whether a configured reranker stage was attempted.
        fallback_used: Whether route or reranker degradation preserved results.
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


class QueryRuntime:
    """Execute configured query stages without owning process resources."""

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
        """Configure the already-created query pipeline components.

        Args:
            query_processor: User-query normalization component.
            intent_router: Optional collection router executed after query preprocessing.
            hybrid_search: Dense, Sparse, RRF, and metadata-filter orchestration.
            rerank_controller: Optional rerank/fallback controller. ``None``
                means reranking is disabled by configuration.
            response_builder: Public evidence, citation, and image assembler.
            self_rag_controller: Optional post-rerank evidence gate. ``None``
                means Self-RAG is disabled or rerank scores are unavailable.
            trace_sink: Optional trace sink receiving one finished query
                snapshot. Production can inject JSON Lines logging while tests
                pass a list appender.
        """

        self._query_processor = query_processor
        self._intent_router = intent_router
        self._hybrid_search = hybrid_search
        self._rerank_controller = rerank_controller
        self._response_builder = response_builder
        self._self_rag_controller = self_rag_controller
        self._trace_sink = trace_sink

    def execute(
        self,
        query: str,
        *,
        collection: str,
        top_k: int,
        no_rerank: bool,
        trace_id: str,
        request_source: str = "query_cli",
    ) -> QueryExecutionResult:
        """Run the complete query path and return public/debug projections.

        Args:
            query: Raw user question.
            collection: Collection selected by CLI override or settings.
            top_k: Positive final result limit.
            no_rerank: Explicit caller request to bypass reranking.
            trace_id: Stable query identifier included in every citation.
            request_source: Calling surface written to query trace metadata.

        Returns:
            Public response plus immutable stage snapshots used by verbose CLI
            output and later integration tests.

        Side Effects:
            Calls embedding, PostgreSQL vector/BM25/image storage, and possibly
            the configured reranker provider.
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
            processing_started = perf_counter()
            try:
                processed = self._query_processor.process(
                    query,
                    collection=collection,
                    top_k=top_k,
                )
            except Exception as error:
                trace_controller.record_stage(
                    "query_processing",
                    duration_ms=(perf_counter() - processing_started) * 1000,
                    input_summary={"raw_query": query},
                    output_summary={},
                    method="normalize_rewrite_tokenize",
                    provider=type(self._query_processor).__name__,
                    status="failed",
                    error={
                        "error_type": type(error).__name__,
                        "message": str(error),
                    },
                )
                raise
            trace_controller.record_stage(
                "query_processing",
                duration_ms=(perf_counter() - processing_started) * 1000,
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
            route_started = perf_counter()
            if self._intent_router is not None:
                intent_route = self._intent_router.route(query, processed)
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
            if intent_route.collection != processed.collection:
                processed = processed.model_copy(
                    update={"collection": intent_route.collection}
                )
                trace_controller.context.collection = intent_route.collection
            trace_controller.record_stage(
                "intent_routing",
                duration_ms=(perf_counter() - route_started) * 1000,
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
            hybrid = self._hybrid_search.search(
                processed,
                filters={"collection": processed.collection},
                trace_context=trace_controller.context,
            )
            rerank_applied = not no_rerank and self._rerank_controller is not None
            rerank_fallback = False
            if rerank_applied:
                outcome = self._rerank_controller.rerank_with_outcome(
                    processed.normalized_query,
                    hybrid.results,
                    top_k=top_k,
                    trace_context=trace_controller.context,
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
                self_rag_decision = self._self_rag_controller.evaluate(
                    processed.normalized_query,
                    final_results,
                    trace_context=trace_controller.context,
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

            response_started = perf_counter()
            response = self._response_builder.build(
                final_results,
                trace_id=trace_id,
                query=processed.normalized_query,
            )
            trace_controller.record_stage(
                "response",
                duration_ms=(perf_counter() - response_started) * 1000,
                input_summary={"final_result_count": len(final_results)},
                output_summary={
                    "citation_count": len(response.citations),
                    "image_count": len(response.images),
                    "is_empty": response.is_empty,
                },
                method="citation_multimodal_build",
                provider=type(self._response_builder).__name__,
                candidate_count=len(final_results),
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
            return QueryExecutionResult(
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
                        self_rag_results=final_results if self_rag_decision is not None else None,
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the supported local-query command-line options.

    Args:
        argv: Optional arguments excluding the executable name. ``None`` uses
            ``sys.argv``.

    Returns:
        Namespace containing query text, final limit, collection override,
        verbose flag, and rerank bypass flag.

    Raises:
        SystemExit: If required values are missing or invalid.
    """

    parser = argparse.ArgumentParser(
        description="Run hybrid RAG retrieval and print a grounded JSON response."
    )
    parser.add_argument(
        "--query",
        required=True,
        type=_non_blank_value("--query"),
        help="Natural-language knowledge query.",
    )
    parser.add_argument(
        "--top-k",
        type=_positive_integer,
        default=10,
        help="Maximum final results (default: 10).",
    )
    parser.add_argument(
        "--collection",
        type=_non_blank_value("--collection"),
        help="Restrict retrieval to one collection.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include trace-safe stage result IDs and scores.",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="Skip the configured reranker and preserve filtered RRF order.",
    )
    return parser.parse_args(argv)


def run_query_cli(
    argv: Sequence[str] | None = None,
    *,
    settings_loader: SettingsLoader = load_settings,
    pool_factory: PoolFactory | None = None,
    schema_initializer: SchemaInitializer = init_schema,
    runtime_builder: RuntimeBuilder | None = None,
    trace_id_factory: TraceIdFactory | None = None,
    output: MessageWriter = print,
    error_output: MessageWriter | None = None,
) -> int:
    """Execute one local query and return a process-compatible exit code.

    Args:
        argv: Optional CLI arguments excluding the executable name.
        settings_loader: Injectable validated-settings loader.
        pool_factory: Injectable lazy PostgreSQL pool constructor.
        schema_initializer: Injectable idempotent schema initializer.
        runtime_builder: Injectable component composition function. The third
            argument indicates whether reranker construction must be skipped.
        trace_id_factory: Injectable stable identifier source for tests.
        output: Writer receiving one JSON success document.
        error_output: Writer receiving one readable failure message. ``None``
            writes to standard error.

    Returns:
        ``0`` on success or ``1`` when configuration, database, provider, or
        retrieval execution fails. Argument errors are handled by argparse.

    Side Effects:
        Loads local environment values, opens PostgreSQL, may call configured
        embedding/reranker providers, reads indexes, and writes one message.
    """

    args = parse_args(argv)
    write_error = error_output or _print_error
    active_pool_factory = pool_factory or _create_pool
    active_runtime_builder = runtime_builder or _build_runtime
    active_trace_id_factory = trace_id_factory or (
        lambda: f"query-{uuid4().hex}"
    )
    pool: PostgresPool | None = None
    try:
        _load_local_environment()
        settings = settings_loader()
        collection = (
            args.collection
            or settings.retrieval.filters.default_collection
        )
        pool = active_pool_factory(settings.database)
        pool.open()
        schema_initializer(pool)
        runtime = active_runtime_builder(settings, pool, args.no_rerank)
        execution = runtime.execute(
            args.query,
            collection=collection,
            top_k=args.top_k,
            no_rerank=args.no_rerank,
            trace_id=active_trace_id_factory(),
            request_source="query_cli",
        )
        payload: dict[str, Any] = {
            "query": args.query,
            "collection": collection,
            "top_k": args.top_k,
            "rerank_applied": execution.rerank_applied,
            "response": execution.response.model_dump(mode="json"),
        }
        if args.verbose:
            payload["debug"] = _build_verbose_debug(execution)
        output(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as error:
        write_error(f"Query failed: {error}")
        return 1
    finally:
        if pool is not None:
            pool.close()


def main() -> int:
    """Run the query CLI with process arguments.

    Returns:
        Process exit code from ``run_query_cli``.
    """

    return run_query_cli()


def _build_runtime(
    settings: RagSettings,
    pool: PostgresPool,
    no_rerank: bool = False,
) -> QueryRuntime:
    """Compose the production query pipeline from settings-backed providers.

    Args:
        settings: Validated runtime settings.
        pool: Open PostgreSQL pool shared by all storage adapters.
        no_rerank: When true, do not construct model-backed reranker
            dependencies because the caller explicitly bypasses that stage.

    Returns:
        A complete ``QueryRuntime`` ready to execute user queries.
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
        provider_settings = settings.rerank.providers.get(
            settings.rerank.default
        )
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
            reranker=RerankerFactory.create(
                settings=settings,
                **reranker_options,
            ),
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

    return QueryRuntime(
        query_processor=query_processor,
        hybrid_search=hybrid_search,
        intent_router=_build_intent_router(settings, embedding, pool),
        rerank_controller=rerank_controller,
        self_rag_controller=self_rag_controller,
        response_builder=KnowledgeHubResponseBuilder(
            multimodal_assembler=MultimodalAssembler(
                resolver=ImageStorage(
                    pool,
                    root_dir=_resolve_runtime_path(
                        settings.ingestion.image_dir
                    ),
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
    """Create the optional intent router from versioned configuration files.

    Args:
        settings: Validated runtime settings containing router file paths and
            thresholds.
        embedding: Configured embedding client reused for semantic profile
            routing so query execution does not create a second provider.
        pool: Open PostgreSQL pool used for profile embedding cache rows.

    Returns:
        Configured ``IntentRouter`` or ``None`` when disabled.
    """

    router_settings = getattr(settings, "intent_router", None)
    if router_settings is None or not router_settings.enabled:
        return None
    return IntentRouter(
        rules=load_intent_rules(
            _resolve_runtime_path(router_settings.rules_path)
        ),
        profiles=load_collection_profiles(
            _resolve_runtime_path(router_settings.collection_profiles_path)
        ),
        embedding_client=embedding,
        profile_repository=CollectionProfileRepository(pool),
        default_collection=settings.retrieval.filters.default_collection,
        rule_threshold=router_settings.rule_threshold,
        semantic_threshold=router_settings.semantic_threshold,
    )


def _build_verbose_debug(
    execution: QueryExecutionResult,
) -> dict[str, Any]:
    """Build trace-safe stage summaries for ``--verbose`` output.

    Args:
        execution: Completed query execution with immutable stage snapshots.

    Returns:
        Query processing fields and chunk ID/score summaries. Retrieval
        metadata is intentionally omitted to prevent internal payload leakage.
    """

    processed = execution.processed_query
    return {
        "query_processor": {
            "raw_query": processed.raw_query,
            "normalized_query": processed.normalized_query,
            "keywords": list(processed.keywords),
            "collection": processed.collection,
            "top_k": processed.top_k,
            "rewrite_applied": processed.rewrite_applied,
            "rewrite_fallback_reason": processed.rewrite_fallback_reason,
        },
        "intent_routing": execution.intent_route.to_trace_details(),
        "dense": _result_summaries(execution.dense_results),
        "sparse": _result_summaries(execution.sparse_results),
        "fusion": _result_summaries(execution.fused_results),
        "filter": _result_summaries(execution.filtered_results),
        "rerank": {
            "applied": execution.rerank_applied,
            "fallback_used": execution.fallback_used,
            "results": _result_summaries(execution.final_results),
        },
        "self_rag": {
            "applied": execution.self_rag_decision is not None,
            "decision": execution.self_rag_decision.decision
            if execution.self_rag_decision is not None
            else None,
            "reason": execution.self_rag_decision.reason
            if execution.self_rag_decision is not None
            else None,
            "results": _result_summaries(execution.final_results),
        },
    }


def _result_summaries(
    results: Sequence[RetrievalResult],
) -> list[dict[str, str | float]]:
    """Project retrieval results onto public debug identifiers and scores."""

    return [
        {"chunk_id": result.chunk_id, "score": result.score}
        for result in results
    ]


def _query_result_snapshot(
    response: KnowledgeHubResponse,
    results: Sequence[RetrievalResult],
) -> dict[str, Any]:
    """Build the compact public result snapshot persisted with a Query Trace.

    The public response retains complete citation and image records for caller
    features such as source navigation and image delivery. Query Trace stores
    only the fields required for evaluation, auditing, and result correlation.
    """

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
    """Return a valid empty result snapshot for failed queries."""

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
    """Build the documented per-stage candidate count summary.

    Args:
        dense_results: Dense Route candidates.
        sparse_results: BM25 Sparse Route candidates.
        fused_results: RRF candidates before metadata filtering.
        filtered_results: Candidates that passed exact metadata filtering.
        rerank_results: Candidates after rerank or filtered-order fallback.
        self_rag_results: Candidates after Self-RAG, or ``None`` when skipped.
        final_results: Response-builder candidates after all gates.

    Returns:
        Dictionary aligned with the TraceContext query summary contract.
    """

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


def _trace_sink_from_settings(
    settings: RagSettings,
    pool: PostgresPool,
) -> TraceSink | None:
    """Create the configured query trace sink when observability is available.

    Args:
        settings: Runtime settings. Unit tests may pass minimal settings
            doubles that omit the observability section.
        pool: Open PostgreSQL pool used when trace persistence is enabled.

    Returns:
        A configured JSON Lines/PostgreSQL writer, or ``None`` for minimal test
        settings and intentionally storage-free composition roots.
    """

    observability = getattr(settings, "observability", None)
    trace_path = getattr(observability, "trace_jsonl_path", None)
    persist_to_postgresql = bool(
        getattr(observability, "persist_to_postgresql", False)
    )
    return build_trace_writer(
        jsonl_path=_resolve_runtime_path(trace_path) if trace_path else None,
        repository=TraceRepository(pool) if persist_to_postgresql else None,
    )


def _create_pool(settings: DatabaseSettings) -> PostgresPool:
    """Create the configured PostgreSQL pool without opening it."""

    return PostgresPool.from_settings(settings)


def _load_local_environment() -> None:
    """Load the nearest parent ``.env`` without overriding process values."""

    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)


def _resolve_runtime_path(path: str | Path) -> Path:
    """Resolve settings paths independently of the launching shell directory."""

    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (RAG_ROOT / candidate).resolve()


def _non_blank_value(option: str) -> Callable[[str], str]:
    """Create an argparse converter for one non-blank string option."""

    def validate(value: str) -> str:
        """Strip and validate one command-line string value."""

        normalized = value.strip()
        if not normalized:
            raise argparse.ArgumentTypeError(f"{option} must not be blank")
        return normalized

    return validate


def _positive_integer(value: str) -> int:
    """Convert one CLI value to a strictly positive integer."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "--top-k must be an integer"
        ) from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            "--top-k must be greater than zero"
        )
    return parsed


def _print_error(message: str) -> None:
    """Write one CLI error message to standard error."""

    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
