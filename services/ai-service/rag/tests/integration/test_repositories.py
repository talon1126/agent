"""Verify the PostgreSQL schema contract for durable RAG document storage.

These tests protect the storage schema milestones without coupling later
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
        FileNotFoundError: If the storage milestones have not provided the
            canonical schema file.
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
        The schema uses constraints without nested SQL expressions that
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

    required_tables = (
        "rag_collections",
        "rag_documents",
        "rag_chunks",
        "image_index",
        "rag_query_traces",
        "rag_ingestion_traces",
        "rag_evaluation_runs",
        "rag_evaluation_results",
    )
    for table_name in required_tables:
        _table_definition(sql, table_name)

    assert len(
        re.findall(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", sql, re.IGNORECASE)
    ) == len(required_tables)
    assert "CREATE INDEX IF NOT EXISTS idx_rag_documents_collection_id" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_documents_source_hash" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_chunks_document_id" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_chunks_content_hash" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding" in sql


@pytest.mark.integration
def test_image_index_preserves_source_and_quality_metadata() -> None:
    """Require durable image locations and multimodal processing metadata.

    Image files live outside PostgreSQL, so the database index must retain the
    stable image ID, source document relationship, collection, page, physical
    dimensions, MIME type, content hash, and caption quality status required by
    ImageStorage, Dashboard inspection, and multimodal response assembly.
    """

    sql = _schema_sql()
    images = _table_definition(sql, "image_index")

    for field_contract in (
        r"\bimage_id\s+TEXT\s+PRIMARY\s+KEY\b",
        r"\bfile_path\s+TEXT\s+NOT\s+NULL\b",
        r"\bcollection_id\s+TEXT\s+NOT\s+NULL\b",
        r"\bdocument_id\s+TEXT\s+NOT\s+NULL\b",
        r"\bdoc_hash\s+TEXT\s+NOT\s+NULL\b",
        r"\bpage_num\s+INTEGER\b",
        r"\bwidth\s+INTEGER\b",
        r"\bheight\s+INTEGER\b",
        r"\bmime_type\s+TEXT\b",
        r"\bimage_hash\s+TEXT\s+NOT\s+NULL\b",
        r"\bquality_status\s+TEXT\s+NOT\s+NULL\b",
    ):
        assert re.search(field_contract, images, re.IGNORECASE)

    assert "CREATE INDEX IF NOT EXISTS idx_collection ON image_index" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_doc_hash ON image_index" in sql


@pytest.mark.integration
def test_trace_tables_store_four_part_trace_contracts() -> None:
    """Require query and ingestion traces to persist the documented sections.

    TraceContext emits basic information, stage details, summary metrics, and
    evaluation metrics. Storing these sections independently lets Dashboard
    filter common columns without losing provider-specific structured details.
    """

    sql = _schema_sql()
    for table_name in ("rag_query_traces", "rag_ingestion_traces"):
        trace = _table_definition(sql, table_name)
        for field_contract in (
            r"\btrace_id\s+TEXT\s+PRIMARY\s+KEY\b",
            r"\bcollection_id\s+TEXT\s+NOT\s+NULL\b",
            r"\bstarted_at\s+TIMESTAMPTZ\s+NOT\s+NULL\b",
            r"\bfinished_at\s+TIMESTAMPTZ\b",
            r"\bstatus\s+TEXT\s+NOT\s+NULL\b",
            r"\bbasic_info\s+JSONB\s+NOT\s+NULL\b",
            r"\bstages\s+JSONB\s+NOT\s+NULL\b",
            r"\bsummary_metrics\s+JSONB\s+NOT\s+NULL\b",
            r"\bevaluation_metrics\s+JSONB\s+NOT\s+NULL\b",
            r"\berror\s+JSONB\b",
        ):
            assert re.search(field_contract, trace, re.IGNORECASE)

    query_trace = _table_definition(sql, "rag_query_traces")
    ingestion_trace = _table_definition(sql, "rag_ingestion_traces")
    assert re.search(r"\braw_query\s+TEXT\s+NOT\s+NULL\b", query_trace, re.IGNORECASE)
    assert re.search(
        r"\brequest_source\s+TEXT\s+NOT\s+NULL\b",
        query_trace,
        re.IGNORECASE,
    )
    assert re.search(
        r"\bsource_uri\s+TEXT\s+NOT\s+NULL\b",
        ingestion_trace,
        re.IGNORECASE,
    )
    assert re.search(
        r"\bsource_hash\s+TEXT\s+NOT\s+NULL\b",
        ingestion_trace,
        re.IGNORECASE,
    )


@pytest.mark.integration
def test_evaluation_results_belong_to_runs() -> None:
    """Require evaluation tasks and metric results to use a one-to-many model.

    One run may report retrieval and generation metrics. A separate result row
    per metric supports historical comparisons without rewriting a JSON blob,
    while the foreign key prevents orphaned evaluation observations.
    """

    sql = _schema_sql()
    runs = _table_definition(sql, "rag_evaluation_runs")
    results = _table_definition(sql, "rag_evaluation_results")

    for field_contract in (
        r"\bid\s+TEXT\s+PRIMARY\s+KEY\b",
        r"\bcollection_id\s+TEXT\s+NOT\s+NULL\b",
        r"\bevaluator\s+TEXT\s+NOT\s+NULL\b",
        r"\bstatus\s+TEXT\s+NOT\s+NULL\b",
        r"\bsettings_snapshot\s+JSONB\s+NOT\s+NULL\b",
    ):
        assert re.search(field_contract, runs, re.IGNORECASE)

    for field_contract in (
        r"\bid\s+TEXT\s+PRIMARY\s+KEY\b",
        r"\brun_id\s+TEXT\s+NOT\s+NULL\b",
        r"\bmetric_name\s+TEXT\s+NOT\s+NULL\b",
        r"\bmetric_value\s+DOUBLE\s+PRECISION\s+NOT\s+NULL\b",
        r"\bdetails\s+JSONB\s+NOT\s+NULL\b",
    ):
        assert re.search(field_contract, results, re.IGNORECASE)

    assert re.search(
        r"FOREIGN\s+KEY\s*\(\s*run_id\s*\)\s*"
        r"REFERENCES\s+rag_evaluation_runs\s*\(\s*id\s*\)",
        results,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"UNIQUE\s*\(\s*run_id\s*,\s*metric_name\s*\)",
        results,
        re.IGNORECASE,
    )
    assert re.search(
        r"metric_value\s+NOT\s+IN\s*\(\s*"
        r"'NaN'::DOUBLE\s+PRECISION\s*,\s*"
        r"'Infinity'::DOUBLE\s+PRECISION\s*,\s*"
        r"'-Infinity'::DOUBLE\s+PRECISION\s*\)",
        results,
        re.IGNORECASE | re.DOTALL,
    )


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
        Creates the storage extensions, tables, and indexes in the configured
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
