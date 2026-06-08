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
    ProcessedQuery,
    QueryProcessor,
    RerankController,
    SparseRoute,
)
from src.core.response import (
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
from src.storage.trace_log_storage import JsonlTraceWriter

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
        dense_results: Dense candidates in provider order.
        sparse_results: Sparse candidates in BM25 order.
        fused_results: RRF candidates before metadata filtering.
        filtered_results: Candidates allowed to enter reranking.
        final_results: Reranked or explicitly preserved RRF candidates.
        response: Public knowledge response containing no internal metadata.
        rerank_applied: Whether a configured reranker stage was attempted.
        fallback_used: Whether route or reranker degradation preserved results.
    """

    processed_query: ProcessedQuery
    dense_results: tuple[RetrievalResult, ...]
    sparse_results: tuple[RetrievalResult, ...]
    fused_results: tuple[RetrievalResult, ...]
    filtered_results: tuple[RetrievalResult, ...]
    final_results: tuple[RetrievalResult, ...]
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
        trace_sink: TraceSink | None = None,
    ) -> None:
        """Configure the already-created query pipeline components.

        Args:
            query_processor: User-query normalization and intent component.
            hybrid_search: Dense, Sparse, RRF, and metadata-filter orchestration.
            rerank_controller: Optional rerank/fallback controller. ``None``
                means reranking is disabled by configuration.
            response_builder: Public evidence, citation, and image assembler.
            trace_sink: Optional trace sink receiving one finished query
                snapshot. Production can inject JSON Lines logging while tests
                pass a list appender.
        """

        self._query_processor = query_processor
        self._hybrid_search = hybrid_search
        self._rerank_controller = rerank_controller
        self._response_builder = response_builder
        self._trace_sink = trace_sink

    def execute(
        self,
        query: str,
        *,
        collection: str,
        top_k: int,
        no_rerank: bool,
        trace_id: str,
    ) -> QueryExecutionResult:
        """Run the complete query path and return public/debug projections.

        Args:
            query: Raw user question.
            collection: Collection selected by CLI override or settings.
            top_k: Positive final result limit.
            no_rerank: Explicit caller request to bypass reranking.
            trace_id: Stable query identifier included in every citation.

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
            request_source="query_cli",
        )
        trace_controller = TraceController(trace_context, sink=self._trace_sink)
        hybrid = None
        final_results: list[RetrievalResult] = []
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
                    "intent": processed.intent.value,
                    "collection": processed.collection,
                    "top_k": processed.top_k,
                    "rewrite_applied": processed.rewrite_applied,
                },
                method="normalize_rewrite_tokenize",
                provider=type(self._query_processor).__name__,
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
                        "before_order": [
                            candidate.chunk_id for candidate in hybrid.results
                        ],
                        "after_order": [
                            candidate.chunk_id for candidate in final_results
                        ],
                    },
                )

            response_started = perf_counter()
            response = self._response_builder.build(
                final_results,
                trace_id=trace_id,
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
            fallback_used = hybrid.fallback_used or rerank_fallback
            trace_controller.flush_query(
                status="degraded" if fallback_used else "success",
                top_k_results=_result_summaries(final_results),
                candidate_count_by_stage=_candidate_counts(
                    dense_results=hybrid.dense_results,
                    sparse_results=hybrid.sparse_results,
                    fused_results=hybrid.fused_results,
                    filtered_results=hybrid.results,
                    final_results=final_results,
                ),
                fallback_used=fallback_used,
                empty_result=response.is_empty,
            )
            return QueryExecutionResult(
                processed_query=processed,
                dense_results=tuple(hybrid.dense_results),
                sparse_results=tuple(hybrid.sparse_results),
                fused_results=tuple(hybrid.fused_results),
                filtered_results=tuple(hybrid.results),
                final_results=tuple(final_results),
                response=response,
                rerank_applied=rerank_applied,
                fallback_used=fallback_used,
            )
        except Exception as error:
            if trace_controller.context.finished_at is None:
                trace_controller.flush_query(
                    status="failed",
                    top_k_results=_result_summaries(final_results),
                    candidate_count_by_stage=_candidate_counts(
                        dense_results=hybrid.dense_results if hybrid else [],
                        sparse_results=hybrid.sparse_results if hybrid else [],
                        fused_results=hybrid.fused_results if hybrid else [],
                        filtered_results=hybrid.results if hybrid else [],
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

    return QueryRuntime(
        query_processor=query_processor,
        hybrid_search=hybrid_search,
        rerank_controller=rerank_controller,
        response_builder=KnowledgeHubResponseBuilder(
            multimodal_assembler=MultimodalAssembler(
                resolver=ImageStorage(
                    pool,
                    root_dir=_resolve_runtime_path(
                        settings.ingestion.image_dir
                    ),
                )
            )
        ),
        trace_sink=_trace_sink_from_settings(settings),
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
            "intent": processed.intent.value,
            "collection": processed.collection,
            "top_k": processed.top_k,
            "rewrite_applied": processed.rewrite_applied,
            "rewrite_fallback_reason": processed.rewrite_fallback_reason,
        },
        "dense": _result_summaries(execution.dense_results),
        "sparse": _result_summaries(execution.sparse_results),
        "fusion": _result_summaries(execution.fused_results),
        "filter": _result_summaries(execution.filtered_results),
        "rerank": {
            "applied": execution.rerank_applied,
            "fallback_used": execution.fallback_used,
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


def _candidate_counts(
    *,
    dense_results: Sequence[RetrievalResult],
    sparse_results: Sequence[RetrievalResult],
    fused_results: Sequence[RetrievalResult],
    filtered_results: Sequence[RetrievalResult],
    final_results: Sequence[RetrievalResult],
) -> dict[str, int]:
    """Build the documented per-stage candidate count summary.

    Args:
        dense_results: Dense Route candidates.
        sparse_results: BM25 Sparse Route candidates.
        fused_results: RRF candidates before metadata filtering.
        filtered_results: Candidates that passed exact metadata filtering.
        final_results: Reranked or preserved final candidates.

    Returns:
        Dictionary aligned with the TraceContext query summary contract.
    """

    return {
        "dense": len(dense_results),
        "sparse": len(sparse_results),
        "fusion": len(fused_results),
        "filter": len(filtered_results),
        "rerank": len(final_results),
    }


def _trace_sink_from_settings(settings: RagSettings) -> TraceSink | None:
    """Create the configured query trace sink when observability is available.

    Args:
        settings: Runtime settings. Unit tests may pass minimal settings
            doubles that omit the observability section.

    Returns:
        A JSON Lines trace writer for production settings, or ``None`` for
        minimal test settings and intentionally storage-free composition roots.
    """

    observability = getattr(settings, "observability", None)
    trace_path = getattr(observability, "trace_jsonl_path", None)
    if not trace_path:
        return None
    return JsonlTraceWriter(_resolve_runtime_path(trace_path))


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
