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
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = RAG_ROOT / "src" / "storage" / "schema.sql"
sys.path.insert(0, str(RAG_ROOT))


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


def _database_settings(
    *,
    url_env: str = "DATABASE_URL",
    pool_size: int = 5,
    timezone: str = "Asia/Shanghai",
) -> object:
    """Build the minimum typed-settings shape consumed by PostgresPool.

    Args:
        url_env: Environment-variable name containing the PostgreSQL DSN.
        pool_size: Maximum configured pool size.

    Returns:
        A validated ``DatabaseSettings`` instance used by the storage adapter.
    """

    from src.core.config import DatabaseSettings

    return DatabaseSettings(
        provider="postgresql",
        url_env=url_env,
        pool_size=pool_size,
        timezone=timezone,
        echo_sql=False,
    )


@pytest.mark.integration
def test_postgres_pool_is_created_from_settings_without_opening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require config-driven lazy pool construction without credential leakage.

    Pool construction must resolve the environment variable named by settings,
    use the configured maximum size, and avoid network I/O until ``open()`` is
    explicitly called by the service lifecycle.
    """

    from src.storage import postgres

    driver_pool = MagicMock()
    pool_class = MagicMock(return_value=driver_pool)
    monkeypatch.setattr(postgres, "ConnectionPool", pool_class)

    pool = postgres.PostgresPool.from_settings(
        _database_settings(pool_size=7),
        environ={"DATABASE_URL": "postgresql://user:secret@db:5432/rag"},
    )

    pool_class.assert_called_once()
    assert pool_class.call_args.args == ("postgresql://user:secret@db:5432/rag",)
    assert pool_class.call_args.kwargs["min_size"] == 1
    assert pool_class.call_args.kwargs["max_size"] == 7
    assert pool_class.call_args.kwargs["open"] is False
    assert pool_class.call_args.kwargs["name"] == "aimodel-rag"
    assert callable(pool_class.call_args.kwargs["configure"])
    assert pool.is_open is False
    assert "secret" not in repr(pool)


@pytest.mark.integration
def test_postgres_pool_configures_session_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require every pooled connection to display timestamps in Beijing time.

    PostgreSQL ``TIMESTAMPTZ`` stores absolute instants, but Dashboard and CLI
    reads should present database timestamps through the configured session
    timezone. A failure means new connections will keep the server default,
    which is commonly ``Etc/UTC`` in Docker images.
    """

    from src.storage import postgres

    driver_pool = MagicMock()
    pool_class = MagicMock(return_value=driver_pool)
    connection = MagicMock()
    monkeypatch.setattr(postgres, "ConnectionPool", pool_class)

    postgres.PostgresPool.from_settings(
        _database_settings(timezone="Asia/Shanghai"),
        environ={"DATABASE_URL": "postgresql://user:secret@db:5432/rag"},
    )

    configure = pool_class.call_args.kwargs["configure"]
    configure(connection)

    connection.execute.assert_called_once_with("SET TIME ZONE 'Asia/Shanghai'")
    connection.commit.assert_called_once_with()


@pytest.mark.integration
def test_postgres_pool_rejects_unsafe_session_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject timezone values that cannot be safely rendered in SET TIME ZONE."""

    from src.core.errors import ConfigurationError
    from src.storage import postgres

    driver_pool = MagicMock()
    pool_class = MagicMock(return_value=driver_pool)
    connection = MagicMock()
    monkeypatch.setattr(postgres, "ConnectionPool", pool_class)

    postgres.PostgresPool.from_settings(
        _database_settings(timezone="Asia/Shanghai'; DROP TABLE rag_chunks; --"),
        environ={"DATABASE_URL": "postgresql://user:secret@db:5432/rag"},
    )

    configure = pool_class.call_args.kwargs["configure"]
    with pytest.raises(ConfigurationError, match="timezone"):
        configure(connection)
    connection.execute.assert_not_called()
    connection.commit.assert_not_called()


@pytest.mark.integration
def test_postgres_pool_rejects_missing_database_url() -> None:
    """Require a configuration error before constructing a driver pool.

    Missing or whitespace-only DSNs are startup configuration failures, not
    retryable database failures. The error may name the environment variable
    but must not include unrelated environment values.
    """

    from src.core.errors import ConfigurationError
    from src.storage.postgres import PostgresPool

    with pytest.raises(ConfigurationError, match="DATABASE_URL") as captured:
        PostgresPool.from_settings(_database_settings(), environ={})

    assert captured.value.context == {"environment_variable": "DATABASE_URL"}


@pytest.mark.integration
def test_postgres_pool_wraps_open_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require driver startup failures to become trace-safe DatabaseError.

    The original exception remains available as ``cause`` for diagnostics,
    while the public message and context avoid exposing the configured DSN.
    """

    from src.core.errors import DatabaseError
    from src.storage import postgres

    driver_pool = MagicMock()
    driver_error = RuntimeError("driver connection failed")
    driver_pool.open.side_effect = driver_error
    monkeypatch.setattr(postgres, "ConnectionPool", MagicMock(return_value=driver_pool))
    pool = postgres.PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": "postgresql://user:secret@db:5432/rag"},
    )

    with pytest.raises(DatabaseError, match="open PostgreSQL connection pool") as captured:
        pool.open()

    assert captured.value.cause is driver_error
    assert captured.value.context == {"operation": "pool_open"}
    assert "secret" not in str(captured.value)
    driver_pool.close.assert_called_once_with()
    assert pool.is_open is False


