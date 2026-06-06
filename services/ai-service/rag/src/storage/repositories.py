"""Persist and reconstruct the core Document and Chunk domain objects.

This module is the relational repository boundary between ingestion/business
code and the PostgreSQL schema. ``DocumentRepository`` owns collection-aware
document persistence, while ``ChunkRepository`` owns ordered chunk persistence
and content-hash calculation. Both repositories accept validated domain objects
and return the same provider-independent contracts defined in ``core.types``.

The repositories do not open database connections, initialize schema, create
embeddings, or implement document lifecycle state transitions. Callers provide
an open ``PostgresPool`` and coordinate higher-level pipeline transactions.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src.core.errors import DatabaseError
from src.core.types import Chunk, Document
from src.storage.postgres import PostgresPool


def _read_rows(
    pool: PostgresPool,
    *,
    operation: str,
    query: str,
    params: tuple[Any, ...],
    many: bool,
) -> Any:
    """Execute one read query with a consistent repository error boundary.

    Args:
        pool: Open connection pool used for the read.
        operation: Trace-safe operation name included in ``DatabaseError``.
        query: Parameterized SQL statement.
        params: Bound SQL parameters; credentials and raw SQL are not logged.
        many: Whether to return ``fetchall()`` instead of ``fetchone()``.

    Returns:
        One positional row, a list of positional rows, or ``None``.

    Raises:
        DatabaseError: If connection management or psycopg query execution
            fails. The original driver exception is retained as ``cause``.
    """

    try:
        with pool.connection() as connection:
            cursor = connection.execute(query, params)
            return cursor.fetchall() if many else cursor.fetchone()
    except DatabaseError:
        raise
    except psycopg.Error as error:
        raise DatabaseError(
            "PostgreSQL repository read failed",
            context={"operation": operation},
            cause=error,
        ) from error


class DocumentRepository:
    """Store canonical source documents within searchable collections.

    A document write automatically creates the collection when it does not yet
    exist. The generated collection uses its stable ID as its initial display
    name, allowing ingestion to persist the first document without a separate
    administrative setup step.
    """

    def __init__(self, pool: PostgresPool) -> None:
        """Bind the repository to an application-managed connection pool.

        Args:
            pool: Open PostgreSQL pool used for every repository transaction.
        """

        self._pool = pool

    def upsert(
        self,
        document: Document,
        *,
        collection_id: str,
        source_path: str,
        source_hash: str,
        title: str | None = None,
    ) -> Document:
        """Insert or replace one canonical document by its stable Python ID.

        Args:
            document: Validated domain document produced by a loader.
            collection_id: Stable collection receiving the document.
            source_path: Original path or URI used for source-level deduplication.
            source_hash: SHA256 digest of the original source bytes.
            title: Optional human-readable title for Dashboard list views.

        Returns:
            The same validated ``Document`` supplied by the caller.

        Raises:
            DatabaseError: If the pool is unavailable or PostgreSQL rejects the
                collection/document transaction.

        Side Effects:
            Creates the collection when absent and inserts or updates one row in
            ``rag_documents``.
        """

        with self._pool.transaction() as connection:
            self._ensure_collection(connection, collection_id)
            # Document IDs may include source_hash, so changed source content can
            # legitimately produce a new ID while retaining the same logical
            # collection/source_path identity. Removing the superseded version
            # first also activates schema cascades for stale chunks and images.
            connection.execute(
                """
                DELETE FROM rag_documents
                WHERE collection_id = %s
                  AND source_path = %s
                  AND id <> %s
                """,
                (collection_id, source_path, document.id),
            )
            connection.execute(
                """
                INSERT INTO rag_documents (
                    id,
                    collection_id,
                    source_path,
                    source_hash,
                    title,
                    content,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    collection_id = EXCLUDED.collection_id,
                    source_path = EXCLUDED.source_path,
                    source_hash = EXCLUDED.source_hash,
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    document.id,
                    collection_id,
                    source_path,
                    source_hash,
                    title,
                    document.text,
                    Jsonb(document.metadata),
                ),
            )
        return document

    def get_by_id(self, document_id: str) -> Document | None:
        """Load one document by stable ID.

        Args:
            document_id: Python-generated document identifier.

        Returns:
            Reconstructed ``Document`` or ``None`` when no row exists.

        Raises:
            DatabaseError: If connection acquisition or query execution fails.
        """

        row = _read_rows(
            self._pool,
            operation="document_get",
            query="""
            SELECT id, content, metadata
            FROM rag_documents
            WHERE id = %s
            """,
            params=(document_id,),
            many=False,
        )
        return self._to_document(row)

    def list_by_collection(self, collection_id: str) -> list[Document]:
        """List collection documents in deterministic source-path order.

        Args:
            collection_id: Stable collection identifier used by ingestion and
                retrieval filters.

        Returns:
            Documents ordered by ``source_path`` and stable document ID.

        Raises:
            DatabaseError: If connection acquisition or query execution fails.
        """

        rows = _read_rows(
            self._pool,
            operation="document_list_by_collection",
            query="""
            SELECT id, content, metadata
            FROM rag_documents
            WHERE collection_id = %s
            ORDER BY source_path ASC, id ASC
            """,
            params=(collection_id,),
            many=True,
        )
        return [self._to_document(row) for row in rows if row is not None]

    @staticmethod
    def _ensure_collection(connection: Any, collection_id: str) -> None:
        """Create a minimal collection record inside the caller transaction.

        Args:
            connection: Active psycopg connection owned by ``PostgresPool``.
            collection_id: Stable identifier also used as the initial name.

        Side Effects:
            Inserts one ``rag_collections`` row when the collection is absent.
        """

        connection.execute(
            """
            INSERT INTO rag_collections (id, name)
            VALUES (%s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (collection_id, collection_id),
        )

    @staticmethod
    def _to_document(row: tuple[Any, ...] | None) -> Document | None:
        """Convert one positional psycopg row into the shared domain contract.

        Args:
            row: ``(id, content, metadata)`` returned by PostgreSQL.

        Returns:
            Validated ``Document`` or ``None`` for a missing row.
        """

        if row is None:
            return None
        document_id, content, metadata = row
        return Document(id=document_id, text=content, metadata=metadata)


class ChunkRepository:
    """Store retrievable chunks while preserving document-relative ordering.

    A chunk's logical identity inside a document is ``chunk_index``. When
    transformed content changes and produces a new stable chunk ID, upsert
    replaces the row at that logical position so stale IDs cannot remain
    retrievable.
    """

    def __init__(self, pool: PostgresPool) -> None:
        """Bind the repository to an application-managed connection pool.

        Args:
            pool: Open PostgreSQL pool used for batch writes and reads.
        """

        self._pool = pool

    def upsert_many(
        self,
        chunks: list[Chunk],
        *,
        collection_id: str,
        document_id: str,
    ) -> list[Chunk]:
        """Persist an ordered chunk batch in one transaction.

        Args:
            chunks: Validated chunks belonging to one source document.
            collection_id: Collection containing the parent document.
            document_id: Existing parent document ID.

        Returns:
            A shallow list copy preserving the caller's input order.

        Raises:
            DatabaseError: If the document is absent, collection ownership does
                not match, or any batch statement fails. The transaction rolls
                back the complete batch.

        Side Effects:
            Inserts or replaces ``rag_chunks`` rows and computes each
            ``content_hash`` from UTF-8 chunk text.
        """

        persisted = list(chunks)
        if not persisted:
            return persisted
        chunk_ids = [chunk.id for chunk in persisted]
        chunk_indexes = [chunk.chunk_index for chunk in persisted]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("Chunk batch contains duplicate chunk IDs")
        if len(set(chunk_indexes)) != len(chunk_indexes):
            raise ValueError("Chunk batch contains duplicate chunk indexes")

        with self._pool.transaction() as connection:
            # Stable chunk IDs do not include chunk_index. Re-splitting can move
            # existing IDs to new positions, including swapping two positions.
            # Removing every incoming ID/index collision before insertion avoids
            # order-dependent primary-key failures while preserving one atomic
            # document-level batch update.
            connection.execute(
                """
                DELETE FROM rag_chunks
                WHERE document_id = %s
                  AND (
                      id = ANY(%s)
                      OR chunk_index = ANY(%s)
                  )
                """,
                (document_id, chunk_ids, chunk_indexes),
            )
            for chunk in persisted:
                heading_path = chunk.metadata.get("heading_path", [])
                connection.execute(
                    """
                    INSERT INTO rag_chunks (
                        id,
                        collection_id,
                        document_id,
                        chunk_index,
                        content,
                        content_hash,
                        start_offset,
                        end_offset,
                        source_ref,
                        heading_path,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        chunk.id,
                        collection_id,
                        document_id,
                        chunk.chunk_index,
                        chunk.text,
                        sha256(chunk.text.encode("utf-8")).hexdigest(),
                        chunk.start_offset,
                        chunk.end_offset,
                        Jsonb(chunk.source_ref) if chunk.source_ref is not None else None,
                        Jsonb(heading_path),
                        Jsonb(chunk.metadata),
                    ),
                )
        return persisted

    def get_by_id(self, chunk_id: str) -> Chunk | None:
        """Load one chunk by its current stable ID.

        Args:
            chunk_id: Python-generated chunk identifier.

        Returns:
            Reconstructed ``Chunk`` or ``None`` when the ID is absent or was
            replaced by a newer chunk at the same logical position.

        Raises:
            DatabaseError: If connection acquisition or query execution fails.
        """

        row = _read_rows(
            self._pool,
            operation="chunk_get",
            query="""
            SELECT
                id,
                content,
                metadata,
                chunk_index,
                start_offset,
                end_offset,
                source_ref
            FROM rag_chunks
            WHERE id = %s
            """,
            params=(chunk_id,),
            many=False,
        )
        return self._to_chunk(row)

    def list_by_document(self, document_id: str) -> list[Chunk]:
        """List all current chunks for a document in source order.

        Args:
            document_id: Stable parent document identifier.

        Returns:
            Chunks ordered by ``chunk_index`` and then ID for deterministic
            behavior even if corrupted legacy data bypasses the unique index.

        Raises:
            DatabaseError: If connection acquisition or query execution fails.
        """

        rows = _read_rows(
            self._pool,
            operation="chunk_list_by_document",
            query="""
            SELECT
                id,
                content,
                metadata,
                chunk_index,
                start_offset,
                end_offset,
                source_ref
            FROM rag_chunks
            WHERE document_id = %s
            ORDER BY chunk_index ASC, id ASC
            """,
            params=(document_id,),
            many=True,
        )
        return [self._to_chunk(row) for row in rows if row is not None]

    @staticmethod
    def _to_chunk(row: tuple[Any, ...] | None) -> Chunk | None:
        """Convert one positional psycopg row into the shared chunk contract.

        Args:
            row: Selected chunk columns in domain-constructor order.

        Returns:
            Validated ``Chunk`` or ``None`` for a missing row.
        """

        if row is None:
            return None
        (
            chunk_id,
            content,
            metadata,
            chunk_index,
            start_offset,
            end_offset,
            source_ref,
        ) = row
        return Chunk(
            id=chunk_id,
            text=content,
            metadata=metadata,
            chunk_index=chunk_index,
            start_offset=start_offset,
            end_offset=end_offset,
            source_ref=source_ref,
        )
