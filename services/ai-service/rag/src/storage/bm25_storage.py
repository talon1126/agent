"""Persist BM25 posting statistics for sparse retrieval.

``BM25Storage`` translates the in-memory ``BM25IndexResult`` produced during
ingestion into relational posting rows. Each document write replaces the
document's complete sparse snapshot, preventing terms from removed or rewritten
chunks from remaining searchable.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import psycopg

from src.core.bm25_analyzer import BM25Candidate, normalize_bm25_keywords
from src.core.errors import DatabaseError
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
    """Store document postings and execute collection-scoped sparse queries."""

    def __init__(
        self,
        pool: PostgresPool,
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        """Bind sparse storage to PostgreSQL and configure BM25 scoring.

        Args:
            pool: Open application PostgreSQL pool.
            k1: Term-frequency saturation factor shared with ``BM25Indexer``.
            b: Document-length normalization factor shared with
                ``BM25Indexer``.

        Raises:
            ValueError: If either scoring parameter is outside the supported
                BM25 range.
        """

        if k1 <= 0:
            raise ValueError("BM25 k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("BM25 b must be between 0 and 1")

        self._pool = pool
        self._k1 = k1
        self._b = b

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

    def query(
        self,
        keywords: list[str] | str,
        *,
        top_k: int,
        collection: str | None = None,
    ) -> list[BM25Candidate]:
        """Rank persisted sparse postings for one knowledge collection.

        Args:
            keywords: Processed query keywords or raw text. The same analyzer
                used during ingestion expands CJK terms and normalizes English.
            top_k: Positive maximum number of sparse candidates.
            collection: Required collection identifier. Persisted indexes are
                collection-scoped so unrelated knowledge cannot influence
                corpus statistics or ranking.

        Returns:
            BM25 candidates ordered by descending score and stable chunk ID.
            Empty terms or an empty collection return an empty list.

        Raises:
            ValueError: If ``top_k`` is invalid or collection is absent/blank.
            DatabaseError: If PostgreSQL cannot read the inverted index.

        Notes:
            Corpus size, average document length, and per-term document
            frequency are calculated from current PostgreSQL rows rather than
            trusting document-local values captured during ingestion. This
            keeps scoring correct after multiple documents enter a collection.
        """

        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k must be positive")
        if not isinstance(collection, str) or not collection.strip():
            raise ValueError("collection must be a non-blank string")

        terms = normalize_bm25_keywords(keywords)
        if not terms:
            return []

        try:
            with self._pool.connection() as connection:
                rows = connection.execute(
                    """
                    WITH query_input AS (
                        SELECT
                            %s::TEXT AS collection_id,
                            %s::TEXT[] AS terms
                    ),
                    corpus AS (
                        SELECT DISTINCT posting.chunk_id, posting.document_length
                        FROM rag_bm25_terms AS posting
                        CROSS JOIN query_input
                        WHERE posting.collection_id = query_input.collection_id
                    ),
                    corpus_stats AS (
                        SELECT
                            COUNT(*)::BIGINT AS chunk_count,
                            COALESCE(AVG(document_length), 0)::DOUBLE PRECISION
                                AS average_document_length
                        FROM corpus
                    ),
                    term_stats AS (
                        SELECT
                            posting.term,
                            COUNT(DISTINCT posting.chunk_id)::BIGINT
                                AS document_frequency
                        FROM rag_bm25_terms AS posting
                        CROSS JOIN query_input
                        WHERE posting.collection_id = query_input.collection_id
                          AND posting.term = ANY(query_input.terms)
                        GROUP BY posting.term
                    )
                    SELECT
                        posting.chunk_id,
                        posting.term,
                        posting.term_frequency,
                        posting.document_length,
                        corpus_stats.chunk_count,
                        corpus_stats.average_document_length,
                        term_stats.document_frequency
                    FROM rag_bm25_terms AS posting
                    JOIN term_stats ON term_stats.term = posting.term
                    CROSS JOIN corpus_stats
                    CROSS JOIN query_input
                    WHERE posting.collection_id = query_input.collection_id
                      AND posting.term = ANY(query_input.terms)
                    ORDER BY posting.chunk_id ASC, posting.term ASC
                    """,
                    (collection.strip(), terms),
                ).fetchall()
        except DatabaseError:
            raise
        except psycopg.Error as error:
            raise DatabaseError(
                "PostgreSQL BM25 query failed",
                context={
                    "operation": "bm25_query",
                    "collection": collection.strip(),
                },
                cause=error,
            ) from error

        scores: dict[str, float] = defaultdict(float)
        for (
            chunk_id,
            _term,
            term_frequency,
            document_length,
            chunk_count,
            average_document_length,
            document_frequency,
        ) in rows:
            if chunk_count <= 0 or average_document_length <= 0:
                continue
            idf = math.log(
                1
                + (
                    chunk_count
                    - document_frequency
                    + 0.5
                )
                / (document_frequency + 0.5)
            )
            denominator = term_frequency + self._k1 * (
                1
                - self._b
                + self._b * document_length / average_document_length
            )
            scores[chunk_id] += (
                idf
                * (term_frequency * (self._k1 + 1))
                / denominator
            )

        ranked = sorted(
            (
                BM25Candidate(chunk_id=chunk_id, score=score)
                for chunk_id, score in scores.items()
                if score > 0
            ),
            key=lambda candidate: (-candidate.score, candidate.chunk_id),
        )
        return ranked[:top_k]

    async def async_query(
        self,
        keywords: list[str] | str,
        *,
        top_k: int,
        collection: str | None = None,
    ) -> list[BM25Candidate]:
        """Rank persisted BM25 postings without blocking event-loop callers.

        Args:
            keywords: Processed query keywords or raw text.
            top_k: Positive maximum number of sparse candidates.
            collection: Required collection identifier.

        Returns:
            BM25 candidates ordered exactly like ``query()``.

        Raises:
            ValueError: If ``top_k`` or collection is invalid.
            DatabaseError: If PostgreSQL cannot read the inverted index.
        """

        return await asyncio.to_thread(
            self.query,
            keywords,
            top_k=top_k,
            collection=collection,
        )