@pytest.mark.integration
def test_postgres_pool_keeps_open_state_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require failed shutdown to remain retryable instead of hiding a leak.

    If the driver cannot close its workers or connections, the wrapper must
    retain ``is_open=True``. Marking the pool closed would make subsequent
    cleanup calls no-ops while resources may still be active.
    """

    from src.core.errors import DatabaseError
    from src.storage import postgres

    driver_pool = MagicMock()
    driver_pool.close.side_effect = RuntimeError("driver close failed")
    monkeypatch.setattr(postgres, "ConnectionPool", MagicMock(return_value=driver_pool))
    pool = postgres.PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": "postgresql://user:secret@db:5432/rag"},
    )
    pool.open()

    with pytest.raises(DatabaseError, match="close PostgreSQL connection pool"):
        pool.close()

    assert pool.is_open is True


@pytest.mark.integration
def test_postgres_pool_lifecycle_and_schema_initialization() -> None:
    """Exercise pool, health check, transaction, and idempotent schema setup.

    The test uses the disposable PostgreSQL selected by ``DATABASE_URL``. It
    proves the service lifecycle can open the pool, execute schema initialization
    twice, borrow connections, commit a transaction, and close cleanly.
    """

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL pool integration")

    from src.storage.postgres import PostgresPool, init_schema

    pool = PostgresPool.from_settings(
        _database_settings(pool_size=3),
        environ={"DATABASE_URL": database_url},
    )
    try:
        pool.open()
        assert pool.is_open is True
        assert pool.health_check() is True

        init_schema(pool)
        init_schema(pool)

        with pool.transaction() as connection:
            cursor = connection.execute("SELECT to_regclass('public.rag_chunks') IS NOT NULL")
            assert cursor.fetchone() == (True,)
    finally:
        pool.close()

    assert pool.is_open is False


@pytest.mark.integration
def test_init_schema_reports_missing_file_without_opening_credentials(
    tmp_path: Path,
) -> None:
    """Require a clear DatabaseError when the configured schema file is absent.

    File failures happen before any SQL is executed. Diagnostics should include
    only the schema path and operation, preserving the original filesystem
    exception without exposing the pool's connection string.
    """

    from src.core.errors import DatabaseError
    from src.storage.postgres import PostgresPool, init_schema

    pool = PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": "postgresql://user:secret@db:5432/rag"},
    )
    missing_path = tmp_path / "missing-schema.sql"

    with pytest.raises(DatabaseError, match="read PostgreSQL schema") as captured:
        init_schema(pool, schema_path=missing_path)

    assert isinstance(captured.value.cause, FileNotFoundError)
    assert captured.value.context == {
        "operation": "schema_read",
        "schema_path": str(missing_path),
    }
    assert "secret" not in str(captured.value)


@pytest.mark.integration
def test_init_schema_wraps_sql_failure_and_rolls_back(
    tmp_path: Path,
) -> None:
    """Require failed schema execution to roll back and retain the driver cause.

    The fixture creates a probe table and then executes invalid SQL in the same
    transaction. A correct implementation reports ``DatabaseError`` and leaves
    no probe table behind, proving initialization cannot persist a partial
    schema.
    """

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL rollback integration")

    import psycopg

    from src.core.errors import DatabaseError
    from src.storage.postgres import PostgresPool, init_schema

    schema_path = tmp_path / "invalid-schema.sql"
    schema_path.write_text(
        "CREATE TABLE b3_rollback_probe (id INTEGER); SELECT b3_missing_function();",
        encoding="utf-8",
    )
    pool = PostgresPool.from_settings(
        _database_settings(pool_size=2),
        environ={"DATABASE_URL": database_url},
    )
    try:
        pool.open()
        with pytest.raises(DatabaseError, match="initialize PostgreSQL schema") as captured:
            init_schema(pool, schema_path=schema_path)

        assert isinstance(captured.value.cause, psycopg.Error)
        with pool.connection() as connection:
            cursor = connection.execute("SELECT to_regclass('public.b3_rollback_probe') IS NULL")
            assert cursor.fetchone() == (True,)
    finally:
        pool.close()


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
def test_core_schema_adds_collection_profile_embedding_cache() -> None:
    """Intent Router semantic profiles should be cached in PostgreSQL."""

    sql = _schema_sql()
    profiles = _table_definition(sql, "rag_collection_profiles")

    for field_contract in (
        r"\bid\s+TEXT\s+PRIMARY\s+KEY\b",
        r"\bcollection\s+TEXT\s+NOT\s+NULL\b",
        r"\bprofile_name\s+TEXT\s+NOT\s+NULL\b",
        r"\bprofile_text\s+TEXT\s+NOT\s+NULL\b",
        r"\bcontent_hash\s+TEXT\s+NOT\s+NULL\b",
        r"\bembedding\s+vector\(1536\)",
        r"\bprovider\s+TEXT\b",
        r"\bmodel\s+TEXT\b",
    ):
        assert re.search(field_contract, profiles, re.IGNORECASE)
    assert "UNIQUE (collection, profile_name)" in profiles
    assert "idx_rag_collection_profiles_collection" in sql


def test_core_schema_enables_pgvector_and_preserves_domain_fields() -> None:
    """Require pgvector and fields needed to reconstruct domain objects.

    Storage must retain chunk ordering, source offsets, metadata-owned source
    fields, content hashes, and the configured 1536-dimensional embedding
    produced by the selected provider.
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
        r"\bmetadata\s+JSONB\s+NOT\s+NULL\b",
        r"\bembedding\s+vector\(1536\)",
    ):
        assert re.search(field_contract, chunks, re.IGNORECASE)

    assert "heading_path" not in chunks
    assert "DROP COLUMN IF EXISTS heading_path" in sql

    assert re.search(
        r"CHECK\s*\(\s*start_offset\s*>=\s*0\s+AND\s+"
        r"end_offset\s*>\s*start_offset\s*\)",
        chunks,
        re.IGNORECASE,
    )


