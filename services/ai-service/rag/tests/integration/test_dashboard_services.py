"""Verify Dashboard read services for configuration and indexed data browsing.

F6 introduces service classes that Streamlit pages can call without knowing
settings internals, SQL table shapes, or repository implementation details.
These tests use a real PostgreSQL schema for data browsing because Dashboard
counts must match the same durable rows created by ingestion and retrieval.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = RAG_ROOT / "config" / "settings.example.yaml"
VECTOR_DIMENSIONS = 1536
sys.path.insert(0, str(RAG_ROOT))


def _database_settings() -> object:
    """Build typed PostgreSQL settings for Dashboard service integration tests."""

    from src.core.config import DatabaseSettings

    return DatabaseSettings(
        provider="postgresql",
        url_env="DATABASE_URL",
        pool_size=3,
        echo_sql=False,
    )


@pytest.mark.integration
def test_config_reader_service_returns_dashboard_component_overview() -> None:
    """Require settings to be projected into a stable Dashboard overview."""

    from src.core.config import load_settings
    from src.observability.services import ConfigReaderService

    settings = load_settings(SETTINGS_PATH, validate_environment=False)

    overview = ConfigReaderService(settings_loader=lambda: settings).read_overview()

    assert overview.project_name == settings.project.name
    assert overview.default_collection == settings.project.default_collection
    assert overview.environment == settings.project.environment
    assert {component.component for component in overview.components} >= {
        "llm",
        "embedding",
        "splitter",
        "reranker",
        "vector_store",
    }
    embedding = next(
        component
        for component in overview.components
        if component.component == "embedding"
    )
    assert embedding.provider == settings.embedding.default
    assert embedding.model == settings.embedding.selected_provider.model
    assert embedding.enabled is True
    assert overview.dashboard_pages == tuple(settings.dashboard.pages)
    assert overview.paths["trace_jsonl_path"] == settings.observability.trace_jsonl_path


@pytest.mark.integration
def test_data_browser_service_lists_documents_chunks_and_images(
    tmp_path: Path,
) -> None:
    """Require Dashboard data browsing to summarize indexed PostgreSQL rows."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for Dashboard service integration")

    from src.core.types import Chunk, Document
    from src.ingestion.embedding import BM25Indexer
    from src.libs.vector_store import PgVectorStore
    from src.observability.services import DataBrowserService
    from src.storage.bm25_storage import BM25Storage
    from src.storage.image_storage import ImageStorage
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import ChunkRepository, DocumentRepository

    collection_id = f"f6-dashboard-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    source_path = f"fixtures/{document_id}.md"
    source_hash = sha256(b"dashboard-fixture").hexdigest()
    image_id = f"image-{uuid4().hex}"
    document = Document(
        id=document_id,
        text="Wireless headphones guide with one product image.",
        metadata={
            "doc_type": "buying_guide",
            "title": "Wireless guide metadata",
        },
    )
    chunks = [
        Chunk(
            id=f"chunk-{uuid4().hex}",
            text="Wireless headphones need stable Bluetooth and comfort.",
            metadata={
                "collection": collection_id,
                "doc_type": "buying_guide",
                "image_refs": [image_id],
            },
            chunk_index=0,
            start_offset=0,
            end_offset=55,
            source_ref={
                "document_id": document_id,
                "source_path": source_path,
                "title": "Wireless guide",
                "section_path": ["Audio"],
            },
        ),
        Chunk(
            id=f"chunk-{uuid4().hex}",
            text="Battery life and active noise cancellation matter.",
            metadata={
                "collection": collection_id,
                "doc_type": "buying_guide",
            },
            chunk_index=1,
            start_offset=56,
            end_offset=107,
            source_ref={
                "document_id": document_id,
                "source_path": source_path,
                "title": "Wireless guide",
                "section_path": ["Audio", "Battery"],
            },
        ),
    ]

    pool = PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        documents = DocumentRepository(pool)
        chunk_repository = ChunkRepository(pool)
        documents.upsert(
            document,
            collection_id=collection_id,
            source_path=source_path,
            source_hash=source_hash,
            title="Wireless guide",
        )
        chunk_repository.upsert_many(
            chunks,
            collection_id=collection_id,
            document_id=document_id,
        )
        PgVectorStore(pool=pool, embedding_dimensions=VECTOR_DIMENSIONS).upsert(
            chunks,
            [
                [1.0] + [0.0] * (VECTOR_DIMENSIONS - 1),
                [0.0, 1.0] + [0.0] * (VECTOR_DIMENSIONS - 2),
            ],
        )
        BM25Storage(pool).upsert_index(
            BM25Indexer().index(chunks),
            collection_id=collection_id,
            document_id=document_id,
        )
        image_source = tmp_path / "headphones.png"
        image_source.write_bytes(b"fake-image")
        image_storage = ImageStorage(pool, root_dir=tmp_path / "images")
        saved_image = image_storage.save_image(
            collection_id,
            image_id,
            b"fake-image",
            suffix=".png",
        )
        image_storage.upsert_index(
            image_id=image_id,
            file_path=saved_image,
            collection_id=collection_id,
            document_id=document_id,
            doc_hash=source_hash,
            image_hash=sha256(b"fake-image").hexdigest(),
            page_num=1,
            width=640,
            height=480,
            mime_type="image/png",
            quality_status="ok",
            metadata={"caption": "Black wireless headphones."},
        )
        documents.mark_success(document_id)

        service = DataBrowserService(pool)
        stats = service.collection_stats(collection_id)
        document_rows = service.list_documents(collection_id)
        chunk_rows = service.list_chunks(document_id)
        image_rows = service.list_images(collection_id)
        chunk_detail = service.get_chunk_detail(chunks[0].id)

        assert stats.collection_id == collection_id
        assert stats.document_count == 1
        assert stats.chunk_count == 2
        assert stats.image_count == 1
        assert stats.dense_indexed_chunk_count == 2
        assert stats.bm25_indexed_chunk_count == 2

        assert len(document_rows) == 1
        assert document_rows[0].document_id == document_id
        assert document_rows[0].title == "Wireless guide"
        assert document_rows[0].source_path == source_path
        assert document_rows[0].lifecycle_status == "success"
        assert document_rows[0].chunk_count == 2
        assert document_rows[0].image_count == 1

        assert [chunk.chunk_id for chunk in chunk_rows] == [
            chunks[0].id,
            chunks[1].id,
        ]
        assert chunk_rows[0].dense_indexed is True
        assert chunk_rows[0].bm25_term_count > 0
        assert chunk_rows[0].image_refs == (image_id,)

        assert len(image_rows) == 1
        assert image_rows[0].image_id == image_id
        assert image_rows[0].file_path == str(saved_image)
        assert image_rows[0].quality_status == "ok"

        assert chunk_detail == chunk_rows[0]
        assert service.get_chunk_detail("missing-chunk") is None
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_trace_reader_service_lists_query_and_ingestion_details() -> None:
    """Require Dashboard trace service to expose history and detail DTOs."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for Dashboard service integration")

    from src.observability.services import TraceReaderService
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import (
        IngestionTraceRecord,
        QueryTraceRecord,
        TraceRepository,
    )

    collection_id = f"f7-traces-{uuid4().hex}"
    query_trace_id = f"query-{uuid4().hex}"
    ingestion_trace_id = f"ingestion-{uuid4().hex}"
    started_at = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    finished_at = started_at + timedelta(milliseconds=180)
    source_hash = sha256(b"trace-fixture").hexdigest()

    pool = PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        repository = TraceRepository(pool)
        repository.upsert_query_trace(
            QueryTraceRecord(
                trace_id=query_trace_id,
                collection_id=collection_id,
                raw_query="How should I choose wireless headphones?",
                request_source="dashboard-test",
                started_at=started_at,
                finished_at=finished_at,
                status="success",
                basic_info={
                    "collection": collection_id,
                    "raw_query": "How should I choose wireless headphones?",
                },
                stages=(
                    {
                        "stage": "dense",
                        "duration_ms": 40.0,
                        "candidate_count": 4,
                        "status": "success",
                    },
                    {
                        "stage": "rerank",
                        "duration_ms": 25.0,
                        "candidate_count": 2,
                        "status": "degraded",
                        "details": {"fallback_reason": "reranker unavailable"},
                    },
                ),
                summary_metrics={
                    "total_duration_ms": 180.0,
                    "candidate_count_by_stage": {"dense": 4, "rerank": 2},
                    "fallback_used": True,
                    "top_k_results": [{"chunk_id": "chunk-1"}],
                },
                evaluation_metrics={
                    "query_document_relevance": 0.91,
                    "rerank_delta": {"chunk-1": 1},
                },
            )
        )
        repository.upsert_ingestion_trace(
            IngestionTraceRecord(
                trace_id=ingestion_trace_id,
                collection_id=collection_id,
                source_uri="fixtures/wireless.md",
                source_hash=source_hash,
                started_at=started_at - timedelta(minutes=5),
                finished_at=started_at - timedelta(minutes=4, milliseconds=500),
                status="success",
                basic_info={
                    "collection": collection_id,
                    "source_uri": "fixtures/wireless.md",
                },
                stages=(
                    {
                        "stage": "load",
                        "duration_ms": 30.0,
                        "status": "success",
                    },
                    {
                        "stage": "upsert",
                        "duration_ms": 55.0,
                        "status": "success",
                    },
                ),
                summary_metrics={
                    "total_duration_ms": 500.0,
                    "document_status": "success",
                    "chunk_count": 3,
                    "embedded_count": 3,
                    "skipped_count": 0,
                },
                evaluation_metrics={
                    "embedding_coverage": 1.0,
                    "index_ready": True,
                },
            )
        )

        service = TraceReaderService(pool)
        query_history = service.list_query_traces(collection_id)
        ingestion_history = service.list_ingestion_traces(collection_id)
        query_detail = service.get_query_trace_detail(query_trace_id)
        ingestion_detail = service.get_ingestion_trace_detail(ingestion_trace_id)

        assert [item.trace_id for item in query_history] == [query_trace_id]
        assert query_history[0].trace_type == "query"
        assert query_history[0].display_input == "How should I choose wireless headphones?"
        assert query_history[0].duration_ms == 180.0
        assert query_history[0].stage_count == 2
        assert query_history[0].fallback_used is True

        assert query_detail is not None
        assert query_detail.trace_id == query_trace_id
        assert [stage.stage for stage in query_detail.waterfall] == ["dense", "rerank"]
        assert query_detail.candidate_counts == {"dense": 4, "rerank": 2}
        assert query_detail.rerank_delta == {"chunk-1": 1}
        assert query_detail.evaluation_metrics["query_document_relevance"] == 0.91

        assert [item.trace_id for item in ingestion_history] == [ingestion_trace_id]
        assert ingestion_history[0].trace_type == "ingestion"
        assert ingestion_history[0].display_input == "fixtures/wireless.md"
        assert ingestion_history[0].duration_ms == 500.0

        assert ingestion_detail is not None
        assert [stage.stage for stage in ingestion_detail.waterfall] == [
            "load",
            "upsert",
        ]
        assert ingestion_detail.summary_metrics["chunk_count"] == 3
        assert ingestion_detail.evaluation_metrics["index_ready"] is True
        assert service.get_query_trace_detail("missing-query-trace") is None
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_evaluation_service_runs_evaluator_and_reads_metric_trends() -> None:
    """Require Dashboard evaluation service to persist runs and trend data."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for Dashboard service integration")

    from src.observability.services import EvaluationService
    from src.storage.postgres import PostgresPool, init_schema

    collection_id = f"f7-evaluation-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    dataset = [
        {
            "id": "wireless-001",
            "question": "How should I choose wireless headphones?",
            "expected_sources": ["wireless.md"],
        }
    ]
    predictions = [
        {
            "id": "wireless-001",
            "answer": "Compare stability, comfort, battery, and warranty.",
            "sources": ["wireless.md"],
        }
    ]

    pool = PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        service = EvaluationService(pool)
        run_detail = service.run_evaluation(
            collection_id=collection_id,
            evaluator="fake",
            dataset_name="shopping-guide-golden",
            dataset=dataset,
            predictions=predictions,
            evaluator_options={
                "metrics": {
                    "hit_rate_at_k": 0.95,
                    "mrr": 0.88,
                }
            },
            settings_snapshot={"retrieval": "hybrid", "rerank": "fake"},
            run_id=run_id,
        )

        run_history = service.list_runs(collection_id)
        run_detail_by_id = service.get_run_detail(run_id)
        trends = service.metric_trends(collection_id)

        assert run_detail.run_id == run_id
        assert run_detail.status == "success"
        assert run_detail.metrics == {
            "hit_rate_at_k": 0.95,
            "mrr": 0.88,
        }
        assert run_detail.summary["sample_count"] == 1

        assert [run.run_id for run in run_history] == [run_id]
        assert run_history[0].metric_count == 2
        assert run_history[0].metrics["mrr"] == 0.88
        assert run_detail_by_id == run_detail

        assert set(trends) == {"hit_rate_at_k", "mrr"}
        assert trends["hit_rate_at_k"][0].run_id == run_id
        assert trends["hit_rate_at_k"][0].metric_value == 0.95
        assert service.get_run_detail("missing-run") is None
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()
