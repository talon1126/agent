"""Run the offline ingestion and indexing pipeline.

The pipeline starts with source SHA256 deduplication, then conditionally runs
Loader, business chunking, configured transforms, Dense/BM25 batch indexing,
and transactional persistence. The constructor keeps post-Loader components
optional so focused C1 unit tests and lightweight callers can still exercise the
legacy ``loaded`` boundary; production composition injects the complete C10
component set and receives an ``indexed`` result.

Skipped runs persist their completed ingestion trace immediately. Full-chain
stage tracing is introduced by the later observability phase, so C10 records
only summary counts and the existing dedup trace behavior.
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
from src.core.types import Chunk, Document
from src.ingestion.chunk import SplitterStep
from src.ingestion.embedding import EmbeddingBatchResult, EmbeddingStep
from src.ingestion.storage import UpsertResult, UpsertStep
from src.ingestion.transform import TransformPipeline
from src.libs.loader.base_loader import BaseLoader
from src.storage.repositories import (
    ChunkRepository,
    DocumentRepository,
    IngestionTraceRecord,
    TraceRepository,
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
        trace_repository: TraceRepository,
        splitter_step: SplitterStep | None = None,
        transform_pipeline: TransformPipeline | None = None,
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
            trace_repository: Durable ingestion trace persistence.
            splitter_step: Optional Document-to-Chunk business adapter.
            transform_pipeline: Optional ordered transform chain.
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
        self._trace_repository = trace_repository
        self._splitter_step = splitter_step
        self._transform_pipeline = transform_pipeline
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
            trace = IngestionTraceRecord(
                trace_id=trace_id,
                collection_id=collection_id,
                source_uri=source_uri,
                source_hash=source_hash,
                started_at=started_at,
                finished_at=finished_at,
                status="skipped",
                basic_info={
                    "trace_type": "ingestion",
                    "collection": collection_id,
                    "source_uri": source_uri,
                },
                stages=(
                    {
                        "stage": "dedup",
                        "method": "sha256",
                        "source_hash": source_hash,
                        "matched": True,
                        "skipped": True,
                        "reason": "successful_source_hash_match",
                        "duration_ms": dedup_duration_ms,
                    },
                ),
                summary_metrics=summary,
            )
            self._trace_repository.upsert_ingestion_trace(trace)
            return IngestionPipelineResult(
                trace_id=trace_id,
                collection_id=collection_id,
                source_uri=source_uri,
                source_hash=source_hash,
                status="skipped",
                document=None,
                trace_summary=summary,
            )

        document = self._loader.load(source_path)
        if not self._complete_mode:
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
        chunks = self._splitter_step.run(document)
        transform_context = {
            "trace_id": trace_id,
            "collection": collection_id,
            "document_id": document.id,
            "source_path": source_uri,
            "title": document.metadata.get("title"),
            "document_context": document.text,
        }
        transformed_chunks = self._transform_pipeline.run(
            chunks,
            context=transform_context,
        )
        indexing_result, upsert_result = self.run_indexing(
            document=document,
            chunks=transformed_chunks,
            collection_id=collection_id,
            source_path=source_uri,
            source_hash=source_hash,
        )
        self._document_repository.mark_success(document.id)
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

    def run_indexing(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        collection_id: str,
        source_path: str,
        source_hash: str,
    ) -> tuple[EmbeddingBatchResult, UpsertResult]:
        """Run the C8 indexing stage followed by the C9 persistence stage.

        Args:
            document: Canonical Loader output.
            chunks: Final ordered chunks after every configured transform.
            collection_id: Search collection receiving the source.
            source_path: Canonical source path used by deduplication.
            source_hash: SHA256 digest of original source bytes.

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
        content_hashes = {
            sha256(chunk.text.encode("utf-8")).hexdigest() for chunk in chunks
        }
        existing_vectors = self._chunk_repository.get_dense_vectors_by_content_hashes(
            content_hashes,
            collection_id=collection_id,
        )
        indexing_result = self._embedding_step.run_batch(
            chunks,
            existing_vectors_by_hash=existing_vectors,
        )
        upsert_result = self._upsert_step.run(
            document=document,
            chunks=chunks,
            indexing_result=indexing_result,
            collection_id=collection_id,
            source_path=source_path,
            source_hash=source_hash,
            title=_optional_title(document.metadata.get("title")),
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
