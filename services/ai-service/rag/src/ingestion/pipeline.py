"""Start the offline ingestion pipeline with source-level deduplication.

C1 implements the mandatory first pipeline boundary: canonicalize the source
path, hash original file bytes, query successful document lifecycle state, and
stop before Loader work when the source is unchanged. Later Phase C tasks extend
``IngestionPipeline.run()`` after the Loader call with splitting, transforms,
encoding, and index upserts.

The module writes a completed ingestion trace for skipped runs because no later
pipeline stage will finalize them. Non-skipped runs remain in process-local
results until later tasks introduce full-chain TraceContext orchestration.
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
from src.core.types import Document
from src.libs.loader.base_loader import BaseLoader
from src.storage.repositories import (
    DocumentRepository,
    IngestionTraceRecord,
    TraceRepository,
)

DEFAULT_HASH_BLOCK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class IngestionRunResult:
    """Describe the result of the currently implemented pipeline boundary.

    Attributes:
        trace_id: Stable identifier reserved for the complete ingestion run.
        collection_id: Collection receiving the source.
        source_uri: Canonical absolute source path used for deduplication.
        source_hash: SHA256 digest of original source bytes.
        status: ``skipped`` for an unchanged successful source or ``loaded``
            when C1 allowed the source to enter Loader.
        document: Loader output for non-skipped runs; otherwise ``None``.
        trace_summary: Trace-safe deduplication outcome and duration metrics.
    """

    trace_id: str
    collection_id: str
    source_uri: str
    source_hash: str
    status: Literal["skipped", "loaded"]
    document: Document | None
    trace_summary: Mapping[str, Any]

    def __post_init__(self) -> None:
        """Freeze the summary mapping to prevent post-run trace mutation."""

        object.__setattr__(
            self,
            "trace_summary",
            MappingProxyType(dict(self.trace_summary)),
        )


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
    """Coordinate source deduplication and the current Loader boundary."""

    def __init__(
        self,
        *,
        loader: BaseLoader,
        document_repository: DocumentRepository,
        trace_repository: TraceRepository,
        trace_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Inject source, persistence, and deterministic test dependencies.

        Args:
            loader: Source adapter invoked only after deduplication allows work.
            document_repository: Durable successful-source lookup.
            trace_repository: Durable ingestion trace persistence.
            trace_id_factory: Optional stable-ID factory for deterministic tests.
            clock: Optional timezone-aware clock for deterministic trace times.
        """

        self._loader = loader
        self._document_repository = document_repository
        self._trace_repository = trace_repository
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
        """Run source hashing and stop or continue at the Loader boundary.

        Args:
            source: Local file path to ingest.
            collection_id: Target knowledge collection.
            force: Bypass successful-source deduplication when true.

        Returns:
            ``IngestionRunResult`` containing either a skipped result or the
            normalized ``Document`` returned by Loader.

        Raises:
            ValueError: If ``collection_id`` is blank.
            IngestionError: If source hashing fails.
            DatabaseError: If deduplication or skipped-trace persistence fails.
            Exception: Loader-specific failures are preserved for the Loader
                adapter to classify according to its contract.

        Side Effects:
            Reads the original source, queries PostgreSQL, calls Loader only
            when required, and writes one completed trace for skipped runs.
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
            return IngestionRunResult(
                trace_id=trace_id,
                collection_id=collection_id,
                source_uri=source_uri,
                source_hash=source_hash,
                status="skipped",
                document=None,
                trace_summary=summary,
            )

        document = self._loader.load(source_path)
        return IngestionRunResult(
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
