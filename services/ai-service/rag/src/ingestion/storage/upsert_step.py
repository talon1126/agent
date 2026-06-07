"""Persist one complete indexed document snapshot in a single transaction.

``UpsertStep`` is the C9 boundary between in-memory ingestion output and
PostgreSQL. It validates that Chunk, Dense, and BM25 identities describe the
same ordered snapshot, copies source images into managed collection storage,
then atomically writes the document, chunks, vectors, sparse postings, and
image index rows.
"""

from __future__ import annotations

import mimetypes
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.core.errors import IngestionError
from src.core.types import Chunk, Document
from src.ingestion.embedding import DenseEncodingResult, EmbeddingBatchResult
from src.storage.bm25_storage import BM25Storage
from src.storage.image_storage import ImageStorage
from src.storage.postgres import PostgresPool
from src.storage.repositories import ChunkRepository, DocumentRepository


class _TransactionalVectorStore(Protocol):
    """Describe the pgvector operation required by the C9 transaction."""

    def upsert_in_transaction(
        self,
        connection: object,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> list[str]:
        """Write aligned vectors through a caller-owned transaction."""


class UpsertResult(BaseModel):
    """Summarize durable IDs written by one C9 ingestion transaction."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    chunk_ids: list[str] = Field(default_factory=list)
    vector_chunk_ids: list[str] = Field(default_factory=list)
    bm25_chunk_ids: list[str] = Field(default_factory=list)
    image_ids: list[str] = Field(default_factory=list)


class _PreparedImage(BaseModel):
    """Carry validated managed-image data into the database transaction."""

    model_config = ConfigDict(extra="forbid")

    image_id: str
    file_path: str
    suffix: str
    image_hash: str
    page_num: int | None = None
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None
    quality_status: str
    metadata: dict[str, object] = Field(default_factory=dict)
    previous_content: bytes | None = None


class UpsertStep:
    """Write one complete document indexing snapshot atomically."""

    def __init__(
        self,
        *,
        pool: PostgresPool,
        document_repository: DocumentRepository,
        chunk_repository: ChunkRepository,
        vector_store: _TransactionalVectorStore,
        bm25_storage: BM25Storage,
        image_storage: ImageStorage,
    ) -> None:
        """Configure transaction-aware storage dependencies.

        Args:
            pool: PostgreSQL pool that owns the unified transaction.
            document_repository: Canonical document persistence adapter.
            chunk_repository: Ordered chunk persistence adapter.
            vector_store: Transaction-aware pgvector adapter.
            bm25_storage: Sparse posting persistence adapter.
            image_storage: Managed image file and index adapter.
        """

        self._pool = pool
        self._document_repository = document_repository
        self._chunk_repository = chunk_repository
        self._vector_store = vector_store
        self._bm25_storage = bm25_storage
        self._image_storage = image_storage

    def run(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        indexing_result: EmbeddingBatchResult,
        collection_id: str,
        source_path: str,
        source_hash: str,
        title: str | None = None,
    ) -> UpsertResult:
        """Persist one validated complete ingestion snapshot.

        Args:
            document: Canonical loader output.
            chunks: Complete ordered chunk snapshot after transforms.
            indexing_result: C8 Dense and BM25 output for exactly ``chunks``.
            collection_id: Search collection receiving the document.
            source_path: Stable logical source path.
            source_hash: SHA256 digest of the original source bytes.
            title: Optional Dashboard display title.

        Returns:
            Ordered durable identifiers for every written subsystem.

        Raises:
            IngestionError: If C8 reports failures, identities or hashes do not
                align, image files are unreadable, or persistence fails.

        Side Effects:
            Copies referenced source images into managed collection storage and
            commits one PostgreSQL transaction containing document, chunks,
            vectors, BM25 postings, and image index rows.
        """

        ordered_chunks = list(chunks)
        dense_by_id = self._validate_indexing_snapshot(
            ordered_chunks,
            indexing_result=indexing_result,
        )
        try:
            prepared_images = self._prepare_images(
                document,
                chunks=ordered_chunks,
                collection_id=collection_id,
            )
            with self._pool.transaction() as connection:
                self._document_repository.upsert_in_transaction(
                    connection,
                    document,
                    collection_id=collection_id,
                    source_path=source_path,
                    source_hash=source_hash,
                    title=title,
                )
                persisted_chunks = self._chunk_repository.upsert_many_in_transaction(
                    connection,
                    ordered_chunks,
                    collection_id=collection_id,
                    document_id=document.id,
                    replace_document=True,
                )
                vector_ids = self._vector_store.upsert_in_transaction(
                    connection,
                    persisted_chunks,
                    [dense_by_id[chunk.id].vector for chunk in persisted_chunks],
                )
                bm25_ids = self._bm25_storage.upsert_index(
                    indexing_result.bm25_index,
                    collection_id=collection_id,
                    document_id=document.id,
                    connection=connection,
                )
                connection.execute(
                    "DELETE FROM image_index WHERE document_id = %s",
                    (document.id,),
                )
                image_ids = [
                    self._image_storage.upsert_index_in_transaction(
                        connection,
                        image_id=image.image_id,
                        file_path=image.file_path,
                        collection_id=collection_id,
                        document_id=document.id,
                        doc_hash=source_hash,
                        image_hash=image.image_hash,
                        page_num=image.page_num,
                        width=image.width,
                        height=image.height,
                        mime_type=image.mime_type,
                        quality_status=image.quality_status,
                        metadata=image.metadata,
                    ).image_id
                    for image in prepared_images
                ]
        except IngestionError:
            if "prepared_images" in locals():
                self._restore_images(prepared_images, collection_id=collection_id)
            raise
        except Exception as error:
            if "prepared_images" in locals():
                self._restore_images(prepared_images, collection_id=collection_id)
            raise IngestionError(
                "Unable to persist ingestion snapshot",
                context={
                    "operation": "ingestion_upsert",
                    "collection_id": collection_id,
                    "document_id": document.id,
                },
                cause=error,
            ) from error

        return UpsertResult(
            document_id=document.id,
            chunk_ids=[chunk.id for chunk in persisted_chunks],
            vector_chunk_ids=vector_ids,
            bm25_chunk_ids=bm25_ids,
            image_ids=image_ids,
        )

    @staticmethod
    def _validate_indexing_snapshot(
        chunks: list[Chunk],
        *,
        indexing_result: EmbeddingBatchResult,
    ) -> dict[str, DenseEncodingResult]:
        """Validate C8 output before any filesystem or database side effect."""

        if indexing_result.dense_failures or indexing_result.bm25_failures:
            raise IngestionError(
                "Cannot persist an indexing snapshot with failed items",
                context={
                    "dense_failure_count": len(indexing_result.dense_failures),
                    "bm25_failure_count": len(indexing_result.bm25_failures),
                },
            )

        chunk_ids = [chunk.id for chunk in chunks]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise IngestionError("Cannot persist duplicate chunk IDs")

        dense_by_id = {result.chunk_id: result for result in indexing_result.dense_results}
        if list(dense_by_id) != chunk_ids:
            raise IngestionError(
                "Dense results must match chunk order and identity",
                context={
                    "chunk_ids": chunk_ids,
                    "dense_chunk_ids": list(dense_by_id),
                },
            )
        bm25_chunk_ids = list(indexing_result.bm25_index.document_lengths)
        if bm25_chunk_ids != chunk_ids:
            raise IngestionError(
                "BM25 results must match chunk order and identity",
                context={
                    "chunk_ids": chunk_ids,
                    "bm25_chunk_ids": bm25_chunk_ids,
                },
            )

        for chunk in chunks:
            expected_hash = sha256(chunk.text.encode("utf-8")).hexdigest()
            if dense_by_id[chunk.id].content_hash != expected_hash:
                raise IngestionError(
                    "Dense content hash does not match chunk text",
                    context={"chunk_id": chunk.id},
                )
        return dense_by_id

    def _prepare_images(
        self,
        document: Document,
        *,
        chunks: list[Chunk],
        collection_id: str,
    ) -> list[_PreparedImage]:
        """Copy document images into managed storage and enrich index metadata."""

        caption_by_image = _captions_by_image(chunks)
        prepared: list[_PreparedImage] = []
        images = document.metadata.get("images", [])
        image_ids = [str(image["id"]) for image in images]
        if len(set(image_ids)) != len(image_ids):
            raise IngestionError(
                "Document metadata contains duplicate image IDs",
                context={"document_id": document.id, "image_ids": image_ids},
            )

        try:
            for image in images:
                source = Path(image["path"]).expanduser().resolve()
                content = source.read_bytes()
                suffix = source.suffix or ".bin"
                target = self._image_storage.image_path(
                    collection_id,
                    image["id"],
                    suffix=suffix,
                )
                previous_content = target.read_bytes() if target.is_file() else None
                managed_path = self._image_storage.save_image(
                    collection_id,
                    image["id"],
                    content,
                    suffix=suffix,
                )
                position = dict(image.get("position", {}))
                caption = caption_by_image.get(image["id"], {})
                caption_status = str(caption.get("status") or "pending")
                prepared.append(
                    _PreparedImage(
                        image_id=image["id"],
                        file_path=str(managed_path),
                        suffix=suffix,
                        image_hash=sha256(content).hexdigest(),
                        page_num=image.get("page"),
                        width=_positive_int(position.get("width")),
                        height=_positive_int(position.get("height")),
                        mime_type=mimetypes.guess_type(managed_path.name)[0],
                        quality_status=_image_quality_status(caption_status),
                        metadata={
                            "position": position,
                            "caption": str(caption.get("description") or ""),
                            "caption_status": caption_status,
                            "source_path": str(source),
                        },
                        previous_content=previous_content,
                    )
                )
        except OSError as error:
            self._restore_images(prepared, collection_id=collection_id)
            raise IngestionError(
                "Unable to read or persist source image",
                context={
                    "operation": "image_prepare",
                    "document_id": document.id,
                },
                cause=error,
            ) from error
        except Exception:
            self._restore_images(prepared, collection_id=collection_id)
            raise
        return prepared

    def _restore_images(
        self,
        images: list[_PreparedImage],
        *,
        collection_id: str,
    ) -> None:
        """Best-effort restore managed files after a failed database write."""

        for image in reversed(images):
            try:
                if image.previous_content is None:
                    Path(image.file_path).unlink(missing_ok=True)
                else:
                    self._image_storage.save_image(
                        collection_id,
                        image.image_id,
                        image.previous_content,
                        suffix=image.suffix,
                    )
            except Exception:
                # Preserve the original ingestion/database exception. A later
                # cleanup task can remove or repair the orphaned file.
                continue


def _captions_by_image(chunks: list[Chunk]) -> dict[str, dict[str, object]]:
    """Collect the latest structured caption for each referenced image."""

    captions: dict[str, dict[str, object]] = {}
    for chunk in chunks:
        for caption in chunk.metadata.get("image_captions", []):
            if not isinstance(caption, dict):
                continue
            image_id = str(caption.get("image_id") or "")
            if image_id:
                captions[image_id] = dict(caption)
    return captions


def _positive_int(value: object) -> int | None:
    """Return a positive integer or ``None`` for unknown dimensions."""

    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _image_quality_status(caption_status: str) -> str:
    """Map caption pipeline states to the image-index schema vocabulary."""

    normalized = caption_status.lower()
    if normalized == "success":
        return "ok"
    if normalized in {"low_quality", "skipped", "failed", "pending"}:
        return normalized
    return "pending"
