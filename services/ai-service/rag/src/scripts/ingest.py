"""Run configured offline ingestion for one document or a source directory.

This module is the local operator entry point for the Phase C ingestion
pipeline. It parses command-line options, discovers supported PDF and Markdown
sources, opens PostgreSQL once, builds the configured pipeline for each source,
and emits one JSON summary after all selected files complete.

The script owns process-level resource management and component composition. It
does not implement document parsing, chunking, transforms, embedding, sparse
indexing, or persistence rules; those responsibilities remain in their
existing Loader, Pipeline, and storage modules.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

from src.core.config import RAG_ROOT, DatabaseSettings, RagSettings, load_prompt, load_settings
from src.ingestion import DocumentSummarizer, IngestionPipeline
from src.ingestion.chunk import DocumentChunker, SplitterStep
from src.ingestion.embedding import (
    BatchProcessor,
    BM25Indexer,
    DenseEncoder,
    EmbeddingStep,
)
from src.ingestion.storage import UpsertStep
from src.ingestion.transform import TransformPipeline
from src.libs.embedding import EmbeddingFactory
from src.libs.llm import LLMFactory
from src.libs.loader import LoaderFactory
from src.libs.splitter import SplitterFactory
from src.libs.vector_store import VectorStoreFactory
from src.storage.bm25_storage import BM25Storage
from src.storage.image_storage import ImageStorage
from src.storage.postgres import PostgresPool, init_schema
from src.storage.repositories import (
    ChunkRepository,
    DocumentRepository,
    TraceRepository,
)
from src.storage.trace_log_storage import JsonlTraceWriter

SUPPORTED_SOURCE_SUFFIXES = frozenset({".md", ".markdown", ".pdf"})

SettingsLoader = Callable[[], RagSettings]
PoolFactory = Callable[[DatabaseSettings], PostgresPool]
SchemaInitializer = Callable[[PostgresPool], None]
PipelineBuilder = Callable[[Path, RagSettings, PostgresPool], IngestionPipeline]
MessageWriter = Callable[[str], Any]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the supported offline-ingestion command-line options.

    Args:
        argv: Optional argument sequence excluding the executable name. ``None``
            delegates to ``sys.argv`` for normal command-line execution.

    Returns:
        An ``argparse.Namespace`` containing the source path, optional
        collection override, and force flag.

    Raises:
        SystemExit: If ``--path`` is missing or an option is malformed.
    """

    parser = argparse.ArgumentParser(
        description="Ingest Markdown or PDF documents into the configured RAG collection."
    )
    parser.add_argument(
        "--path",
        required=True,
        type=Path,
        help="Source file or directory. Directories are searched recursively.",
    )
    parser.add_argument(
        "--collection",
        type=_non_blank_collection,
        help="Target collection. Defaults to project.default_collection.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass source SHA256 deduplication and rebuild selected documents.",
    )
    return parser.parse_args(argv)


