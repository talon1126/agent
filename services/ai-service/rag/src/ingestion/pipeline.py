"""Run the offline ingestion and indexing pipeline.

The pipeline starts with source SHA256 deduplication, then conditionally runs
Loader, business chunking, configured transforms, Dense/BM25 batch indexing,
and transactional persistence. The constructor keeps post-Loader components
optional so focused C1 unit tests and lightweight callers can still exercise the
legacy ``loaded`` boundary; production composition injects the complete C10
component set and receives an ``indexed`` result.

Phase F injects a storage-independent TraceContext through every complete
ingestion stage. The final snapshot is sent to one composition-root-provided
trace sink so JSON Lines and PostgreSQL persistence observe identical data
without storage-specific branches inside the business pipeline.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import Any, Literal
from uuid import uuid4

from src.core.errors import IngestionError
from src.core.trace import TraceContext, TraceController
from src.core.trace.trace_controller import TraceSink
from src.core.types import Chunk, Document
from src.ingestion.chunk import SplitterStep
from src.ingestion.document_summarizer import DocumentSummarizer
from src.ingestion.embedding import EmbeddingBatchResult, EmbeddingStep
from src.ingestion.storage import UpsertResult, UpsertStep
from src.ingestion.transform import TransformPipeline
from src.libs.loader.base_loader import BaseLoader
from src.storage.repositories import (
    ChunkRepository,
    DocumentRepository,
)

DEFAULT_HASH_BLOCK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class IngestionPipelineResult:
    """Describe one skipped, loaded-only, or fully indexed pipeline run.

    Attributes:
        trace_id: Stable identifier reserved for the complete ingestion run.
        collection_id: Collection receiving the source.
        source_uri: Canonical absolute source path used for deduplication.
        source_hash: SHA256 digest of original source bytes.
        status: ``skipped`` for an unchanged successful source, ``loaded`` when
            only the legacy Loader boundary is configured, or ``indexed`` after
            the complete C10 workflow succeeds.
        document: Loader output for non-skipped runs; otherwise ``None``.
        chunks: Final transformed chunks for an indexed run.
        indexing_result: Dense/BM25 batch result for an indexed run.
        upsert_result: Durable IDs written by the C9 transaction.
        trace_summary: Trace-safe deduplication outcome and duration metrics.
    """

    trace_id: str
    collection_id: str
    source_uri: str
    source_hash: str
    status: Literal["skipped", "loaded", "indexed"]
    document: Document | None
    trace_summary: Mapping[str, Any]
    chunks: tuple[Chunk, ...] = ()
    indexing_result: EmbeddingBatchResult | None = None
    upsert_result: UpsertResult | None = None

    def __post_init__(self) -> None:
        """Freeze the summary mapping to prevent post-run trace mutation."""

        object.__setattr__(
            self,
            "trace_summary",
            MappingProxyType(dict(self.trace_summary)),
        )
        object.__setattr__(self, "chunks", tuple(self.chunks))


# Preserve the C1 public name for existing callers while C10 exposes the more
# accurate complete-pipeline result name.
IngestionRunResult = IngestionPipelineResult


def calculate_sha256(
    source: str | Path,
    *,
    block_size: int = DEFAULT_HASH_BLOCK_SIZE,
) -> str:
    """Calculate a SHA256 digest directly from original source bytes.

    Args:
        source: Filesystem source hashed before Loader parsing or conversion.
        block_size: Positive read size used to avoid loading large PDFs fully
            into memory.

    Returns:
        Lowercase 64-character SHA256 hexadecimal digest.

    Raises:
        ValueError: If ``block_size`` is not positive.
        IngestionError: If the source cannot be opened or read.
    """

    if block_size <= 0:
        raise ValueError("SHA256 block_size must be greater than zero")

    resolved_source = Path(source).expanduser().resolve()
    digest = sha256()
    try:
        with resolved_source.open("rb") as source_file:
            while block := source_file.read(block_size):
                digest.update(block)
    except OSError as error:
        raise IngestionError(
            "Unable to read source for SHA256 deduplication",
            context={
                "operation": "source_hash",
                "source": str(resolved_source),
            },
            cause=error,
        ) from error
    return digest.hexdigest()


def should_skip_document(
    document_repository: DocumentRepository,
    *,
    collection_id: str,
    source_path: str,
    source_hash: str,
    force: bool = False,
) -> bool:
    """Decide whether ingestion may stop before Loader execution.

    Args:
        document_repository: Repository queried for successful source state.
        collection_id: Target collection.
        source_path: Canonical source path.
        source_hash: SHA256 digest of original source bytes.
        force: When true, bypass repository lookup and always continue.

    Returns:
        ``True`` only for an unchanged successful source when force is disabled.
    """

    if force:
        return False
    return document_repository.has_successful_source_hash(
        collection_id=collection_id,
        source_path=source_path,
        source_hash=source_hash,
    )


class IngestionPipeline:
    """Coordinate source deduplication and optional complete indexing."""

    def __init__(
        self,
        *,
        loader: BaseLoader,
        document_repository: DocumentRepository,
        chunk_repository: ChunkRepository | None = None,
        trace_sink: TraceSink | None = None,
        document_summarizer: DocumentSummarizer | None = None,
        splitter_step: SplitterStep | None = None,
        transform_pipeline: TransformPipeline | None = None,
        transform_snapshot_options: Mapping[str, Any] | None = None,
        embedding_step: EmbeddingStep | None = None,
        upsert_step: UpsertStep | None = None,
        trace_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Inject source, persistence, and deterministic test dependencies.

        Args:
            loader: Source adapter invoked only after deduplication allows work.
            document_repository: Durable successful-source lookup.
            chunk_repository: Durable chunk-vector lookup used for differential
                embedding in complete mode.
            trace_sink: Optional storage-independent trace sink receiving one
                finished JSON-compatible snapshot per run.
            document_summarizer: Optional independent LLM-backed summary step
                executed after Loader and before Splitter.
            splitter_step: Optional Document-to-Chunk business adapter.
            transform_pipeline: Optional ordered transform chain.
            transform_snapshot_options: Optional bounded before/after preview
                policy passed to ``TransformPipeline`` for trace snapshots.
            embedding_step: Optional Dense/BM25 batch indexing orchestrator.
            upsert_step: Optional transactional persistence orchestrator.
            trace_id_factory: Optional stable-ID factory for deterministic tests.
            clock: Optional timezone-aware clock for deterministic trace times.

        Raises:
            ValueError: If only part of the complete C10 component set is
                supplied. Loader-only mode requires all five to be omitted.
        """

        optional_components = (
            chunk_repository,
            splitter_step,
            transform_pipeline,
            embedding_step,
            upsert_step,
        )
        if any(component is not None for component in optional_components) and not all(
            component is not None for component in optional_components
        ):
            raise ValueError(
                "Complete ingestion mode requires splitter_step, "
                "transform_pipeline, embedding_step, upsert_step, and "
                "chunk_repository"
            )
        self._loader = loader
        self._document_repository = document_repository
        self._chunk_repository = chunk_repository
        self._trace_sink = trace_sink
        self._document_summarizer = document_summarizer
        self._splitter_step = splitter_step
        self._transform_pipeline = transform_pipeline
        self._transform_snapshot_options = (
            dict(transform_snapshot_options)
            if transform_snapshot_options is not None
            else None
        )
        self._embedding_step = embedding_step
        self._upsert_step = upsert_step
        self._trace_id_factory = trace_id_factory or (
            lambda: f"ingestion-{uuid4().hex}"
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(
        self,
        source: str | Path,
        *,
        collection_id: str,
        force: bool = False,
    ) -> IngestionRunResult:
        """Run source deduplication and every configured ingestion stage.

        Args:
            source: Local file path to ingest.
            collection_id: Target knowledge collection.
            force: Bypass successful-source deduplication when true.

        Returns:
            ``IngestionPipelineResult`` containing a skipped, loaded-only, or
            completely indexed result.

        Raises:
            ValueError: If ``collection_id`` is blank.
            IngestionError: If source hashing fails.
            DatabaseError: If deduplication or skipped-trace persistence fails.
            Exception: Loader-specific failures are preserved for the Loader
                adapter to classify according to its contract.

        Side Effects:
            Reads the original source, queries PostgreSQL, calls Loader only
            when required, and in complete mode persists chunks, vectors, BM25
            postings, images, and document lifecycle state.
        """

        if not collection_id.strip():
            raise ValueError("collection_id must not be blank")

        started_at = self._clock()
        trace_id = self._trace_id_factory()
        source_path = Path(source).expanduser().resolve()
        source_uri = str(source_path)
        dedup_started = perf_counter()
        source_hash = calculate_sha256(source_path)
        skipped = should_skip_document(
            self._document_repository,
            collection_id=collection_id,
            source_path=source_uri,
            source_hash=source_hash,
            force=force,
        )
        dedup_duration_ms = max((perf_counter() - dedup_started) * 1000, 0.0)
        trace_context = TraceContext.ingestion(
            trace_id=trace_id,
            collection=collection_id,
            source_uri=source_uri,
            source_hash=source_hash,
            started_at=started_at,
        )
        trace_controller = TraceController(
            trace_context,
            sink=self._trace_sink,
            clock=self._clock,
        )
        trace_controller.record_stage(
            "dedup",
            duration_ms=dedup_duration_ms,
            input_summary={
                "source_uri": source_uri,
                "source_hash": source_hash,
                "force": force,
            },
            output_summary={"skipped": skipped},
            method="sha256",
            provider=type(self._document_repository).__name__,
            status="skipped" if skipped else "success",
            details={
                "successful_hash_hit": skipped,
                "skip_reason": (
                    "successful_source_hash_match" if skipped else None
                ),
            },
        )

        if skipped:
            finished_at = self._clock()
            total_duration_ms = max(
                (finished_at - started_at).total_seconds() * 1000,
                0.0,
            )
            summary = {
                "skipped": True,
                "skip_reason": "successful_source_hash_match",
                "loaded_documents": 0,
                "total_duration_ms": total_duration_ms,
            }
            trace_controller.flush_ingestion(
                status="skipped",
                document_status="skipped",
                chunk_count=0,
                embedded_count=0,
                skipped_count=1,
                index_ready=True,
            )
            return IngestionPipelineResult(
                trace_id=trace_id,
                collection_id=collection_id,
                source_uri=source_uri,
                source_hash=source_hash,
                status="skipped",
                document=None,
                trace_summary=summary,
            )

        try:
            load_started = perf_counter()
            document = self._loader.load(source_path)
            trace_controller.record_stage(
                "load",
                duration_ms=(perf_counter() - load_started) * 1000,
                input_summary={"source_uri": source_uri},
                output_summary={
                    "document_id": document.id,
                    "text_length": len(document.text),
                    "image_count": _document_image_count(document),
                },
                method="load",
                provider=type(self._loader).__name__,
            )
            if self._document_summarizer is not None:
                summary_started = perf_counter()
                document = self._document_summarizer.summarize(
                    document,
                    context={
                        "collection": collection_id,
                        "source_uri": source_uri,
                        "source_hash": source_hash,
                    },
                )
                trace_controller.record_stage(
                    "document_summary",
                    duration_ms=(perf_counter() - summary_started) * 1000,
                    input_summary={
                        "document_id": document.id,
                        "text_length": len(document.text),
                    },
                    output_summary={
                        "summary_present": document.summary is not None,
                        "summary_length": len(document.summary or ""),
                    },
                    method="llm_document_summary",
                    provider=type(self._document_summarizer).__name__,
                )

            if not self._complete_mode:
                trace_controller.flush_ingestion(
                    status="success",
                    document_status="success",
                    chunk_count=0,
                    embedded_count=0,
                    skipped_count=0,
                    index_ready=False,
                )
                return IngestionPipelineResult(
                    trace_id=trace_id,
                    collection_id=collection_id,
                    source_uri=source_uri,
                    source_hash=source_hash,
                    status="loaded",
                    document=document,
                    trace_summary={
                        "skipped": False,
                        "force": force,
                        "loaded_documents": 1,
                        "dedup_duration_ms": dedup_duration_ms,
                    },
                )

            assert self._splitter_step is not None
            assert self._transform_pipeline is not None
            split_started = perf_counter()
            chunks = self._splitter_step.run(document)
            trace_controller.record_stage(
                "split",
                duration_ms=(perf_counter() - split_started) * 1000,
                input_summary={"document_id": document.id},
                output_summary={
                    "chunk_count": len(chunks),
                    "chunk_ids": [chunk.id for chunk in chunks],
                },
                method="document_chunker",
                provider=type(self._splitter_step).__name__,
            )

            transform_context = {
                "trace_id": trace_id,
                "collection": collection_id,
                "document_id": document.id,
                "source_path": source_uri,
                "title": document.metadata.get("title"),
                "document_summary": document.summary,
                "document_images": document.metadata.get("images", []),
                "image_caption_artifacts": {},
            }
            transform_started = perf_counter()
            transform_sub_stages: list[dict[str, Any]] = []
            try:
                transformed_chunks = self._transform_pipeline.run(
                    chunks,
                    context=transform_context,
                    step_observer=transform_sub_stages.append,
                    snapshot_options=self._transform_snapshot_options,
                )
            except Exception as error:
                trace_controller.record_stage(
                    "transform",
                    duration_ms=(perf_counter() - transform_started) * 1000,
                    input_summary={"chunk_count": len(chunks)},
                    output_summary={"chunk_count": 0, "chunk_ids": []},
                    method="transform_pipeline",
                    provider=type(self._transform_pipeline).__name__,
                    sub_stages=transform_sub_stages,
                    status="failed",
                    error={
                        "error_type": type(error).__name__,
                        "message": str(error),
                    },
                )
                raise
            trace_controller.record_stage(
                "transform",
                duration_ms=(perf_counter() - transform_started) * 1000,
                input_summary={"chunk_count": len(chunks)},
                output_summary={
                    "chunk_count": len(transformed_chunks),
                    "chunk_ids": [chunk.id for chunk in transformed_chunks],
                },
                method="transform_pipeline",
                provider=type(self._transform_pipeline).__name__,
                sub_stages=transform_sub_stages,
            )

            indexing_result, upsert_result = self.run_indexing(
                document=document,
                chunks=transformed_chunks,
                collection_id=collection_id,
                source_path=source_uri,
                source_hash=source_hash,
                trace_controller=trace_controller,
                image_caption_artifacts=transform_context.get(
                    "image_caption_artifacts",
                    {},
                ),
            )
            self._document_repository.mark_success(document.id)
            trace_controller.flush_ingestion(
                status="success",
                document_status="success",
                chunk_count=len(transformed_chunks),
                embedded_count=len(indexing_result.dense_results),
                skipped_count=0,
                embedding_coverage=(
                    len(indexing_result.dense_results) / len(transformed_chunks)
                    if transformed_chunks
                    else 0
                ),
                index_ready=True,
            )
            return IngestionPipelineResult(
                trace_id=trace_id,
                collection_id=collection_id,
                source_uri=source_uri,
                source_hash=source_hash,
                status="indexed",
                document=document,
                chunks=tuple(transformed_chunks),
                indexing_result=indexing_result,
                upsert_result=upsert_result,
                trace_summary={
                    "skipped": False,
                    "force": force,
                    "loaded_documents": 1,
                    "chunk_count": len(transformed_chunks),
                    "dense_count": len(indexing_result.dense_results),
                    "bm25_chunk_count": indexing_result.bm25_index.chunk_count,
                    "image_count": len(upsert_result.image_ids),
                    "dedup_duration_ms": dedup_duration_ms,
                },
            )
        except Exception as error:
            if trace_controller.context.finished_at is None:
                trace_controller.flush_ingestion(
                    status="failed",
                    document_status="failed",
                    chunk_count=len(locals().get("transformed_chunks", [])),
                    embedded_count=0,
                    skipped_count=0,
                    error={
                        "error_type": type(error).__name__,
                        "message": str(error),
                    },
                    index_ready=False,
                )
            raise

    def run_indexing(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        collection_id: str,
        source_path: str,
        source_hash: str,
        trace_controller: TraceController | None = None,
        image_caption_artifacts: Mapping[str, Mapping[str, object]] | None = None,
    ) -> tuple[EmbeddingBatchResult, UpsertResult]:
        """Run the C8 indexing stage followed by the C9 persistence stage.

        Args:
            document: Canonical Loader output.
            chunks: Final ordered chunks after every configured transform.
            collection_id: Search collection receiving the source.
            source_path: Canonical source path used by deduplication.
            source_hash: SHA256 digest of original source bytes.
            trace_controller: Optional request trace controller used to record
                ``embed`` and ``upsert`` stages in the complete pipeline.
            image_caption_artifacts: Optional structured captions emitted by
                ImageCaptioner before downstream transforms rewrite chunk text.

        Returns:
            A tuple containing the in-memory indexing result and durable upsert
            result.

        Raises:
            RuntimeError: If the pipeline was constructed in Loader-only mode.
            IngestionError: If no searchable chunks remain, indexing fails, or
                transactional persistence fails.
        """

        if self._embedding_step is None or self._upsert_step is None:
            raise RuntimeError(
                "run_indexing() requires complete ingestion pipeline components"
            )
        if not chunks:
            raise IngestionError(
                "Cannot index a document without searchable chunks",
                context={
                    "operation": "ingestion_indexing",
                    "document_id": document.id,
                    "collection_id": collection_id,
                },
            )
        if self._chunk_repository is None:
            raise RuntimeError(
                "run_indexing() requires a chunk repository in complete mode"
            )
        embed_started = perf_counter()
        content_hashes = {
            sha256(chunk.text.encode("utf-8")).hexdigest() for chunk in chunks
        }
        try:
            existing_vectors = self._chunk_repository.get_dense_vectors_by_content_hashes(
                content_hashes,
                collection_id=collection_id,
            )
            indexing_result = self._embedding_step.run_batch(
                chunks,
                existing_vectors_by_hash=existing_vectors,
            )
        except Exception as error:
            _record_stage_best_effort(
                trace_controller,
                stage="embed",
                started_at=embed_started,
                input_summary={"chunk_count": len(chunks)},
                output_summary={"dense_count": 0, "bm25_chunk_count": 0},
                method="dense_and_bm25_batch",
                provider=type(self._embedding_step).__name__,
                status="failed",
                details={"error_type": type(error).__name__},
            )
            raise
        _record_stage_best_effort(
            trace_controller,
            stage="embed",
            started_at=embed_started,
            input_summary={
                "chunk_count": len(chunks),
                "content_hash_count": len(content_hashes),
            },
            output_summary={
                "dense_count": len(indexing_result.dense_results),
                "bm25_chunk_count": indexing_result.bm25_index.chunk_count,
                "reused_vector_count": len(existing_vectors),
            },
            method="dense_and_bm25_batch",
            provider=type(self._embedding_step).__name__,
            status="success",
            details={
                "dense_batches_processed": indexing_result.dense_batches_processed,
                "bm25_batches_processed": indexing_result.bm25_batches_processed,
                "dense_failure_count": len(indexing_result.dense_failures),
                "bm25_failure_count": len(indexing_result.bm25_failures),
            },
        )

        upsert_started = perf_counter()
        try:
            upsert_result = self._upsert_step.run(
                document=document,
                chunks=chunks,
                indexing_result=indexing_result,
                collection_id=collection_id,
                source_path=source_path,
                source_hash=source_hash,
                title=_optional_title(document.metadata.get("title")),
                image_caption_artifacts=image_caption_artifacts,
            )
        except Exception as error:
            _record_stage_best_effort(
                trace_controller,
                stage="upsert",
                started_at=upsert_started,
                input_summary={"chunk_count": len(chunks)},
                output_summary={},
                method="transactional_upsert",
                provider=type(self._upsert_step).__name__,
                status="failed",
                details={"error_type": type(error).__name__},
            )
            raise
        _record_stage_best_effort(
            trace_controller,
            stage="upsert",
            started_at=upsert_started,
            input_summary={"chunk_count": len(chunks)},
            output_summary={
                "document_id": upsert_result.document_id,
                "chunk_ids": list(upsert_result.chunk_ids),
                "vector_chunk_ids": list(upsert_result.vector_chunk_ids),
                "bm25_chunk_ids": list(upsert_result.bm25_chunk_ids),
                "image_ids": list(upsert_result.image_ids),
            },
            method="transactional_upsert",
            provider=type(self._upsert_step).__name__,
            status="success",
        )
        return indexing_result, upsert_result

    @property
    def _complete_mode(self) -> bool:
        """Return whether every post-Loader C10 component is configured."""

        return all(
            component is not None
            for component in (
                self._chunk_repository,
                self._splitter_step,
                self._transform_pipeline,
                self._embedding_step,
                self._upsert_step,
            )
        )


def _optional_title(value: Any) -> str | None:
    """Normalize optional document titles before persistence."""

    if value is None:
        return None
    title = str(value).strip()
    return title or None


def _document_image_count(document: Document) -> int:
    """Return the number of image metadata entries collected by the Loader."""

    images = document.metadata.get("images", [])
    return len(images) if isinstance(images, list) else 0


def _record_stage_best_effort(
    trace_controller: TraceController | None,
    *,
    stage: str,
    started_at: float,
    input_summary: dict[str, Any],
    output_summary: dict[str, Any],
    method: str,
    provider: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record one trace stage without letting observability replace business flow.

    Args:
        trace_controller: Optional request trace controller.
        stage: Stable ingestion stage name.
        started_at: ``perf_counter`` value captured before the stage.
        input_summary: Compact, trace-safe input summary.
        output_summary: Compact, trace-safe output summary.
        method: Logical algorithm or orchestration method.
        provider: Concrete component class name.
        status: Stage completion status.
        details: Optional diagnostic details.
    """

    if trace_controller is None:
        return
    try:
        trace_controller.record_stage(
            stage,
            duration_ms=(perf_counter() - started_at) * 1000,
            input_summary=input_summary,
            output_summary=output_summary,
            method=method,
            provider=provider,
            status=status,
            details=details,
        )
    except Exception:
        return
