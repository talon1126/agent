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


class _FakeStreamlit:
    """Record Streamlit-like calls made by Dashboard page render functions."""

    def __init__(self) -> None:
        """Create an empty call log and deterministic widget responses."""

        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.text_input_value = ""
        self.checkbox_value = False
        self.button_value = False
        self.selectbox_index = 0

    def title(self, *args: object, **kwargs: object) -> None:
        """Record a page title call."""

        self.calls.append(("title", args, kwargs))

    def subheader(self, *args: object, **kwargs: object) -> None:
        """Record a section header call."""

        self.calls.append(("subheader", args, kwargs))

    def caption(self, *args: object, **kwargs: object) -> None:
        """Record caption text used for operator context."""

        self.calls.append(("caption", args, kwargs))

    def metric(self, *args: object, **kwargs: object) -> None:
        """Record a metric card call."""

        self.calls.append(("metric", args, kwargs))

    def dataframe(self, *args: object, **kwargs: object) -> None:
        """Record a table rendering call."""

        self.calls.append(("dataframe", args, kwargs))

    def write(self, *args: object, **kwargs: object) -> None:
        """Record generic structured output."""

        self.calls.append(("write", args, kwargs))

    def info(self, *args: object, **kwargs: object) -> None:
        """Record informational state shown to an operator."""

        self.calls.append(("info", args, kwargs))

    def warning(self, *args: object, **kwargs: object) -> None:
        """Record warning state shown to an operator."""

        self.calls.append(("warning", args, kwargs))

    def success(self, *args: object, **kwargs: object) -> None:
        """Record success state shown to an operator."""

        self.calls.append(("success", args, kwargs))

    def text_input(self, *args: object, **kwargs: object) -> str:
        """Record a text input and return the configured fake value."""

        self.calls.append(("text_input", args, kwargs))
        return self.text_input_value

    def checkbox(self, *args: object, **kwargs: object) -> bool:
        """Record a checkbox and return the configured fake value."""

        self.calls.append(("checkbox", args, kwargs))
        return self.checkbox_value

    def button(self, *args: object, **kwargs: object) -> bool:
        """Record a button and return the configured fake value."""

        self.calls.append(("button", args, kwargs))
        return self.button_value

    def selectbox(
        self,
        *args: object,
        options: list[str] | tuple[str, ...],
        **kwargs: object,
    ) -> str | None:
        """Record a selectbox and return a deterministic selected option."""

        self.calls.append(("selectbox", args, {"options": options, **kwargs}))
        if not options:
            return None
        return options[self.selectbox_index]


@pytest.mark.integration
def test_overview_page_builds_and_renders_system_summary() -> None:
    """Require the overview page to render config, assets, and health data."""

    from datetime import datetime

    from src.observability.pages.overview import (
        DashboardHealthSnapshot,
        OverviewPageModel,
        render_overview_page,
    )
    from src.observability.services import CollectionStats, ConfigOverview

    fake_ui = _FakeStreamlit()
    model = OverviewPageModel(
        config=ConfigOverview(
            project_name="aimodel-rag",
            default_collection="shopping_guides",
            environment="test",
            components=(),
            dashboard_pages=("overview", "ingestion_manage"),
            paths={"raw_data_dir": "data/raw"},
        ),
        collection_stats=CollectionStats(
            collection_id="shopping_guides",
            document_count=2,
            chunk_count=8,
            image_count=1,
            dense_indexed_chunk_count=8,
            bm25_indexed_chunk_count=8,
        ),
        latest_query=DashboardHealthSnapshot(
            trace_id="query-1",
            status="success",
            duration_ms=120.0,
            started_at=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
            error=None,
        ),
        latest_ingestion=DashboardHealthSnapshot(
            trace_id="ingestion-1",
            status="success",
            duration_ms=450.0,
            started_at=datetime(2026, 1, 1, 7, 55, tzinfo=UTC),
            error=None,
        ),
    )

    render_overview_page(model, ui=fake_ui)

    call_names = [name for name, _, _ in fake_ui.calls]
    assert call_names.count("title") == 1
    assert call_names.count("metric") >= 6
    assert "dataframe" in call_names
    assert any("System Overview" in args for name, args, _ in fake_ui.calls if name == "title")