def run_ingest_cli(
    argv: Sequence[str] | None = None,
    *,
    settings_loader: SettingsLoader = load_settings,
    pool_factory: PoolFactory | None = None,
    schema_initializer: SchemaInitializer = init_schema,
    pipeline_builder: PipelineBuilder | None = None,
    output: MessageWriter = print,
    error_output: MessageWriter | None = None,
) -> int:
    """Execute offline ingestion and return a process-compatible exit code.

    Args:
        argv: Optional CLI arguments excluding the executable name.
        settings_loader: Injectable validated-settings loader.
        pool_factory: Injectable PostgreSQL pool constructor.
        schema_initializer: Injectable idempotent schema initializer.
        pipeline_builder: Injectable source-specific pipeline composer.
        output: Writer receiving the final JSON success summary.
        error_output: Writer receiving a readable failure message. ``None``
            writes to standard error.

    Returns:
        ``0`` when every selected source completes, ``2`` when the source
        selection is invalid, or ``1`` when configuration, provider, database,
        or ingestion execution fails.

    Side Effects:
        Reads source files, initializes PostgreSQL schema, invokes external
        model providers configured by ``settings.yaml``, persists indexes, and
        writes one summary message.
    """

    args = parse_args(argv)
    write_error = error_output or _print_error
    try:
        sources = _discover_sources(args.path)
    except ValueError as error:
        write_error(str(error))
        return 2

    active_pool_factory = pool_factory or _create_pool
    active_pipeline_builder = pipeline_builder or _build_pipeline
    pool: PostgresPool | None = None
    try:
        _load_local_environment()
        settings = settings_loader()
        collection = args.collection or settings.project.default_collection
        pool = active_pool_factory(settings.database)
        pool.open()
        schema_initializer(pool)

        results: list[dict[str, Any]] = []
        for source in sources:
            pipeline = active_pipeline_builder(source, settings, pool)
            result = pipeline.run(
                source,
                collection_id=collection,
                force=args.force,
            )
            results.append(
                {
                    "source": result.source_uri,
                    "status": result.status,
                    "trace_id": result.trace_id,
                    "source_hash": result.source_hash,
                    "summary": dict(result.trace_summary),
                }
            )

        output(
            json.dumps(
                {
                    "collection": collection,
                    "force": args.force,
                    "processed": len(results),
                    "results": results,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as error:
        write_error(f"Ingestion failed: {error}")
        return 1
    finally:
        if pool is not None:
            pool.close()


def main() -> int:
    """Run the ingestion CLI with process arguments.

    Returns:
        Process exit code returned by ``run_ingest_cli``.
    """

    return run_ingest_cli()


def _discover_sources(source: Path) -> list[Path]:
    """Resolve one file or recursively discover supported source documents.

    Args:
        source: User-selected file or directory.

    Returns:
        Deterministically sorted absolute source paths.

    Raises:
        ValueError: If the path does not exist, is unsupported, or contains no
            supported Markdown/PDF files.
    """

    resolved = source.expanduser().resolve()
    candidates: list[Path]
    if resolved.is_file():
        candidates = [resolved] if resolved.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES else []
    elif resolved.is_dir():
        candidates = [
            path.resolve()
            for path in resolved.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
        ]
    else:
        candidates = []

    ordered = sorted(candidates)
    if not ordered:
        raise ValueError(
            "No supported ingestion files were found. "
            "Expected .md, .markdown, or .pdf sources."
        )
    return ordered


def _build_pipeline(
    source: Path,
    settings: RagSettings,
    pool: PostgresPool,
) -> IngestionPipeline:
    """Compose one source-specific pipeline from validated runtime settings.

    Args:
        source: Source path whose suffix selects the Loader implementation.
        settings: Validated configuration controlling every pluggable component.
        pool: Open PostgreSQL pool shared across the CLI run.

    Returns:
        A complete ``IngestionPipeline`` ready to process ``source``.

    Notes:
        Vision captioning degrades to skipped metadata when no dedicated Vision
        client implementation is available. The configured image-to-text
        transform remains in the chain and preserves image references.
    """

    loader_options: dict[str, Any] = {}
    image_output_dir = _resolve_runtime_path(settings.ingestion.image_dir)
    if source.suffix.lower() == ".pdf":
        loader_options["image_output_dir"] = image_output_dir
    loader = LoaderFactory.for_source(source, **loader_options)
    documents = DocumentRepository(pool)
    chunks = ChunkRepository(pool)
    embedding = EmbeddingFactory.create(settings=settings)
    vector_store = VectorStoreFactory.create(settings=settings, pool=pool)

    return IngestionPipeline(
        loader=loader,
        document_repository=documents,
        chunk_repository=chunks,
        trace_repository=TraceRepository(pool),
        trace_sink=JsonlTraceWriter(
            _resolve_runtime_path(settings.observability.trace_jsonl_path)
        ),
        document_summarizer=_build_document_summarizer(settings),
        splitter_step=SplitterStep(
            DocumentChunker(splitter=SplitterFactory.create(settings=settings))
        ),
        transform_pipeline=TransformPipeline.from_settings(settings),
        embedding_step=EmbeddingStep(
            dense_encoder=DenseEncoder(embedding=embedding),
            bm25_indexer=BM25Indexer(),
            batch_processor=BatchProcessor(batch_size=settings.embedding.batch_size),
        ),
        upsert_step=UpsertStep(
            pool=pool,
            document_repository=documents,
            chunk_repository=chunks,
            vector_store=vector_store,
            bm25_storage=BM25Storage(pool),
            image_storage=ImageStorage(
                pool,
                root_dir=image_output_dir,
            ),
        ),
    )


def _build_document_summarizer(
    settings: RagSettings,
    *,
    llm: Any | None = None,
) -> DocumentSummarizer | None:
    """Create the optional document-summary step from ingestion settings.

    Args:
        settings: Validated RAG settings. The ``ingestion.document_summary``
            mapping is intentionally read as an extension field so older config
            files can keep running without a migration.
        llm: Optional injected LLM client used by tests. ``None`` uses the
            configured ``LLMFactory`` provider.

    Returns:
        A configured ``DocumentSummarizer`` when enabled, otherwise ``None``.
    """

    config = getattr(settings.ingestion, "document_summary", None)
    if config is None:
        config = {
            "enabled": True,
            "llm_provider": "deepseek",
            "prompt_path": "config/prompts/document_summary_prompt.yaml",
            "max_document_chars": 12000,
        }
    if not isinstance(config, dict) or not config.get("enabled", False):
        return None
    prompt_path = str(
        config.get("prompt_path", "config/prompts/document_summary_prompt.yaml")
    )
    llm_provider = str(config.get("llm_provider", settings.llm.default))
    max_document_chars = int(config.get("max_document_chars", 12000))
    return DocumentSummarizer(
        llm=llm or LLMFactory.create(settings=settings, provider=llm_provider),
        prompt=load_prompt(prompt_path),
        max_document_chars=max_document_chars,
    )


def _create_pool(settings: DatabaseSettings) -> PostgresPool:
    """Create the configured PostgreSQL pool without opening it."""

    return PostgresPool.from_settings(settings)


def _load_local_environment() -> None:
    """Load the nearest parent ``.env`` file for local command-line execution.

    The search starts at the current working directory and walks through parent
    directories, allowing commands launched from either the repository root or
    the nested RAG project to reuse the repository-level ``.env`` file.
    Existing process variables are never overwritten, so shell, CI, and Docker
    environment injection remains authoritative.

    Side Effects:
        Adds values from the discovered ``.env`` file to ``os.environ`` only
        when the corresponding process variable is currently absent.
    """

    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)


def _resolve_runtime_path(path: str | Path) -> Path:
    """Resolve configured runtime storage paths against the RAG module root.

    Args:
        path: Absolute path or RAG-relative path from ``settings.yaml``.

    Returns:
        A normalized absolute path. Relative values remain independent of the
        shell working directory used to launch the ingestion command.
    """

    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (RAG_ROOT / candidate).resolve()


def _non_blank_collection(value: str) -> str:
    """Validate and normalize a collection command-line value."""

    normalized = value.strip()
    if not normalized:
        raise argparse.ArgumentTypeError("--collection must not be blank")
    return normalized


def _print_error(message: str) -> None:
    """Write one CLI error message to standard error."""

    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
