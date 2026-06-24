"""Read indexed documents, chunks, images, and index status for Dashboard.

``DataBrowserService`` is a read-only SQL projection layer. It deliberately
returns compact immutable DTOs instead of domain objects so Dashboard pages can
show lifecycle state, source paths, index readiness, image metadata, and chunk
details without duplicating table joins.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg

from src.core.errors import DatabaseError
from src.storage.image_storage import ImageStorage
from src.storage.postgres import PostgresPool


@dataclass(frozen=True, slots=True)
class CollectionStats:
    """Summarize indexed assets for one collection."""

    collection_id: str
    document_count: int
    chunk_count: int
    image_count: int
    dense_indexed_chunk_count: int
    bm25_indexed_chunk_count: int


@dataclass(frozen=True, slots=True)
class DocumentBrowserRow:
    """Represent one document row in the Dashboard data browser."""

    document_id: str
    collection_id: str
    title: str | None
    source_path: str
    source_hash: str
    lifecycle_status: str
    chunk_count: int
    image_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)
    summary: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ChunkBrowserRow:
    """Represent one chunk row with retrieval-index readiness details."""

    chunk_id: str
    document_id: str
    collection_id: str
    chunk_index: int
    text: str
    text_preview: str
    text_length: int
    content_hash: str
    start_offset: int
    end_offset: int
    dense_indexed: bool
    bm25_term_count: int
    image_refs: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImageBrowserRow:
    """Represent one image index row suitable for Dashboard display."""

    image_id: str
    file_path: str
    collection_id: str
    document_id: str
    page_num: int | None
    width: int | None
    height: int | None
    mime_type: str | None
    quality_status: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DataBrowserService:
    """Provide read-only Dashboard queries over PostgreSQL RAG assets."""

    def __init__(
        self,
        pool: PostgresPool,
        *,
        image_storage: ImageStorage | None = None,
    ) -> None:
        """Bind the service to storage dependencies.

        Args:
            pool: Open PostgreSQL pool used for document and chunk projections.
            image_storage: Optional image-index reader. ``None`` creates the
                default ``ImageStorage`` with its standard root path.
        """

        self._pool = pool
        self._image_storage = image_storage or ImageStorage(pool)

    def collection_stats(self, collection_id: str) -> CollectionStats:
        """Return aggregate document, chunk, image, and index counts."""

        _validate_non_blank(collection_id, field_name="collection_id")
        row = self._fetch_one(
            operation="dashboard_collection_stats",
            query="""
            SELECT
                (
                    SELECT COUNT(*)
                    FROM rag_documents
                    WHERE collection_id = %s
                ) AS document_count,
                (
                    SELECT COUNT(*)
                    FROM rag_chunks
                    WHERE collection_id = %s
                ) AS chunk_count,
                (
                    SELECT COUNT(*)
                    FROM image_index
                    WHERE collection_id = %s
                ) AS image_count,
                (
                    SELECT COUNT(*)
                    FROM rag_chunks
                    WHERE collection_id = %s
                      AND embedding IS NOT NULL
                ) AS dense_indexed_chunk_count,
                (
                    SELECT COUNT(DISTINCT chunk_id)
                    FROM rag_bm25_terms
                    WHERE collection_id = %s
                ) AS bm25_indexed_chunk_count
            """,
            params=(
                collection_id,
                collection_id,
                collection_id,
                collection_id,
                collection_id,
            ),
        )
        assert row is not None
        return CollectionStats(
            collection_id=collection_id,
            document_count=int(row[0]),
            chunk_count=int(row[1]),
            image_count=int(row[2]),
            dense_indexed_chunk_count=int(row[3]),
            bm25_indexed_chunk_count=int(row[4]),
        )

    def list_documents(self, collection_id: str) -> list[DocumentBrowserRow]:
        """List documents in one collection with child asset counts."""

        _validate_non_blank(collection_id, field_name="collection_id")
        rows = self._fetch_all(
            operation="dashboard_document_list",
            query="""
            SELECT
                document.id,
                document.collection_id,
                document.title,
                document.summary,
                document.source_path,
                document.source_hash,
                document.lifecycle_status,
                document.metadata,
                document.created_at,
                document.updated_at,
                COUNT(DISTINCT chunk.id) AS chunk_count,
                COUNT(DISTINCT image.image_id) AS image_count
            FROM rag_documents AS document
            LEFT JOIN rag_chunks AS chunk
              ON chunk.document_id = document.id
             AND chunk.collection_id = document.collection_id
            LEFT JOIN image_index AS image
              ON image.document_id = document.id
             AND image.collection_id = document.collection_id
            WHERE document.collection_id = %s
            GROUP BY
                document.id,
                document.collection_id,
                document.title,
                document.summary,
                document.source_path,
                document.source_hash,
                document.lifecycle_status,
                document.metadata,
                document.created_at,
                document.updated_at
            ORDER BY document.updated_at DESC, document.created_at DESC, document.id ASC
            """,
            params=(collection_id,),
        )
        return [self._to_document_row(row) for row in rows]

    def list_chunks(self, document_id: str) -> list[ChunkBrowserRow]:
        """List chunks for one document in source order."""

        _validate_non_blank(document_id, field_name="document_id")
        rows = self._fetch_all(
            operation="dashboard_chunk_list",
            query=f"""
            {self._chunk_projection()}
            WHERE chunk.document_id = %s
            GROUP BY chunk.id
            ORDER BY chunk.chunk_index ASC, chunk.id ASC
            """,
            params=(document_id,),
        )
        return [self._to_chunk_row(row) for row in rows]

    def get_chunk_detail(self, chunk_id: str) -> ChunkBrowserRow | None:
        """Load one chunk detail row by stable chunk ID."""

        _validate_non_blank(chunk_id, field_name="chunk_id")
        row = self._fetch_one(
            operation="dashboard_chunk_detail",
            query=f"""
            {self._chunk_projection()}
            WHERE chunk.id = %s
            GROUP BY chunk.id
            """,
            params=(chunk_id,),
        )
        return self._to_chunk_row(row) if row is not None else None

    def list_images(self, collection_id: str) -> list[ImageBrowserRow]:
        """List image index rows for one collection."""

        _validate_non_blank(collection_id, field_name="collection_id")
        return [
            ImageBrowserRow(
                image_id=record.image_id,
                file_path=record.file_path,
                collection_id=record.collection_id,
                document_id=record.document_id,
                page_num=record.page_num,
                width=record.width,
                height=record.height,
                mime_type=record.mime_type,
                quality_status=record.quality_status,
                metadata=dict(record.metadata),
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in sorted(
                self._image_storage.find_by_collection(collection_id),
                key=lambda item: _optional_timestamp(item.updated_at, item.created_at),
                reverse=True,
            )
        ]

    def _fetch_one(
        self,
        *,
        operation: str,
        query: str,
        params: tuple[Any, ...],
    ) -> tuple[Any, ...] | None:
        """Execute one Dashboard read query and return a single row."""

        return self._execute_read(
            operation=operation,
            query=query,
            params=params,
            many=False,
        )

    def _fetch_all(
        self,
        *,
        operation: str,
        query: str,
        params: tuple[Any, ...],
    ) -> list[tuple[Any, ...]]:
        """Execute one Dashboard read query and return every row."""

        rows = self._execute_read(
            operation=operation,
            query=query,
            params=params,
            many=True,
        )
        return list(rows)

    def _execute_read(
        self,
        *,
        operation: str,
        query: str,
        params: tuple[Any, ...],
        many: bool,
    ) -> Any:
        """Run a SQL read through the shared Dashboard error boundary."""

        try:
            with self._pool.connection() as connection:
                cursor = connection.execute(query, params)
                return cursor.fetchall() if many else cursor.fetchone()
        except DatabaseError:
            raise
        except psycopg.Error as error:
            raise DatabaseError(
                "PostgreSQL Dashboard read failed",
                context={"operation": operation},
                cause=error,
            ) from error

    @staticmethod
    def _chunk_projection() -> str:
        """Return the shared chunk detail projection with BM25 readiness."""

        return """
            SELECT
                chunk.id,
                chunk.document_id,
                chunk.collection_id,
                chunk.chunk_index,
                chunk.content,
                chunk.content_hash,
                chunk.start_offset,
                chunk.end_offset,
                chunk.embedding IS NOT NULL AS dense_indexed,
                COUNT(DISTINCT bm25.term) AS bm25_term_count,
                chunk.metadata
            FROM rag_chunks AS chunk
            LEFT JOIN rag_bm25_terms AS bm25
              ON bm25.chunk_id = chunk.id
        """

    @staticmethod
    def _to_document_row(row: tuple[Any, ...]) -> DocumentBrowserRow:
        """Convert a document projection row into a Dashboard DTO."""

        return DocumentBrowserRow(
            document_id=row[0],
            collection_id=row[1],
            title=row[2],
            summary=row[3],
            source_path=row[4],
            source_hash=row[5],
            lifecycle_status=row[6],
            metadata=dict(row[7] or {}),
            created_at=row[8],
            updated_at=row[9],
            chunk_count=int(row[10]),
            image_count=int(row[11]),
        )

    @staticmethod
    def _to_chunk_row(row: tuple[Any, ...]) -> ChunkBrowserRow:
        """Convert a chunk projection row into a Dashboard DTO."""

        metadata = dict(row[10] or {})
        text = str(row[4])
        return ChunkBrowserRow(
            chunk_id=row[0],
            document_id=row[1],
            collection_id=row[2],
            chunk_index=row[3],
            text=text,
            text_preview=_preview(text),
            text_length=len(text),
            content_hash=row[5],
            start_offset=row[6],
            end_offset=row[7],
            dense_indexed=bool(row[8]),
            bm25_term_count=int(row[9]),
            image_refs=_image_refs(metadata),
            metadata=metadata,
        )


def _validate_non_blank(value: str, *, field_name: str) -> None:
    """Reject blank Dashboard query identities before database access."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _preview(text: str, *, limit: int = 160) -> str:
    """Return a compact single-line text preview for table views."""

    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else f"{normalized[: limit - 3]}..."


def _image_refs(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract stable image references from chunk metadata."""

    refs = metadata.get("image_refs", [])
    if not isinstance(refs, list | tuple):
        return ()
    normalized_refs = []
    for ref in refs:
        if ref is None:
            continue
        normalized_ref = str(ref).strip()
        if normalized_ref:
            normalized_refs.append(normalized_ref)
    return tuple(normalized_refs)


def _optional_timestamp(*values: datetime | None) -> datetime:
    """Return the first available timestamp for deterministic newest-first sort.

    Args:
        *values: Candidate timestamps ordered by preference.

    Returns:
        The first non-``None`` value, or an aware minimum timestamp so rows with
        missing time data sort last instead of raising a comparison error.
    """

    for value in values:
        if value is not None:
            return value
    return datetime.min.replace(tzinfo=UTC)