@pytest.mark.integration
def test_ingestion_manage_page_builds_and_renders_operator_controls() -> None:
    """Require ingestion manage page to render controls without side effects."""

    from src.observability.pages.ingestion_manage import (
        IngestionManagePageModel,
        render_ingestion_manage_page,
    )
    from src.observability.services import DocumentBrowserRow

    fake_ui = _FakeStreamlit()
    fake_ui.text_input_value = "data/raw/shopping_guides/wireless.md"
    fake_ui.checkbox_value = True
    fake_ui.button_value = True
    model = IngestionManagePageModel(
        collection_id="shopping_guides",
        raw_data_dir="data/raw/shopping_guides",
        documents=(
            DocumentBrowserRow(
                document_id="doc-1",
                collection_id="shopping_guides",
                title="Wireless guide",
                source_path="data/raw/shopping_guides/wireless.md",
                source_hash="a" * 64,
                lifecycle_status="success",
                chunk_count=3,
                image_count=1,
            ),
        ),
    )

    selection = render_ingestion_manage_page(model, ui=fake_ui)

    assert selection.collection_id == "shopping_guides"
    assert selection.source_path == "data/raw/shopping_guides/wireless.md"
    assert selection.force is True
    assert selection.submit_ingest is True
    assert selection.delete_document_id == "doc-1"
    call_names = [name for name, _, _ in fake_ui.calls]
    assert "text_input" in call_names
    assert "checkbox" in call_names
    assert call_names.count("button") == 2
    assert "dataframe" in call_names


@pytest.mark.integration
def test_dashboard_page_model_builders_read_services_without_side_effects() -> None:
    """Require F8 page model builders to use service DTOs as their data source."""

    from datetime import datetime

    from src.observability.pages.ingestion_manage import (
        build_ingestion_manage_page_model,
    )
    from src.observability.pages.overview import build_overview_page_model
    from src.observability.services import (
        CollectionStats,
        ConfigOverview,
        DocumentBrowserRow,
        TraceHistoryItem,
    )

    config = ConfigOverview(
        project_name="aimodel-rag",
        default_collection="shopping_guides",
        environment="test",
        components=(),
        dashboard_pages=("overview", "ingestion_manage"),
        paths={"raw_data_dir": "data/raw"},
    )
    stats = CollectionStats(
        collection_id="shopping_guides",
        document_count=1,
        chunk_count=2,
        image_count=0,
        dense_indexed_chunk_count=2,
        bm25_indexed_chunk_count=2,
    )
    document = DocumentBrowserRow(
        document_id="doc-builder",
        collection_id="shopping_guides",
        title="Builder guide",
        source_path="data/raw/builder.md",
        source_hash="b" * 64,
        lifecycle_status="success",
        chunk_count=2,
        image_count=0,
    )
    query_trace = TraceHistoryItem(
        trace_id="query-builder",
        trace_type="query",
        collection_id="shopping_guides",
        status="success",
        display_input="builder query",
        started_at=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        finished_at=None,
        duration_ms=42.0,
        stage_count=2,
        fallback_used=False,
    )

    class _ConfigReader:
        """Return the fixed Dashboard configuration overview."""

        def read_overview(self) -> ConfigOverview:
            """Return a stable config DTO for page builder tests."""

            return config

    class _DataBrowser:
        """Return fixed collection stats and document rows."""

        def collection_stats(self, collection_id: str) -> CollectionStats:
            """Return stats for the requested collection."""

            assert collection_id == "shopping_guides"
            return stats

        def list_documents(self, collection_id: str) -> list[DocumentBrowserRow]:
            """Return indexed documents for the requested collection."""

            assert collection_id == "shopping_guides"
            return [document]

    class _TraceReader:
        """Return fixed trace history rows for overview health cards."""

        def list_query_traces(self, collection_id: str) -> list[TraceHistoryItem]:
            """Return query traces for the requested collection."""

            assert collection_id == "shopping_guides"
            return [query_trace]

        def list_ingestion_traces(self, collection_id: str) -> list[TraceHistoryItem]:
            """Return an empty ingestion trace list for no-data health state."""

            assert collection_id == "shopping_guides"
            return []

    overview_model = build_overview_page_model(
        config_reader=_ConfigReader(),
        data_browser=_DataBrowser(),
        trace_reader=_TraceReader(),
    )
    ingestion_model = build_ingestion_manage_page_model(
        config_reader=_ConfigReader(),
        data_browser=_DataBrowser(),
    )

    assert overview_model.config == config
    assert overview_model.collection_stats == stats
    assert overview_model.latest_query.trace_id == "query-builder"
    assert overview_model.latest_ingestion.status == "empty"
    assert ingestion_model.collection_id == "shopping_guides"
    assert ingestion_model.raw_data_dir == "data/raw"
    assert ingestion_model.documents == (document,)
