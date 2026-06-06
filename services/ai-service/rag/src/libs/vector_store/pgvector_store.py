"""Persist and query dense vectors in the canonical PostgreSQL chunk table.

``PgVectorStore`` complements ``ChunkRepository`` instead of replacing it.
Ingestion first persists validated chunk content and lifecycle metadata through
the repository, then this adapter writes vectors to those existing rows.
Retrieval uses pgvector cosine distance and reconstructs local domain objects,
keeping SQL and PostgreSQL-specific vector syntax outside the query engine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from src.core.errors import ConfigurationError, DatabaseError
from src.core.types import Chunk, RetrievalResult
from src.libs.vector_store.base_vector_store import BaseVectorStore
from src.storage.postgres import PostgresPool


class PgVectorStore(BaseVectorStore):
    """Store and search chunk embeddings through an application-managed pool."""

    def __init__(
        self,
        *,
        pool: PostgresPool,
        chunk_table: str = "rag_chunks",
        collection_table: str = "rag_collections",
        document_table: str = "rag_documents",
        distance: str = "cosine",
        embedding_dimensions: int = 1536,
        **_: Any,
    ) -> None:
        """Configure table identifiers and the fixed vector schema.

        Args:
            pool: Open ``PostgresPool`` managed by application lifecycle.
            chunk_table: Table containing chunk rows and embeddings.
            collection_table: Collection table retained for config parity and
                future collection-management operations.
            document_table: Document table retained for config parity and future
                lifecycle-aware retrieval operations.
            distance: Distance strategy. B12 supports cosine only.
            embedding_dimensions: Fixed pgvector column dimensions.
            **_: Forward-compatible vector-store settings ignored in B12.

        Raises:
            ConfigurationError: If table identifiers, distance strategy, or
                dimensions are invalid.
        """

        for setting, identifier in {
            "chunk_table": chunk_table,
            "collection_table": collection_table,
            "document_table": document_table,
        }.items():
            if not identifier.isidentifier():
                raise ConfigurationError(
                    "PostgreSQL table name must be a simple identifier",
                    context={"setting": setting, "value": identifier},
                )
        if distance.lower() != "cosine":
            raise ConfigurationError(
                "PgVectorStore currently supports cosine distance only",
                context={"distance": distance},
            )
        if embedding_dimensions <= 0:
            raise ConfigurationError("PgVectorStore dimensions must be positive")

        self._pool = pool
        self._chunk_table = chunk_table
        self._collection_table = collection_table
        self._document_table = document_table
        self._embedding_dimensions = embedding_dimensions

    def upsert(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> list[str]:
        """Write vectors to existing chunk rows in one transaction.

        Args:
            chunks: Chunks already persisted by ``ChunkRepository``.
            vectors: Dense vectors aligned positionally with ``chunks``.

        Returns:
            Updated chunk IDs in input order.

        Raises:
            ValueError: If counts or vector dimensions are invalid.
            DatabaseError: If a chunk row is missing or PostgreSQL rejects the
                transaction.

        Side Effects:
            Updates ``embedding`` and ``updated_at`` for every supplied chunk.
            No chunk content or lifecycle metadata is inserted here.
        """

        if len(chunks) != len(vectors):
            raise ValueError("Chunk and vector counts must match")
        if not chunks:
            return []

        normalized = [self._vector_literal(vector) for vector in vectors]
        updated_ids: list[str] = []
        update_query = sql.SQL(
            """
            UPDATE {chunk_table}
            SET embedding = %s::vector,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING id
            """
        ).format(chunk_table=sql.Identifier(self._chunk_table))

        with self._pool.transaction() as connection:
            for chunk, vector_literal in zip(chunks, normalized, strict=True):
                row = connection.execute(
                    update_query,
                    (vector_literal, chunk.id),
                ).fetchone()
                if row is None:
                    raise DatabaseError(
                        "Cannot write embedding for a missing chunk",
                        context={
                            "operation": "pgvector_upsert",
                            "chunk_id": chunk.id,
                        },
                    )
                updated_ids.append(row[0])
        return updated_ids

    def search(
        self,
        vector: Sequence[float],
        *,
        filters: Mapping[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[RetrievalResult]:
        """Search chunk embeddings by cosine similarity and metadata filters.

        Args:
            vector: Dense query vector matching configured dimensions.
            filters: Optional exact metadata subset matched with JSONB
                containment.
            top_k: Positive maximum number of results.

        Returns:
            Retrieval results ordered by nearest cosine distance.

        Raises:
            ValueError: If ``top_k`` or vector dimensions are invalid.
            DatabaseError: If PostgreSQL query execution fails.
        """

        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        vector_literal = self._vector_literal(vector)
        clauses = [sql.SQL("embedding IS NOT NULL")]
        params: list[Any] = [vector_literal]
        if filters:
            clauses.append(sql.SQL("metadata @> %s"))
            params.append(Jsonb(dict(filters)))
        params.extend([vector_literal, top_k])

        query = sql.SQL(
            """
            SELECT
                id,
                content,
                1 - (embedding <=> %s::vector) AS score,
                metadata
            FROM {chunk_table}
            WHERE {where_clause}
            ORDER BY embedding <=> %s::vector, id ASC
            LIMIT %s
            """
        ).format(
            chunk_table=sql.Identifier(self._chunk_table),
            where_clause=sql.SQL(" AND ").join(clauses),
        )

        try:
            with self._pool.connection() as connection:
                rows = connection.execute(query, tuple(params)).fetchall()
        except DatabaseError:
            raise
        except Exception as error:
            raise DatabaseError(
                "Pgvector search failed",
                context={"operation": "pgvector_search"},
                cause=error,
            ) from error

        return [
            RetrievalResult(
                chunk_id=chunk_id,
                text=content,
                score=float(score),
                metadata=metadata,
            )
            for chunk_id, content, score, metadata in rows
        ]

    def get_by_ids(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        """Load existing chunks in caller-provided order.

        Args:
            chunk_ids: Ordered IDs returned by sparse retrieval.

        Returns:
            Existing chunks in requested relative order; missing IDs are
            skipped.

        Raises:
            DatabaseError: If PostgreSQL lookup fails.
        """

        requested_ids = list(chunk_ids)
        if not requested_ids:
            return []

        query = sql.SQL(
            """
            SELECT
                id,
                content,
                metadata,
                chunk_index,
                start_offset,
                end_offset,
                source_ref
            FROM {chunk_table}
            WHERE id = ANY(%s)
            """
        ).format(chunk_table=sql.Identifier(self._chunk_table))

        try:
            with self._pool.connection() as connection:
                rows = connection.execute(query, (requested_ids,)).fetchall()
        except DatabaseError:
            raise
        except Exception as error:
            raise DatabaseError(
                "Pgvector chunk lookup failed",
                context={"operation": "pgvector_get_by_ids"},
                cause=error,
            ) from error

        chunks_by_id = {
            row[0]: Chunk(
                id=row[0],
                text=row[1],
                metadata=row[2],
                chunk_index=row[3],
                start_offset=row[4],
                end_offset=row[5],
                source_ref=row[6],
            )
            for row in rows
        }
        return [
            chunks_by_id[chunk_id]
            for chunk_id in requested_ids
            if chunk_id in chunks_by_id
        ]

    def _vector_literal(self, vector: Sequence[float]) -> str:
        """Validate and serialize a vector for PostgreSQL's vector input parser.

        Args:
            vector: Numeric values expected to match the configured dimensions.

        Returns:
            A bracketed pgvector literal passed as a bound SQL parameter.

        Raises:
            ValueError: If dimensions differ or any value is non-finite.
        """

        values = [float(value) for value in vector]
        if len(values) != self._embedding_dimensions:
            raise ValueError(
                "Vector dimensions do not match PgVectorStore configuration"
            )
        if any(not isfinite(value) for value in values):
            raise ValueError("Vector values must be finite")
        return "[" + ",".join(format(value, ".17g") for value in values) + "]"
