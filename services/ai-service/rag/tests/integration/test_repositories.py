"""Verify the PostgreSQL schema contract for durable RAG document storage.

These tests protect the first storage milestone without coupling later
repository implementations to a specific SQL client. Static contract tests run
in every development environment. The opt-in database test executes the same
schema against PostgreSQL when ``DATABASE_URL`` is available, allowing local
development and CI to verify pgvector compatibility and idempotent DDL.

Failures usually indicate that the schema no longer matches Python domain IDs,
that required integrity constraints were removed, or that migration-safe
``IF NOT EXISTS`` guards were omitted.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = RAG_ROOT / "src" / "storage" / "schema.sql"


def _schema_sql() -> str:
    """Read the canonical storage schema used by initialization code.

    Returns:
        The complete schema text encoded as UTF-8.

    Raises:
        FileNotFoundError: If B1 has not provided the canonical schema file.
    """

    return SCHEMA_PATH.read_text(encoding="utf-8")


def _table_definition(sql: str, table_name: str) -> str:
    """Extract one ``CREATE TABLE`` body for focused contract assertions.

    Args:
        sql: Complete schema SQL.
        table_name: Unquoted PostgreSQL table name to locate.

    Returns:
        The text inside the table's outer parentheses.

    Raises:
        AssertionError: If the requested table is absent from the schema.

    Notes:
        The B1 schema uses constraints without nested SQL expressions that
        require a full PostgreSQL parser. Database syntax is separately covered
        by the opt-in execution test.
    """

    pattern = re.compile(
        rf"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{re.escape(table_name)}\s*"
        r"\((.*?)\);",
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(sql)
    assert match is not None, f"Missing CREATE TABLE for {table_name}"
    return match.group(1)


@pytest.mark.integration
def test_core_schema_uses_python_domain_ids_as_primary_keys() -> None:
    """Require collection, document, and chunk IDs to remain stable strings.

    Python creates ``Document.id`` and ``Chunk.id`` before persistence.
    PostgreSQL must store those values directly instead of introducing serial
    IDs that require repository-level identity translation.
    """

    sql = _schema_sql()

    for table_name in ("rag_collections", "rag_documents", "rag_chunks"):
        table = _table_definition(sql, table_name)
        assert re.search(r"\bid\s+TEXT\s+PRIMARY\s+KEY\b", table, re.IGNORECASE)
        assert "BIGSERIAL" not in table.upper()

    documents = _table_definition(sql, "rag_documents")
    chunks = _table_definition(sql, "rag_chunks")
    assert re.search(r"\bcollection_id\s+TEXT\s+NOT\s+NULL\b", documents, re.IGNORECASE)
    assert re.search(r"\bdocument_id\s+TEXT\s+NOT\s+NULL\b", chunks, re.IGNORECASE)


@pytest.mark.integration
def test_core_schema_enables_pgvector_and_preserves_domain_fields() -> None:
    """Require pgvector and fields needed to reconstruct domain objects.

    Storage must retain chunk ordering, source offsets, source references,
    extensible metadata, content hashes, and the configured 1536-dimensional
    embedding produced by ``text-embedding-3-small``.
    """

    sql = _schema_sql()
    chunks = _table_definition(sql, "rag_chunks")

    assert re.search(
        r"CREATE\s+EXTENSION\s+IF\s+NOT\s+EXISTS\s+vector\s*;",
        sql,
        re.IGNORECASE,
    )
    for field_contract in (
        r"\bchunk_index\s+INTEGER\s+NOT\s+NULL\b",
        r"\bcontent\s+TEXT\s+NOT\s+NULL\b",
        r"\bcontent_hash\s+TEXT\s+NOT\s+NULL\b",
        r"\bstart_offset\s+INTEGER\s+NOT\s+NULL\b",
        r"\bend_offset\s+INTEGER\s+NOT\s+NULL\b",
        r"\bsource_ref\s+JSONB\b",
        r"\bmetadata\s+JSONB\s+NOT\s+NULL\b",
        r"\bembedding\s+vector\(1536\)",
    ):
        assert re.search(field_contract, chunks, re.IGNORECASE)

    assert re.search(
        r"CHECK\s*\(\s*start_offset\s*>=\s*0\s+AND\s+"
        r"end_offset\s*>\s*start_offset\s*\)",
        chunks,
        re.IGNORECASE,
    )


@pytest.mark.integration
def test_core_schema_declares_idempotent_tables_and_indexes() -> None:
    """Require repeatable DDL and lookup indexes used by later repositories.

    Schema initialization runs during deployment and local setup, so every
    table and index must be guarded by ``IF NOT EXISTS``. The lookup indexes
    support document deduplication, ordered chunk reads, and embedding search.
    """

    sql = _schema_sql()

    assert len(
        re.findall(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", sql, re.IGNORECASE)
    ) == 3
    assert "CREATE INDEX IF NOT EXISTS idx_rag_documents_collection_id" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_documents_source_hash" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_chunks_document_id" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_chunks_content_hash" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding" in sql


@pytest.mark.integration
def test_chunk_collection_must_match_its_document_collection() -> None:
    """Require chunk collection metadata to agree with its parent document.

    Both IDs are useful for filtering, but independent foreign keys would allow
    a chunk to reference a valid document from one collection and a different
    valid collection. A composite foreign key prevents that silent retrieval
    boundary violation at the persistence layer.
    """

    sql = _schema_sql()
    documents = _table_definition(sql, "rag_documents")
    chunks = _table_definition(sql, "rag_chunks")

    assert re.search(
        r"UNIQUE\s*\(\s*id\s*,\s*collection_id\s*\)",
        documents,
        re.IGNORECASE,
    )
    assert re.search(
        r"FOREIGN\s+KEY\s*\(\s*document_id\s*,\s*collection_id\s*\)\s*"
        r"REFERENCES\s+rag_documents\s*\(\s*id\s*,\s*collection_id\s*\)",
        chunks,
        re.IGNORECASE | re.DOTALL,
    )


@pytest.mark.integration
def test_core_schema_executes_twice_when_database_is_available() -> None:
    """Execute the schema twice against an explicitly configured PostgreSQL.

    The test is skipped when ``DATABASE_URL`` is absent because it must never
    guess credentials or silently connect to a developer database. CI and local
    integration environments should provide a disposable database with the
    pgvector extension available.

    Raises:
        psycopg.Error: If PostgreSQL rejects the schema or pgvector is missing.

    Side Effects:
        Creates the B1 extension, tables, and indexes in the configured
        PostgreSQL database. Re-execution verifies DDL idempotency.
    """

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL schema execution")

    import psycopg

    sql = _schema_sql()
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(sql)
        connection.execute(sql)
