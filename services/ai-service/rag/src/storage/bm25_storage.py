"""Persist BM25 posting statistics for sparse retrieval.

``BM25Storage`` translates the in-memory ``BM25IndexResult`` produced during
ingestion into relational posting rows. Each document write replaces the
document's complete sparse snapshot, preventing terms from removed or rewritten
chunks from remaining searchable.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from src.storage.postgres import PostgresPool

if TYPE_CHECKING:
    from src.ingestion.embedding import BM25IndexResult


class _BM25IndexData(Protocol):
    """Describe the sparse statistics required by the storage adapter."""

    document_lengths: dict[str, int]
    term_frequencies: dict[str, dict[str, int]]
    term_document_frequency: dict[str, int]
    average_document_length: float


@dataclass(frozen=True, slots=True)
class BM25TermRecord:
    """Represent one persisted term/chunk posting."""

    collection_id: str
    document_id: str
    chunk_id: str
    term: str
    term_frequency: int
    document_frequency: int
    document_length: int
    average_document_length: float


class BM25Storage:
    """Store and inspect document-scoped BM25 posting snapshots."""

    def __init__(self, pool: PostgresPool) -> None:
        """Bind sparse storage to the application PostgreSQL pool."""

        self._pool = pool

    def upsert_index(
        self,
        index: BM25IndexResult | _BM25IndexData,
        *,
        collection_id: str,
        document_id: str,
        connection: Any | None = None,
    ) -> list[str]:
        """Replace one document's complete BM25 posting snapshot.

        Args:
            index: In-memory term statistics generated from the same ordered
                chunks being persisted by the ingestion transaction.
            collection_id: Collection owning every indexed chunk.
            document_id: Source document owning every indexed chunk.
            connection: Optional active PostgreSQL transaction supplied by
                ``UpsertStep``. When omitted, this method opens its own
                transaction for standalone use.

        Returns:
            Indexed chunk IDs in the insertion order retained by
            ``BM25IndexResult.document_lengths``.

        Side Effects:
            Deletes stale postings for the document and inserts the supplied
            complete snapshot.
        """

        manager = (
            nullcontext(connection)
            if connection is not None
            else self._pool.transaction()
        )
        with manager as active_connection:
            active_connection.execute(
                "DELETE FROM rag_bm25_terms WHERE document_id = %s",
                (document_id,),
            )
            for chunk_id in index.document_lengths:
                term_frequencies = index.term_frequencies.get(chunk_id, {})
                for term, term_frequency in term_frequencies.items():
                    active_connection.execute(
                        """
                        INSERT INTO rag_bm25_terms (
                            collection_id,
                            document_id,
                            chunk_id,
                            term,
                            term_frequency,
                            document_frequency,
                            document_length,
                            average_document_length
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (chunk_id, term) DO UPDATE SET
                            collection_id = EXCLUDED.collection_id,
                            document_id = EXCLUDED.document_id,
                            term_frequency = EXCLUDED.term_frequency,
                            document_frequency = EXCLUDED.document_frequency,
                            document_length = EXCLUDED.document_length,
                            average_document_length =
                                EXCLUDED.average_document_length,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            collection_id,
                            document_id,
                            chunk_id,
                            term,
                            term_frequency,
                            index.term_document_frequency[term],
                            index.document_lengths[chunk_id],
                            index.average_document_length,
                        ),
                    )
        return list(index.document_lengths)

    def list_by_document(self, document_id: str) -> list[BM25TermRecord]:
        """Return one document's postings in deterministic chunk/term order."""

        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    collection_id,
                    document_id,
                    chunk_id,
                    term,
                    term_frequency,
                    document_frequency,
                    document_length,
                    average_document_length
                FROM rag_bm25_terms
                WHERE document_id = %s
                ORDER BY chunk_id ASC, term ASC
                """,
                (document_id,),
            ).fetchall()
        return [BM25TermRecord(*row) for row in rows]