@pytest.mark.integration
def test_document_schema_exposes_summary_as_first_class_field() -> None:
    """Require document summaries to live outside metadata JSON.

    Document summaries are consumed by rewrite prompts, MCP metadata tools, and
    Dashboard browsing. Keeping them in a first-class column avoids parsing
    arbitrary metadata and lets repositories reconstruct ``Document.summary``
    directly.
    """

    sql = _schema_sql()
    documents = _table_definition(sql, "rag_documents")

    assert re.search(r"\bsummary\s+TEXT\b", documents, re.IGNORECASE)
    assert "metadata->'summary'" not in sql


@pytest.mark.integration
def test_document_schema_exposes_lifecycle_status_for_filtering() -> None:
    """Require a first-class lifecycle field for retrieval-visible filtering.

    Lifecycle state must not live only inside JSON metadata because retrieval,
    Dashboard list views, and cleanup logic need indexed access to
    ``success``, ``failed``, and ``deleted`` states.
    """

    sql = _schema_sql()
    documents = _table_definition(sql, "rag_documents")

    assert re.search(
        r"\blifecycle_status\s+TEXT\s+NOT\s+NULL\s+DEFAULT\s+'pending'",
        documents,
        re.IGNORECASE,
    )
    assert re.search(
        r"CHECK\s*\(\s*lifecycle_status\s+IN\s*\(\s*'pending'\s*,\s*"
        r"'processing'\s*,\s*'success'\s*,\s*'failed'\s*,\s*'deleted'\s*\)\s*\)",
        documents,
        re.IGNORECASE | re.DOTALL,
    )
    assert "CREATE INDEX IF NOT EXISTS idx_rag_documents_lifecycle_status" in sql


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
        "rag_bm25_terms",
        "rag_collection_profiles",
        "image_index",
        "rag_query_traces",
        "rag_ingestion_traces",
        "rag_evaluation_runs",
        "rag_evaluation_results",
        "rag_evaluation_sample_results",
    )
    for table_name in required_tables:
        _table_definition(sql, table_name)

    assert len(re.findall(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", sql, re.IGNORECASE)) == len(
        required_tables
    )
    assert "CREATE INDEX IF NOT EXISTS idx_rag_documents_collection_id" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_documents_source_hash" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_chunks_document_id" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_chunks_content_hash" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_bm25_terms_collection_term" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_rag_bm25_terms_document" in sql


@pytest.mark.integration
def test_bm25_schema_persists_sparse_posting_statistics() -> None:
    """Require term/chunk postings needed for query-time BM25 scoring."""

    sql = _schema_sql()
    terms = _table_definition(sql, "rag_bm25_terms")

    for field_contract in (
        r"\bcollection_id\s+TEXT\s+NOT\s+NULL\b",
        r"\bdocument_id\s+TEXT\s+NOT\s+NULL\b",
        r"\bchunk_id\s+TEXT\s+NOT\s+NULL\b",
        r"\bterm\s+TEXT\s+NOT\s+NULL\b",
        r"\bterm_frequency\s+INTEGER\s+NOT\s+NULL\b",
        r"\bdocument_frequency\s+INTEGER\s+NOT\s+NULL\b",
        r"\bdocument_length\s+INTEGER\s+NOT\s+NULL\b",
        r"\baverage_document_length\s+DOUBLE\s+PRECISION\s+NOT\s+NULL\b",
    ):
        assert re.search(field_contract, terms, re.IGNORECASE)

    assert re.search(
        r"PRIMARY\s+KEY\s*\(\s*chunk_id\s*,\s*term\s*\)",
        terms,
        re.IGNORECASE,
    )
    assert "REFERENCES rag_chunks(id)" in terms


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
        r"\bquery_result\s+JSONB\s+NOT\s+NULL\s+DEFAULT\s+'\{\}'::jsonb\b",
        query_trace,
        re.IGNORECASE,
    )
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
    sample_results = _table_definition(sql, "rag_evaluation_sample_results")

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

    for field_contract in (
        r"\bid\s+TEXT\s+PRIMARY\s+KEY\b",
        r"\brun_id\s+TEXT\s+NOT\s+NULL\b",
        r"\bsample_id\s+TEXT\s+NOT\s+NULL\b",
        r"\bsample_index\s+INTEGER\s+NOT\s+NULL\b",
        r"\bcollection_id\s+TEXT\b",
        r"\bquestion\s+TEXT\s+NOT\s+NULL\b",
        r"\bgolden_answer\s+TEXT\s+NOT\s+NULL\b",
        r"\bgenerated_answer\s+TEXT\s+NOT\s+NULL\b",
        r"\bretrieved_contexts\s+JSONB\s+NOT\s+NULL\b",
        r"\bcontext_chunk_ids\s+JSONB\s+NOT\s+NULL\b",
        r"\bquery_trace_ids\s+JSONB\s+NOT\s+NULL\b",
        r"\bmetrics\s+JSONB\s+NOT\s+NULL\b",
        r"\berror\s+JSONB\b",
    ):
        assert re.search(field_contract, sample_results, re.IGNORECASE)

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
    assert re.search(
        r"FOREIGN\s+KEY\s*\(\s*run_id\s*\)\s*"
        r"REFERENCES\s+rag_evaluation_runs\s*\(\s*id\s*\)",
        sample_results,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"UNIQUE\s*\(\s*run_id\s*,\s*sample_id\s*\)",
        sample_results,
        re.IGNORECASE,
    )
    assert re.search(
        r"CHECK\s*\(\s*jsonb_typeof\s*\(\s*retrieved_contexts\s*\)\s*=\s*'array'",
        sample_results,
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
def test_trace_schema_accepts_degraded_status_and_upgrades_existing_constraints() -> None:
    """Trace tables must persist fallback-success outcomes used by pipelines."""

    sql = _schema_sql()
    query_traces = _table_definition(sql, "rag_query_traces")
    ingestion_traces = _table_definition(sql, "rag_ingestion_traces")

    assert re.search(
        r"CHECK\s*\(\s*status\s+IN\s*\([^)]*'degraded'",
        query_traces,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"CHECK\s*\(\s*status\s+IN\s*\([^)]*'degraded'",
        ingestion_traces,
        re.IGNORECASE | re.DOTALL,
    )
    assert "DROP CONSTRAINT IF EXISTS chk_rag_query_traces_status" in sql
    assert "DROP CONSTRAINT IF EXISTS chk_rag_ingestion_traces_status" in sql


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


@pytest.mark.integration
def test_document_and_chunk_repositories_round_trip_and_replace_content() -> None:
    """Protect the complete document and chunk repository persistence contract.

    The repository must create a missing collection, preserve domain metadata,
    return deterministic chunk order, and replace a logical chunk when changed
    content produces a new stable chunk ID for the same document position.
    """

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for repository integration")

    from src.core.types import Chunk, Document
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import ChunkRepository, DocumentRepository

    collection_id = f"b4-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    source_path = f"fixtures/{document_id}.md"
    source_hash = sha256(b"document-v1").hexdigest()
    pool = PostgresPool.from_settings(
        _database_settings(pool_size=3),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        documents = DocumentRepository(pool)
        chunks = ChunkRepository(pool)
        document = Document(
            id=document_id,
            text="Alpha section. Beta section.",
            summary="Repository fixture summary for chunking and document tools.",
            metadata={"doc_type": "shopping_guide", "language": "en"},
        )

        assert (
            documents.upsert(
                document,
                collection_id=collection_id,
                source_path=source_path,
                source_hash=source_hash,
                title="Repository fixture",
            )
            == document
        )
        assert documents.get_by_id(document_id) == document
        assert documents.list_by_collection(collection_id) == [document]

        initial_chunks = [
            Chunk(
                id=f"chunk-{uuid4().hex}",
                text="Alpha section.",
                metadata={"doc_type": "shopping_guide", "source_path": source_path},
                chunk_index=0,
                start_offset=0,
                end_offset=14,
            ),
            Chunk(
                id=f"chunk-{uuid4().hex}",
                text="Beta section.",
                metadata={"doc_type": "shopping_guide", "source_path": source_path},
                chunk_index=1,
                start_offset=15,
                end_offset=28,
            ),
        ]
        assert (
            chunks.upsert_many(
                initial_chunks,
                collection_id=collection_id,
                document_id=document_id,
            )
            == initial_chunks
        )
        assert chunks.get_by_id(initial_chunks[0].id) == initial_chunks[0]
        assert chunks.list_by_document(document_id) == initial_chunks

        replacement = initial_chunks[0].model_copy(
            update={
                "id": f"chunk-{uuid4().hex}",
                "text": "Alpha section with updated guidance.",
            }
        )
        assert chunks.upsert_many(
            [replacement],
            collection_id=collection_id,
            document_id=document_id,
        ) == [replacement]
        assert chunks.get_by_id(initial_chunks[0].id) is None
        assert chunks.list_by_document(document_id) == [
            replacement,
            initial_chunks[1],
        ]

        reordered = [
            initial_chunks[1].model_copy(
                update={
                    "chunk_index": 0,
                    "start_offset": 0,
                    "end_offset": 13,
                }
            ),
            replacement.model_copy(
                update={
                    "chunk_index": 1,
                    "start_offset": 14,
                    "end_offset": 50,
                }
            ),
        ]
        assert (
            chunks.upsert_many(
                reordered,
                collection_id=collection_id,
                document_id=document_id,
            )
            == reordered
        )
        assert chunks.list_by_document(document_id) == reordered

        replacement_document = Document(
            id=f"doc-{uuid4().hex}",
            text="A new source version with a different source hash.",
            metadata={"doc_type": "shopping_guide", "language": "en"},
        )
        assert (
            documents.upsert(
                replacement_document,
                collection_id=collection_id,
                source_path=source_path,
                source_hash=sha256(b"document-v2").hexdigest(),
                title="Repository fixture v2",
            )
            == replacement_document
        )
        assert documents.get_by_id(document_id) is None
        assert documents.get_by_id(replacement_document.id) == replacement_document
        assert chunks.list_by_document(document_id) == []
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_document_repository_drops_loader_headings_but_keeps_chunk_section_path() -> None:
    """Persist compact document metadata while preserving chunk section paths."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for repository integration")

    from src.core.types import Chunk, Document
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import ChunkRepository, DocumentRepository

    collection_id = f"metadata-prune-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    source_path = f"fixtures/{document_id}.md"
    source_hash = sha256(b"document-with-headings").hexdigest()
    pool = PostgresPool.from_settings(
        _database_settings(pool_size=3),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        document = Document(
            id=document_id,
            text="# Buying Guide\n\n## Audio\n\nWireless guidance.",
            metadata={
                "source_path": source_path,
                "source_type": "markdown",
                "source_hash": source_hash,
                "title": "Buying Guide",
                "headings": [
                    {
                        "level": 1,
                        "title": "Buying Guide",
                        "path": ["Buying Guide"],
                        "text_offset": 0,
                    },
                    {
                        "level": 2,
                        "title": "Audio",
                        "path": ["Buying Guide", "Audio"],
                        "text_offset": 17,
                    },
                ],
                "images": [{"id": "image-1", "path": "data/images/image-1.png"}],
            },
        )
        chunk = Chunk(
            id=f"chunk-{uuid4().hex}",
            text="Wireless guidance.",
            metadata={
                "document_id": document_id,
                "source_path": source_path,
                "section_path": ["Audio"],
            },
            chunk_index=0,
            start_offset=27,
            end_offset=45,
        )

        documents = DocumentRepository(pool)
        chunks = ChunkRepository(pool)
        documents.upsert(
            document,
            collection_id=collection_id,
            source_path=source_path,
            source_hash=source_hash,
            title="Buying Guide",
        )
        chunks.upsert_many([chunk], collection_id=collection_id, document_id=document_id)

        with pool.connection() as connection:
            persisted_document_metadata = connection.execute(
                "SELECT metadata FROM rag_documents WHERE id = %s",
                (document_id,),
            ).fetchone()[0]
            persisted_chunk_metadata = connection.execute(
                "SELECT metadata FROM rag_chunks WHERE id = %s",
                (chunk.id,),
            ).fetchone()[0]

        assert "headings" not in persisted_document_metadata
        assert persisted_document_metadata["images"] == [
            {"id": "image-1", "path": "data/images/image-1.png"}
        ]
        assert persisted_document_metadata["title"] == "Buying Guide"
        assert persisted_chunk_metadata["section_path"] == ["Audio"]
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_image_storage_saves_files_and_queries_upserted_indexes(
    tmp_path: Path,
) -> None:
    """Protect filesystem and PostgreSQL behavior for extracted source images.

    Images must be stored below the configured root, index upserts must remain
    idempotent, and collection/doc-hash queries must return typed records
    without requiring callers to write SQL.
    """

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for image-storage integration")

    from src.core.types import Document
    from src.storage.image_storage import ImageStorage
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import DocumentRepository

    collection_id = f"b4-images-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    source_path = f"fixtures/{document_id}.md"
    doc_hash = sha256(b"image-document").hexdigest()
    image_id = f"image-{uuid4().hex}"
    image_hash = sha256(b"png-fixture").hexdigest()
    pool = PostgresPool.from_settings(
        _database_settings(pool_size=3),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        DocumentRepository(pool).upsert(
            Document(
                id=document_id,
                text="Document containing an image placeholder.",
                metadata={"doc_type": "shopping_guide", "source_path": source_path},
            ),
            collection_id=collection_id,
            source_path=source_path,
            source_hash=doc_hash,
        )
        storage = ImageStorage(pool, root_dir=tmp_path / "data" / "images")

        saved_path = storage.save_image(
            collection_id,
            image_id,
            b"png-fixture",
            suffix=".png",
        )
        assert (
            saved_path
            == (tmp_path / "data" / "images" / collection_id / f"{image_id}.png").resolve()
        )
        assert saved_path.read_bytes() == b"png-fixture"

        first = storage.upsert_index(
            image_id=image_id,
            file_path=saved_path,
            collection_id=collection_id,
            document_id=document_id,
            doc_hash=doc_hash,
            page_num=2,
            width=640,
            height=480,
            mime_type="image/png",
            image_hash=image_hash,
            quality_status="ok",
            metadata={"caption": "Product comparison chart"},
        )
        updated = storage.upsert_index(
            image_id=image_id,
            file_path=saved_path,
            collection_id=collection_id,
            document_id=document_id,
            doc_hash=doc_hash,
            page_num=2,
            width=640,
            height=480,
            mime_type="image/png",
            image_hash=image_hash,
            quality_status="low_quality",
            metadata={"caption": "Low-confidence chart"},
        )

        assert first.image_id == image_id
        assert updated.quality_status == "low_quality"
        assert storage.find_by_collection(collection_id) == [updated]
        assert storage.find_by_doc_hash(doc_hash) == [updated]
        assert storage.find_by_ids(
            [f"missing-{uuid4().hex}", image_id, image_id]
        ) == [updated]
        assert storage.find_by_ids([]) == []
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_document_repository_manages_lifecycle_and_deleted_cleanup(
    tmp_path: Path,
) -> None:
    """Protect document state transitions and cleanup of retrieval-visible data.

    Deleted documents remain as metadata records, but their chunks and image
    index rows must be removed so retrieval and multimodal responses cannot use
    stale content.
    """

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for lifecycle repository integration")

    from src.core.types import Chunk, Document
    from src.storage.image_storage import ImageStorage
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import ChunkRepository, DocumentRepository

    collection_id = f"b6-lifecycle-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    source_path = f"fixtures/{document_id}.md"
    source_hash = sha256(b"lifecycle-document").hexdigest()
    image_id = f"image-{uuid4().hex}"
    pool = PostgresPool.from_settings(
        _database_settings(pool_size=3),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        documents = DocumentRepository(pool)
        chunks = ChunkRepository(pool)
        images = ImageStorage(pool, root_dir=tmp_path / "data" / "images")
        document = Document(
            id=document_id,
            text="Lifecycle test document.",
            metadata={"doc_type": "shopping_guide", "source_path": source_path},
        )
        documents.upsert(
            document,
            collection_id=collection_id,
            source_path=source_path,
            source_hash=source_hash,
        )
        chunks.upsert_many(
            [
                Chunk(
                    id=f"chunk-{uuid4().hex}",
                    text="Lifecycle test document.",
                    metadata={"doc_type": "shopping_guide", "source_path": source_path},
                    chunk_index=0,
                    start_offset=0,
                    end_offset=24,
                )
            ],
            collection_id=collection_id,
            document_id=document_id,
        )
        saved_path = images.save_image(
            collection_id,
            image_id,
            b"image-bytes",
            suffix=".png",
        )
        images.upsert_index(
            image_id=image_id,
            file_path=saved_path,
            collection_id=collection_id,
            document_id=document_id,
            doc_hash=source_hash,
            image_hash=sha256(b"image-bytes").hexdigest(),
        )

        assert documents.mark_processing(document_id) == "processing"
        assert documents.mark_success(document_id) == "success"
        assert documents.list_retrievable_by_collection(collection_id) == [document]
        assert documents.mark_failed(document_id) == "failed"
        assert documents.list_retrievable_by_collection(collection_id) == []
        assert documents.mark_success(document_id) == "success"
        assert documents.mark_deleted(document_id) == "deleted"

        assert documents.get_by_id(document_id) == document
        assert documents.get_lifecycle_status(document_id) == "deleted"
        assert documents.list_retrievable_by_collection(collection_id) == []
        assert chunks.list_by_document(document_id) == []
        assert images.find_by_collection(collection_id) == []
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_image_storage_rejects_paths_outside_collection_directory(
    tmp_path: Path,
) -> None:
    """Prevent collection names, image IDs, or suffixes from escaping storage.

    Path validation must happen before any filesystem write so untrusted source
    metadata cannot overwrite files outside ``data/images/{collection}/``.
    """

    from src.storage.image_storage import ImageStorage

    storage = ImageStorage(MagicMock(), root_dir=tmp_path / "images")

    with pytest.raises(ValueError, match="collection"):
        storage.save_image("../outside", "image-1", b"data", suffix=".png")
    with pytest.raises(ValueError, match="image_id"):
        storage.save_image("guides", "../image-1", b"data", suffix=".png")
    with pytest.raises(ValueError, match="suffix"):
        storage.save_image("guides", "image-1", b"data", suffix="../file")

    unicode_path = storage.save_image(
        "选购指南",
        "商品图-1",
        b"valid-unicode-path",
        suffix=".png",
    )
    assert unicode_path.read_bytes() == b"valid-unicode-path"


@pytest.mark.integration
def test_repository_reads_wrap_psycopg_failures_with_operation_context() -> None:
    """Require read paths to preserve the shared storage exception contract.

    ``PostgresPool.connection()`` intentionally preserves caller exceptions, so
    each repository read boundary must translate psycopg failures into
    ``DatabaseError`` with a trace-safe operation name and original cause.
    """

    import psycopg

    from src.core.errors import DatabaseError
    from src.storage.image_storage import ImageStorage
    from src.storage.repositories import (
        ChunkRepository,
        DocumentRepository,
        EvaluationRepository,
        TraceRepository,
    )

    driver_error = psycopg.OperationalError("simulated read failure")
    connection = MagicMock()
    connection.execute.side_effect = driver_error
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = connection
    cases = [
        (
            lambda: DocumentRepository(pool).get_by_id("doc-1"),
            "document_get",
        ),
        (
            lambda: ChunkRepository(pool).list_by_document("doc-1"),
            "chunk_list_by_document",
        ),
        (
            lambda: ImageStorage(pool).find_by_collection("guides"),
            "image_index_find_by_collection_id",
        ),
        (
            lambda: TraceRepository(pool).get_query_trace("trace-1"),
            "query_trace_get",
        ),
        (
            lambda: EvaluationRepository(pool).list_results("run-1"),
            "evaluation_result_list",
        ),
    ]

    for operation, expected_context in cases:
        with pytest.raises(DatabaseError) as captured:
            operation()
        assert captured.value.context == {"operation": expected_context}
        assert captured.value.cause is driver_error


@pytest.mark.integration
def test_trace_repository_upserts_and_lists_query_and_ingestion_traces() -> None:
    """Protect durable Trace updates and Dashboard-oriented history ordering.

    Query and ingestion traces begin in ``running`` state and are later upserted
    with completion metrics. Repository reads must reconstruct immutable records
    and list newest traces first for one collection.
    """

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for trace repository integration")

    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import (
        IngestionTraceRecord,
        QueryTraceRecord,
        TraceRepository,
    )

    collection_id = f"b5-traces-{uuid4().hex}"
    query_trace_id = f"query-{uuid4().hex}"
    ingestion_trace_id = f"ingestion-{uuid4().hex}"
    started_at = datetime.now(UTC)
    pool = PostgresPool.from_settings(
        _database_settings(pool_size=3),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        repository = TraceRepository(pool)
        query_trace = QueryTraceRecord(
            trace_id=query_trace_id,
            collection_id=collection_id,
            raw_query="How should I compare two headphones?",
            request_source="aimodel",
            started_at=started_at,
            basic_info={"user_id": 1},
            stages=[{"stage": "query_processing", "duration_ms": 4.2}],
        )
        ingestion_trace = IngestionTraceRecord(
            trace_id=ingestion_trace_id,
            collection_id=collection_id,
            source_uri="fixtures/headphones.md",
            source_hash=sha256(b"headphones-guide").hexdigest(),
            started_at=started_at + timedelta(seconds=1),
            basic_info={"force": False},
            stages=[{"stage": "load", "duration_ms": 8.5}],
        )

        running_query = repository.upsert_query_trace(query_trace)
        running_ingestion = repository.upsert_ingestion_trace(ingestion_trace)
        assert running_query.created_at is not None
        assert running_ingestion.created_at is not None
        with pytest.raises(FrozenInstanceError):
            running_query.status = "failed"
        with pytest.raises(TypeError):
            running_query.basic_info["user_id"] = 2
        with pytest.raises(TypeError):
            running_query.stages[0]["stage"] = "mutated"

        completed_query = repository.upsert_query_trace(
            replace(
                query_trace,
                finished_at=started_at + timedelta(seconds=2),
                status="degraded",
                query_result={
                    "contexts": [{"chunk_id": "chunk-1", "score": 0.9, "rank": 1}],
                    "content": "[1] context",
                    "citations": [],
                    "images": [],
                },
                summary_metrics={"duration_ms": 2000, "top_score": 0.9},
                evaluation_metrics={"query_document_relevance": 0.91},
            )
        )
        completed_ingestion = repository.upsert_ingestion_trace(
            replace(
                ingestion_trace,
                finished_at=started_at + timedelta(seconds=3),
                status="degraded",
                summary_metrics={"duration_ms": 2000, "chunk_count": 12},
                evaluation_metrics={"chunk_quality": 0.88},
            )
        )
        assert completed_query.status == "degraded"
        assert completed_ingestion.status == "degraded"
        newer_query = repository.upsert_query_trace(
            replace(
                query_trace,
                trace_id=f"query-{uuid4().hex}",
                raw_query="Which headphone has better battery life?",
                started_at=started_at + timedelta(seconds=10),
            )
        )

        assert repository.get_query_trace(query_trace_id) == completed_query
        assert repository.get_ingestion_trace(ingestion_trace_id) == completed_ingestion
        assert repository.list_query_traces(collection_id) == [
            newer_query,
            completed_query,
        ]
        assert repository.list_ingestion_traces(collection_id) == [completed_ingestion]
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_evaluation_repository_upserts_runs_and_metric_results() -> None:
    """Protect evaluation-run history and one-row-per-metric persistence.

    Re-running the same metric for one evaluation run must update its score and
    evidence rather than create a duplicate. Batch return order must match input
    order while list queries use stable metric-name ordering.
    """

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for evaluation repository integration")

    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import (
        EvaluationRepository,
        EvaluationResultRecord,
        EvaluationRunRecord,
    )

    collection_id = f"b5-evaluation-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    started_at = datetime.now(UTC)
    pool = PostgresPool.from_settings(
        _database_settings(pool_size=3),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        repository = EvaluationRepository(pool)
        running = EvaluationRunRecord(
            id=run_id,
            collection_id=collection_id,
            evaluator="custom",
            dataset_name="shopping_guides_golden_set",
            status="running",
            started_at=started_at,
            settings_snapshot={"retrieval": "hybrid", "rerank": "cross_encoder"},
        )
        stored_running = repository.upsert_run(running)
        assert stored_running.created_at is not None

        initial_results = [
            EvaluationResultRecord(
                id=f"result-{uuid4().hex}",
                run_id=run_id,
                metric_name="hit_rate_at_10",
                metric_value=0.92,
                details={"sample_count": 50},
            ),
            EvaluationResultRecord(
                id=f"result-{uuid4().hex}",
                run_id=run_id,
                metric_name="mrr",
                metric_value=0.81,
                details={"sample_count": 50},
            ),
        ]
        stored_results = repository.upsert_results(run_id, initial_results)
        updated_hit_rate = EvaluationResultRecord(
            id=f"result-{uuid4().hex}",
            run_id=run_id,
            metric_name="hit_rate_at_10",
            metric_value=0.94,
            details={"sample_count": 60, "strategy": "hybrid_rerank"},
        )
        repository.upsert_results(run_id, [updated_hit_rate])
        completed = repository.upsert_run(
            replace(
                running,
                status="success",
                finished_at=started_at + timedelta(seconds=4),
                summary={"metric_count": 2},
            )
        )
        newer_run = repository.upsert_run(
            replace(
                running,
                id=f"run-{uuid4().hex}",
                dataset_name="shopping_guides_regression_set",
                started_at=started_at + timedelta(seconds=10),
            )
        )

        assert [result.metric_name for result in stored_results] == [
            "hit_rate_at_10",
            "mrr",
        ]
        assert repository.get_run(run_id) == completed
        assert repository.list_runs(collection_id) == [newer_run, completed]
        assert repository.list_results(run_id) == [
            replace(updated_hit_rate, created_at=stored_results[0].created_at),
            stored_results[1],
        ]
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_evaluation_repository_upserts_sample_results() -> None:
    """Persist per-sample evaluation diagnostics for low-score analysis."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for evaluation repository integration")

    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import (
        EvaluationRepository,
        EvaluationRunRecord,
        EvaluationSampleResultRecord,
    )

    collection_id = f"b5-eval-samples-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    started_at = datetime.now(UTC)
    pool = PostgresPool.from_settings(
        _database_settings(pool_size=3),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        repository = EvaluationRepository(pool)
        repository.upsert_run(
            EvaluationRunRecord(
                id=run_id,
                collection_id=collection_id,
                evaluator="ragas",
                dataset_name="golden_set",
                status="success",
                started_at=started_at,
                finished_at=started_at + timedelta(seconds=3),
                summary={"sample_count": 2},
            )
        )
        initial = [
            EvaluationSampleResultRecord(
                id=f"sample-result-{uuid4().hex}",
                run_id=run_id,
                sample_id="sample-2",
                sample_index=2,
                collection_id="manual",
                question="物流异常时客服如何回复？",
                golden_answer="客服应安抚并说明催查流程。",
                generated_answer="我理解您着急，会为您发起物流催查。",
                retrieved_contexts=("物流异常应先安抚用户。",),
                context_chunk_ids=("chunk-2",),
                query_trace_ids=("trace-2", "trace-3"),
                metrics={"faithfulness": 0.72},
                error=None,
            ),
            EvaluationSampleResultRecord(
                id=f"sample-result-{uuid4().hex}",
                run_id=run_id,
                sample_id="sample-1",
                sample_index=1,
                collection_id="faq",
                question="金属碗能放微波炉吗？",
                golden_answer="不建议把金属碗放入微波炉。",
                generated_answer="普通家庭不建议将金属碗放入微波炉。",
                retrieved_contexts=("金属会反射微波并产生火花。",),
                context_chunk_ids=("chunk-1",),
                query_trace_ids=("trace-1",),
                metrics={"faithfulness": 0.95, "answer_relevancy": 0.9},
                error=None,
            ),
        ]

        stored = repository.upsert_sample_results(run_id, initial)
        replacement = replace(
            initial[0],
            id=f"sample-result-{uuid4().hex}",
            generated_answer="更新后的客服回答。",
            metrics={"faithfulness": 0.8},
            error={"type": "diagnostic", "message": "updated"},
        )
        repository.upsert_sample_results(run_id, [replacement])

        assert stored == [
            replace(initial[0], created_at=stored[0].created_at),
            replace(initial[1], created_at=stored[1].created_at),
        ]
        assert repository.list_sample_results(run_id) == [
            replace(initial[1], created_at=stored[1].created_at),
            replace(replacement, created_at=stored[0].created_at),
        ]
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_pgvector_store_updates_searches_and_restores_chunk_order() -> None:
    """Exercise the B12 pgvector adapter against the canonical chunk schema.

    The adapter must update embeddings only after ``ChunkRepository`` persists
    chunk content, apply JSONB metadata filters during cosine search, and
    restore caller order for sparse-route ID lookups.
    """

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for pgvector adapter integration")

    from src.core.types import Chunk, Document
    from src.libs.vector_store import PgVectorStore
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import ChunkRepository, DocumentRepository

    collection_id = f"b12-pgvector-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    source_path = f"fixtures/{document_id}.md"
    pool = PostgresPool.from_settings(
        _database_settings(pool_size=3),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        document = Document(
            id=document_id,
            text="Stress ball guide.\nWireless headphone comparison.",
            metadata={"collection": collection_id},
        )
        DocumentRepository(pool).upsert(
            document,
            collection_id=collection_id,
            source_path=source_path,
            source_hash=sha256(document.text.encode()).hexdigest(),
        )
        chunks = [
            Chunk(
                id=f"chunk-{uuid4().hex}",
                text="Stress ball guide.",
                metadata={
                    "doc_type": "guide",
                    "collection": collection_id,
                    "document_id": document_id,
                    "source_path": source_path,
                },
                chunk_index=0,
                start_offset=0,
                end_offset=18,
            ),
            Chunk(
                id=f"chunk-{uuid4().hex}",
                text="Wireless headphone comparison.",
                metadata={
                    "doc_type": "comparison",
                    "collection": collection_id,
                    "document_id": document_id,
                    "source_path": source_path,
                },
                chunk_index=1,
                start_offset=19,
                end_offset=49,
            ),
        ]
        ChunkRepository(pool).upsert_many(
            chunks,
            collection_id=collection_id,
            document_id=document_id,
        )
        store = PgVectorStore(pool=pool, embedding_dimensions=1536)
        first_vector = [1.0, *([0.0] * 1535)]
        second_vector = [0.0, 1.0, *([0.0] * 1534)]

        upserted = store.upsert(chunks, [first_vector, second_vector])
        repeated = store.upsert(chunks, [first_vector, second_vector])
        results = store.search(
            first_vector,
            filters={"doc_type": "guide"},
            top_k=5,
        )
        restored = store.get_by_ids([chunks[1].id, "missing", chunks[0].id])

        assert upserted == [chunks[0].id, chunks[1].id]
        assert repeated == upserted
        assert [result.chunk_id for result in results] == [chunks[0].id]
        assert results[0].score == pytest.approx(1.0)
        assert results[0].metadata["document_id"] == document_id
        assert results[0].metadata["source_path"] == source_path
        assert [chunk.id for chunk in restored] == [chunks[1].id, chunks[0].id]
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_document_repository_deduplicates_only_successful_same_source() -> None:
    """Protect C1 deduplication scope and lifecycle requirements in PostgreSQL."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for document dedup integration")

    from src.core.types import Document
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import DocumentRepository

    collection_id = f"c1-dedup-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    source_path = str((RAG_ROOT / "data" / "raw" / collection_id / "guide.md").resolve())
    source_hash = sha256(b"stable-source").hexdigest()
    pool = PostgresPool.from_settings(
        _database_settings(pool_size=3),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        repository = DocumentRepository(pool)
        repository.upsert(
            Document(
                id=document_id,
                text="Stable source content.",
                metadata={"source_path": source_path},
            ),
            collection_id=collection_id,
            source_path=source_path,
            source_hash=source_hash,
        )

        assert (
            repository.has_successful_source_hash(
                collection_id=collection_id,
                source_path=source_path,
                source_hash=source_hash,
            )
            is False
        )
        repository.mark_success(document_id)
        assert (
            repository.has_successful_source_hash(
                collection_id=collection_id,
                source_path=source_path,
                source_hash=source_hash,
            )
            is True
        )
        assert (
            repository.has_successful_source_hash(
                collection_id=collection_id,
                source_path=source_path,
                source_hash=source_hash.upper(),
            )
            is True
        )
        assert (
            repository.has_successful_source_hash(
                collection_id=collection_id,
                source_path=f"{source_path}.copy",
                source_hash=source_hash,
            )
            is False
        )
        assert (
            repository.has_successful_source_hash(
                collection_id=collection_id,
                source_path=source_path,
                source_hash=sha256(b"changed-source").hexdigest(),
            )
            is False
        )
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_collection_profile_repository_upserts_and_reads_embedding_cache() -> None:
    """Repository should reuse profile embeddings by collection and profile name."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL repository integration")

    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import CollectionProfileRepository

    pool = PostgresPool.from_settings(
        _database_settings(pool_size=2),
        environ={"DATABASE_URL": database_url},
    )
    collection = f"profiles-{uuid4().hex}"
    try:
        pool.open()
        init_schema(pool)
        repository = CollectionProfileRepository(pool)

        repository.upsert_profile_embedding(
            collection=collection,
            profile_name="default",
            profile_text="客服话术 profile",
            content_hash="a" * 64,
            embedding=[0.1] * 1536,
            provider="fake",
            model="fake-model",
        )
        first = repository.get_profile_embedding(collection, "default")
        assert first is not None
        assert first["content_hash"] == "a" * 64
        assert first["embedding"] == [0.1] * 1536

        repository.upsert_profile_embedding(
            collection=collection,
            profile_name="default",
            profile_text="客服话术 profile changed",
            content_hash="b" * 64,
            embedding=[0.4] * 1536,
            provider="fake",
            model="fake-model-v2",
        )
        second = repository.get_profile_embedding(collection, "default")
        assert second is not None
        assert second["content_hash"] == "b" * 64
        assert second["embedding"] == [0.4] * 1536
        assert second["model"] == "fake-model-v2"
    finally:
        if pool.is_open:
            pool.close()
