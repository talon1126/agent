"""Run golden-set evaluation against the real query pipeline.

This script is the executable Phase G bridge between the static golden set,
the configured retrieval stack, Ragas-compatible generation metrics, and
PostgreSQL evaluation history. It intentionally reuses ``QueryRuntime`` from
``src.scripts.query`` so evaluation observes the same query processing,
retrieval, rerank, response building, and trace-writing behavior as CLI, MCP,
and AImodel callers.

The first implementation evaluates the RAG answer source, meaning
``query_result.content`` is treated as the generated response. Agent-message
evaluation belongs to the AImodel integration phase because it requires the
message table and ``message_query_trace`` association.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from src.core.config import (
    RAG_ROOT,
    RagSettings,
    enabled_generation_metrics,
    load_settings,
)
from src.core.types import Chunk
from src.libs.vector_store import VectorStoreFactory
from src.observability.services import EvaluationService
from src.scripts.query import (
    _build_runtime,
    _create_pool,
    _load_local_environment,
)
from src.storage.postgres import PostgresPool, init_schema


class ChunkLookup(Protocol):
    """Describe the chunk text lookup required to build Ragas contexts."""

    def get_by_ids(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        """Return existing chunks in the caller-requested relative order."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the local evaluation command-line interface.

    Args:
        argv: Optional command arguments excluding the executable. ``None``
            uses process arguments through ``argparse``.

    Returns:
        Parsed namespace containing dataset, collection, evaluator, and query
        execution options.
    """

    parser = argparse.ArgumentParser(
        description="Run RAG golden-set evaluation and persist metric history."
    )
    parser.add_argument(
        "--collection",
        help="Restrict evaluation to one collection. Defaults to settings or sample collection.",
    )
    parser.add_argument(
        "--golden-set",
        help="Path to the golden set JSON file. Defaults to settings.evaluation.golden_set_path.",
    )
    parser.add_argument(
        "--evaluator",
        default="ragas",
        help="Evaluator provider registered in EvaluatorFactory. Defaults to ragas.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Final query result count. Defaults to settings.retrieval.final_top_k.",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="Skip the configured reranker during evaluation queries.",
    )
    parser.add_argument(
        "--answer-source",
        choices=("rag",),
        default="rag",
        help="Answer source to evaluate. Phase G supports query_result.content.",
    )
    return parser.parse_args(argv)


def run_evaluation_cli(
    argv: Sequence[str] | None = None,
    *,
    output: Any = print,
    error_output: Any | None = None,
) -> int:
    """Execute golden-set evaluation and return a process exit code.

    Args:
        argv: Optional CLI arguments excluding the executable.
        output: Writer receiving one JSON success document.
        error_output: Writer receiving readable failure messages. ``None``
            writes to stderr.

    Returns:
        ``0`` on success, otherwise ``1``.

    Side Effects:
        Loads local environment variables, opens PostgreSQL, runs real query
        pipeline calls, writes query traces, and persists evaluation run/result
        records.
    """

    args = parse_args(argv)
    write_error = error_output or (lambda message: print(message, file=sys.stderr))
    pool: PostgresPool | None = None
    try:
        _load_local_environment()
        settings = load_settings()
        top_k = args.top_k or settings.retrieval.final_top_k
        if top_k <= 0:
            raise ValueError("--top-k must be greater than zero")
        golden_set_path = _resolve_golden_set_path(args.golden_set, settings=settings)
        dataset = _filter_dataset(
            load_golden_set(golden_set_path),
            collection=args.collection,
        )
        if not dataset:
            raise ValueError("Golden set contains no samples for the selected collection")
        collection = args.collection or select_single_collection(dataset)

        pool = _create_pool(settings.database)
        pool.open()
        init_schema(pool)
        runtime = _build_runtime(settings, pool, args.no_rerank)
        metric_names = enabled_generation_metrics(settings)
        chunk_lookup = VectorStoreFactory.create(settings=settings, pool=pool)
        predictions = [
            _prediction_for_sample(
                sample,
                runtime=runtime,
                chunk_lookup=chunk_lookup,
                collection=collection,
                top_k=top_k,
                no_rerank=args.no_rerank,
            )
            for sample in dataset
        ]

        detail = EvaluationService(pool).run_evaluation(
            collection_id=collection,
            evaluator=args.evaluator,
            dataset_name=golden_set_path.stem,
            dataset=dataset,
            predictions=predictions,
            evaluator_options={"settings": settings, "metric_names": metric_names},
            settings_snapshot={
                "answer_source": args.answer_source,
                "collection": collection,
                "top_k": top_k,
                "no_rerank": args.no_rerank,
                "golden_set_path": str(golden_set_path),
                "evaluation_llm_provider": settings.evaluation.llm_provider,
                "evaluation_embedding_provider": (
                    settings.evaluation.embedding_provider
                ),
                "generation_metrics": metric_names,
                "response_optimizer_enabled": (
                    settings.response.evidence_context_optimizer.enabled
                ),
            },
        )
        output(json.dumps(_detail_payload(detail), ensure_ascii=False))
        return 0 if detail.status == "success" else 1
    except Exception as error:
        write_error(f"Evaluation failed: {error}")
        return 1
    finally:
        if pool is not None:
            pool.close()


def load_golden_set(path: Path) -> list[dict[str, Any]]:
    """Load and validate the JSON golden set used by evaluation.

    Args:
        path: Absolute golden set path.

    Returns:
        List of sample mappings.

    Raises:
        ValueError: If the file is missing, not a JSON array, or lacks required
            sample fields.
    """

    if not path.is_file():
        raise ValueError(f"Golden set file does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("Golden set must be a non-empty JSON array")
    samples: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise ValueError("Golden set samples must be JSON objects")
        sample = dict(item)
        for field_name in ("id", "collection", "question", "golden_answer"):
            _required_text(sample.get(field_name), field_name=field_name)
        samples.append(sample)
    return samples


def build_prediction_from_query_result(
    sample: Mapping[str, Any],
    query_result: Mapping[str, Any],
    *,
    chunk_lookup: ChunkLookup,
    query_trace_id: str,
) -> dict[str, Any]:
    """Build one Ragas-compatible prediction from a Query Trace result.

    Args:
        sample: Golden set sample that produced the query.
        query_result: Query trace ``query_result`` section containing content
            and ranked context identities.
        chunk_lookup: Vector store or fake exposing ``get_by_ids``.
        query_trace_id: Trace ID written by the query pipeline.

    Returns:
        Prediction record with ``answer`` from ``query_result.content`` and
        ``contexts`` from ranked chunk text.

    Raises:
        ValueError: If content, contexts, or looked-up chunk text is missing.
    """

    answer = _required_text(query_result.get("content"), field_name="query_result.content")
    ranked_contexts = _ranked_contexts(query_result.get("contexts"))
    chunk_ids = [context["chunk_id"] for context in ranked_contexts]
    chunks_by_id = {chunk.id: chunk for chunk in chunk_lookup.get_by_ids(chunk_ids)}
    missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in chunks_by_id]
    if missing:
        raise ValueError(f"Unable to load retrieved chunk text for IDs: {missing}")
    retrieved_contexts = [
        _required_text(chunks_by_id[chunk_id].text, field_name=f"chunk {chunk_id} text")
        for chunk_id in chunk_ids
    ]
    return {
        "sample_id": sample.get("id"),
        "question": _required_text(sample.get("question"), field_name="question"),
        "answer": answer,
        "contexts": retrieved_contexts,
        "retrieved_contexts": retrieved_contexts,
        "query_trace_id": _required_text(query_trace_id, field_name="query_trace_id"),
        "context_chunk_ids": chunk_ids,
    }


def main() -> int:
    """Run the evaluation CLI with process arguments."""

    return run_evaluation_cli()


def _prediction_for_sample(
    sample: Mapping[str, Any],
    *,
    runtime: Any,
    chunk_lookup: ChunkLookup,
    collection: str,
    top_k: int,
    no_rerank: bool,
) -> dict[str, Any]:
    """Execute one query and convert its result into a prediction record."""

    trace_id = f"query-eval-{uuid4().hex}"
    execution = runtime.execute(
        _required_text(sample.get("question"), field_name="question"),
        collection=collection,
        top_k=top_k,
        no_rerank=no_rerank,
        trace_id=trace_id,
    )
    return build_prediction_from_query_result(
        sample,
        {
            "contexts": [
                {"chunk_id": result.chunk_id, "score": result.score, "rank": rank}
                for rank, result in enumerate(execution.final_results, start=1)
            ],
            "content": execution.response.content,
            "citations": [
                citation.model_dump(mode="json")
                for citation in execution.response.citations
            ],
            "images": [image.model_dump(mode="json") for image in execution.response.images],
        },
        chunk_lookup=chunk_lookup,
        query_trace_id=trace_id,
    )


def _resolve_golden_set_path(path: str | None, *, settings: RagSettings) -> Path:
    """Resolve configured or CLI-provided golden set paths."""

    candidate = Path(path or settings.evaluation.golden_set_path)
    if candidate.is_absolute():
        return candidate
    working_directory_candidate = (Path.cwd() / candidate).resolve()
    if working_directory_candidate.exists():
        return working_directory_candidate
    return (RAG_ROOT / candidate).resolve()


def _filter_dataset(
    dataset: list[dict[str, Any]],
    *,
    collection: str | None,
) -> list[dict[str, Any]]:
    """Return samples matching the selected collection, preserving order."""

    if collection is None:
        return dataset
    return [sample for sample in dataset if sample.get("collection") == collection]


def select_single_collection(dataset: Sequence[Mapping[str, Any]]) -> str:
    """Return the only collection represented by a dataset.

    Args:
        dataset: Already validated and optionally filtered golden samples.

    Returns:
        The single collection shared by all samples.

    Raises:
        ValueError: If the dataset mixes collections and the caller did not
            select one explicitly.
    """

    collections = sorted(
        {
            _required_text(sample.get("collection"), field_name="collection")
            for sample in dataset
        }
    )
    if len(collections) != 1:
        raise ValueError(
            "Golden set contains multiple collections; pass --collection to select one"
        )
    return collections[0]


def _ranked_contexts(value: Any) -> list[dict[str, Any]]:
    """Validate and sort ``query_result.contexts`` by final rank."""

    if not isinstance(value, Sequence) or isinstance(value, str) or not value:
        raise ValueError("query_result.contexts must be a non-empty list")
    contexts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("query_result.contexts items must be objects")
        chunk_id = _required_text(item.get("chunk_id"), field_name="context.chunk_id")
        rank = item.get("rank")
        if not isinstance(rank, int) or rank <= 0:
            raise ValueError("context.rank must be a positive integer")
        contexts.append({"chunk_id": chunk_id, "rank": rank})
    return sorted(contexts, key=lambda context: context["rank"])


def _detail_payload(detail: Any) -> dict[str, Any]:
    """Convert an EvaluationRunDetail DTO into JSON-safe CLI output."""

    return {
        "run_id": detail.run_id,
        "collection": detail.collection_id,
        "evaluator": detail.evaluator,
        "dataset_name": detail.dataset_name,
        "status": detail.status,
        "metrics": dict(detail.metrics),
        "summary": dict(detail.summary),
        "error": dict(detail.error) if detail.error is not None else None,
    }


def _required_text(value: Any, *, field_name: str) -> str:
    """Return stripped non-empty text or raise a field-specific error."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


if __name__ == "__main__":
    raise SystemExit(main())
