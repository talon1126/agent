"""Verify Dashboard read services for configuration and indexed data browsing.

F6 introduces service classes that Streamlit pages can call without knowing
settings internals, SQL table shapes, or repository implementation details.
These tests use a real PostgreSQL schema for data browsing because Dashboard
counts must match the same durable rows created by ingestion and retrieval.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
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
    reranker = next(
        component
        for component in overview.components
        if component.component == "reranker"
    )
    assert reranker.provider == settings.rerank.default
    assert reranker.model == settings.llm.providers["deepseek"].model
    assert reranker.details["llm_provider"] == "deepseek"

    transform = next(
        component
        for component in overview.components
        if component.component == "transform"
    )
    transform_steps = transform.details["steps"]
    rewrite_step = next(step for step in transform_steps if step["name"] == "rewrite_chunk")
    semantic_merge_step = next(
        step for step in transform_steps if step["name"] == "semantic_merge"
    )
    image_captioner_step = next(
        step for step in transform_steps if step["name"] == "image_captioner"
    )
    assert rewrite_step["provider"] == settings.llm.default
    assert rewrite_step["model"] == settings.llm.selected_provider.model
    assert rewrite_step["model_source"] == "llm.default"
    assert semantic_merge_step["provider"] == settings.llm.default
    assert semantic_merge_step["model"] == settings.llm.selected_provider.model
    assert semantic_merge_step["model_source"] == "llm.default"
    assert image_captioner_step["provider"] == settings.vision_llm.default
    assert image_captioner_step["model"] == settings.vision_llm.selected_provider.model
    assert image_captioner_step["model_source"] == "vision_llm.default"
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
                query_result={
                    "contexts": [{"chunk_id": "chunk-1", "score": 0.91, "rank": 1}],
                    "content": "[1] context",
                    "citations": [],
                    "images": [],
                },
                summary_metrics={
                    "total_duration_ms": 180.0,
                    "candidate_count_by_stage": {"dense": 4, "rerank": 2},
                    "fallback_used": True,
                    "top_score": 0.91,
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
                        "stage": "transform",
                        "duration_ms": 120.0,
                        "status": "success",
                        "sub_stages": [
                            {
                                "name": "metadata_enrich",
                                "duration_ms": 5.0,
                                "status": "success",
                                "input_count": 3,
                                "output_count": 3,
                                "changed_count": 2,
                                "unchanged_count": 1,
                                "method": "transform",
                                "provider": "MetadataEnricher",
                                "error": None,
                            },
                            {
                                "name": "rewrite_chunk",
                                "duration_ms": 115.0,
                                "status": "success",
                                "input_count": 3,
                                "output_count": 3,
                                "changed_count": 2,
                                "unchanged_count": 1,
                                "method": "transform",
                                "provider": "ChunkRewriter",
                                "error": None,
                                "snapshots": [
                                    {
                                        "chunk_id": "chunk-1",
                                        "chunk_index": 0,
                                        "change_type": "changed",
                                        "before_preview": "Original buying guide.",
                                        "after_preview": "Rewritten buying guide.",
                                        "before_truncated": False,
                                        "after_truncated": False,
                                    }
                                ],
                            },
                            {
                                "name": "image_captioner",
                                "duration_ms": 20.0,
                                "status": "success",
                                "input_count": 3,
                                "output_count": 3,
                                "changed_count": 0,
                                "unchanged_count": 3,
                                "method": "transform",
                                "provider": "ImageCaptioner",
                                "error": None,
                                "details": {
                                    "provider": "dashscope",
                                    "model": "qwen-vl-max",
                                    "image_count": 3,
                                    "caption_count": 0,
                                    "status_counts": {"failed": 3},
                                    "failures": [
                                        {
                                            "image_id": "image-1",
                                            "status": "failed",
                                            "reason": "provider unavailable",
                                        }
                                    ],
                                },
                            },
                        ],
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
            "transform",
            "upsert",
        ]
        assert [step.name for step in ingestion_detail.transform_steps] == [
            "metadata_enrich",
            "rewrite_chunk",
            "image_captioner",
        ]
        assert ingestion_detail.transform_steps[1].duration_ms == 115.0
        assert ingestion_detail.transform_steps[1].provider == "ChunkRewriter"
        assert ingestion_detail.transform_steps[1].changed_count == 2
        assert ingestion_detail.transform_steps[1].unchanged_count == 1
        assert ingestion_detail.transform_steps[1].snapshots[0].step_color == "#8B5CF6"
        assert ingestion_detail.transform_steps[1].snapshots[0].before_preview == (
            "Original buying guide."
        )
        assert ingestion_detail.transform_steps[2].details["model"] == "qwen-vl-max"
        assert ingestion_detail.transform_steps[2].details["failures"][0]["reason"] == (
            "provider unavailable"
        )
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
        self.radio_index = 0
        self.sidebar = self
        self.checkbox_values: dict[str, bool] = {}
        self.uploaded_files: dict[str, list[object]] = {}

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

    def bar_chart(self, *args: object, **kwargs: object) -> None:
        """Record a chart rendering call."""

        self.calls.append(("bar_chart", args, kwargs))

    def write(self, *args: object, **kwargs: object) -> None:
        """Record generic structured output."""

        self.calls.append(("write", args, kwargs))

    def markdown(self, *args: object, **kwargs: object) -> None:
        """Record Markdown/HTML output used by visual legends."""

        self.calls.append(("markdown", args, kwargs))

    def text_area(self, *args: object, **kwargs: object) -> None:
        """Record read-only text blocks used for longer trace content."""

        self.calls.append(("text_area", args, kwargs))

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
        label = str(args[0]) if args else ""
        return self.checkbox_values.get(label, self.checkbox_value)

    def button(self, *args: object, **kwargs: object) -> bool:
        """Record a button and return the configured fake value."""

        self.calls.append(("button", args, kwargs))
        return self.button_value

    def file_uploader(self, *args: object, **kwargs: object) -> list[object]:
        """Record file uploader calls and return configured uploaded files."""

        self.calls.append(("file_uploader", args, kwargs))
        label = str(args[0]) if args else ""
        return self.uploaded_files.get(label, [])

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

    def radio(
        self,
        *args: object,
        options: list[str] | tuple[str, ...],
        **kwargs: object,
    ) -> str | None:
        """Record a radio selector and return a deterministic page option."""

        self.calls.append(("radio", args, {"options": options, **kwargs}))
        if not options:
            return None
        return options[self.radio_index]

    def expander(self, *args: object, **kwargs: object) -> _FakeStreamlit:
        """Record an expander and return a context-manager-compatible fake."""

        self.calls.append(("expander", args, kwargs))
        return self

    def __enter__(self) -> _FakeStreamlit:
        """Return the fake renderer for Streamlit expander context blocks."""

        return self

    def __exit__(self, *args: object) -> None:
        """Close a fake Streamlit context block without suppressing errors."""

        return None


def _dataframe_payload(fake_ui: _FakeStreamlit, index: int) -> object:
    """Return the payload passed to one recorded fake dataframe call."""

    dataframes = [args[0] for name, args, _kwargs in fake_ui.calls if name == "dataframe"]
    return dataframes[index]


@pytest.mark.integration
def test_overview_page_builds_and_renders_system_summary() -> None:
    """Require the overview page to render config, assets, and health data."""

    from datetime import datetime

    from src.observability.pages.overview import (
        DashboardHealthSnapshot,
        OverviewPageModel,
        render_overview_page,
    )
    from src.observability.services import (
        CollectionStats,
        ComponentConfig,
        ConfigOverview,
    )

    fake_ui = _FakeStreamlit()
    model = OverviewPageModel(
        config=ConfigOverview(
            project_name="aimodel-rag",
            default_collection="shopping_guides",
            environment="test",
            components=(
                ComponentConfig(
                    component="llm",
                    provider="deepseek",
                    model="deepseek-v4-flash",
                    enabled=True,
                ),
                ComponentConfig(
                    component="transform",
                    provider="serial_pipeline",
                    enabled=True,
                    details={
                        "steps": [
                            {
                                "name": "rewrite_chunk",
                                "enabled": True,
                                "provider": "deepseek",
                                "model": "deepseek-v4-flash",
                                "model_source": "llm.default",
                                "prompt_path": "config/prompts/rewrite_chunk_prompt.yaml",
                            },
                            {
                                "name": "denoise",
                                "enabled": True,
                                "provider": "deterministic",
                                "model": "n/a",
                                "model_source": "deterministic",
                                "prompt_path": None,
                            },
                        ]
                    },
                ),
            ),
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
    assert "expander" in call_names
    assert any(
        args == ("sub_transform",)
        for name, args, _kwargs in fake_ui.calls
        if name == "expander"
    )
    transform_rows = _dataframe_payload(fake_ui, 1)
    assert transform_rows[0]["sub_transform"] == "rewrite_chunk"
    assert transform_rows[0]["provider"] == "deepseek"
    assert transform_rows[0]["model"] == "deepseek-v4-flash"
    assert transform_rows[0]["model_source"] == "llm.default"
    assert any("System Overview" in args for name, args, _ in fake_ui.calls if name == "title")


@pytest.mark.integration
def test_ingestion_manage_page_builds_and_renders_operator_controls() -> None:
    """Require ingestion manage page to submit ingestion through a service."""

    from src.observability.pages.ingestion_manage import (
        IngestionManagePageModel,
        render_ingestion_manage_page,
    )
    from src.observability.services import (
        DocumentBrowserRow,
        IngestionOperationResult,
    )

    class FakeIngestionService:
        """Capture Dashboard ingestion requests without running a pipeline."""

        def __init__(self) -> None:
            """Create an empty request log for assertions."""

            self.requests: list[object] = []

        def run_ingestion(self, request: object) -> IngestionOperationResult:
            """Record the request and return a successful fake result."""

            self.requests.append(request)
            return IngestionOperationResult(
                status="success",
                collection="shopping_guides",
                source_path="data/raw/shopping_guides/wireless.md",
                force=True,
                exit_code=0,
                processed=1,
                trace_ids=("trace-dashboard-ingest",),
                source_paths=("data/raw/shopping_guides/wireless.md",),
                summary={"chunk_count": 3},
            )

    fake_ui = _FakeStreamlit()
    fake_ui.text_input_value = "data/raw/shopping_guides/wireless.md"
    fake_ui.checkbox_value = True
    fake_ui.button_value = True
    fake_service = FakeIngestionService()
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

    selection = render_ingestion_manage_page(
        model,
        ui=fake_ui,
        ingestion_service=fake_service,
    )

    assert selection.collection_id == "shopping_guides"
    assert selection.source_path == "data/raw/shopping_guides/wireless.md"
    assert selection.force is True
    assert selection.submit_ingest is True
    assert selection.delete_document_id == "doc-1"
    assert len(fake_service.requests) == 1
    request = fake_service.requests[0]
    assert request.collection == "shopping_guides"
    assert request.source_path == "data/raw/shopping_guides/wireless.md"
    assert request.force is True
    call_names = [name for name, _, _ in fake_ui.calls]
    assert "text_input" in call_names
    assert "checkbox" in call_names
    assert call_names.count("button") == 2
    assert "success" in call_names
    assert "dataframe" in call_names
    document_rows = _dataframe_payload(fake_ui, -1)
    assert document_rows[0]["created_at"] is None
    assert document_rows[0]["updated_at"] is None


@pytest.mark.integration
def test_ingestion_manage_page_submits_selected_uploaded_files_only() -> None:
    """Require multi-file and directory uploads to support candidate deselection."""

    from src.observability.pages.ingestion_manage import (
        IngestionManagePageModel,
        render_ingestion_manage_page,
    )
    from src.observability.services import IngestionOperationResult

    class FakeUploadedFile:
        """Minimal Streamlit UploadedFile double for Dashboard page tests."""

        def __init__(self, name: str, content: bytes) -> None:
            """Store a fake uploaded file name and bytes."""

            self.name = name
            self._content = content

        def getvalue(self) -> bytes:
            """Return uploaded file bytes as Streamlit does."""

            return self._content

    class FakeIngestionService:
        """Capture the batch request submitted by the page."""

        def __init__(self) -> None:
            """Create an empty request log."""

            self.requests: list[object] = []

        def discover_source_candidates(self, source_path: str) -> tuple[str, ...]:
            """Return no server-side candidates for this upload-only test."""

            return ()

        def run_ingestion(self, request: object) -> IngestionOperationResult:
            """Capture selected uploaded files and return a fake success."""

            self.requests.append(request)
            return IngestionOperationResult(
                status="success",
                collection="shopping_guides",
                source_path="",
                force=False,
                exit_code=0,
                processed=2,
                trace_ids=("trace-1", "trace-2"),
                source_paths=("keep.md", "folder/guide.pdf"),
                summary={"processed": 2},
            )

    fake_ui = _FakeStreamlit()
    fake_ui.button_value = True
    fake_ui.uploaded_files = {
        "Choose files": [
            FakeUploadedFile("keep.md", b"# keep"),
            FakeUploadedFile("drop.md", b"# drop"),
        ],
        "Choose folder": [
            FakeUploadedFile("folder/guide.pdf", b"%PDF-1.4"),
        ],
    }
    fake_ui.checkbox_values = {
        "Force rebuild": False,
        "Ingest upload: keep.md": True,
        "Ingest upload: drop.md": False,
        "Ingest upload: folder/guide.pdf": True,
    }
    fake_service = FakeIngestionService()

    render_ingestion_manage_page(
        IngestionManagePageModel(
            collection_id="shopping_guides",
            raw_data_dir="data/raw/shopping_guides",
            documents=(),
        ),
        ui=fake_ui,
        ingestion_service=fake_service,
    )

    assert len(fake_service.requests) == 1
    request = fake_service.requests[0]
    assert [uploaded.filename for uploaded in request.uploaded_files] == [
        "keep.md",
        "folder/guide.pdf",
    ]
    assert request.source_paths == ()
    call_names = [name for name, _, _ in fake_ui.calls]
    assert call_names.count("file_uploader") == 2
    assert "success" in call_names


@pytest.mark.integration
def test_ingestion_operation_service_invokes_ingest_runner_with_dashboard_request(
    tmp_path: Path,
) -> None:
    """Require Dashboard ingestion service to call the real ingest entry contract."""

    from src.observability.services import (
        IngestionOperationRequest,
        IngestionOperationService,
    )

    source = tmp_path / "wireless.md"
    source.write_text("# Wireless\nChoose stable Bluetooth.", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_runner(
        argv: list[str],
        *,
        output: object,
        error_output: object,
    ) -> int:
        """Record CLI arguments and emit the same JSON shape as ingest.py."""

        calls.append(argv)
        output(
            json.dumps(
                {
                    "collection": "shopping_guides",
                    "force": True,
                    "processed": 1,
                    "results": [
                        {
                            "source": str(source),
                            "status": "success",
                            "trace_id": "trace-dashboard-ingest",
                            "summary": {"chunk_count": 1},
                        }
                    ],
                }
            )
        )
        return 0

    service = IngestionOperationService(runner=fake_runner)
    result = service.run_ingestion(
        IngestionOperationRequest(
            collection="shopping_guides",
            source_path=str(source),
            force=True,
        )
    )

    assert calls == [
        [
            "--path",
            str(source.resolve()),
            "--collection",
            "shopping_guides",
            "--force",
        ]
    ]
    assert result.status == "success"
    assert result.collection == "shopping_guides"
    assert result.processed == 1
    assert result.trace_ids == ("trace-dashboard-ingest",)
    assert result.source_paths == (str(source),)
    assert result.summary == {"chunk_count": 1}


@pytest.mark.integration
def test_ingestion_operation_service_returns_failed_result_for_blank_source() -> None:
    """Require invalid Dashboard ingestion input to return a renderable failure."""

    from src.observability.services import (
        IngestionOperationRequest,
        IngestionOperationService,
    )

    service = IngestionOperationService(runner=lambda *args, **kwargs: 0)

    result = service.run_ingestion(
        IngestionOperationRequest(
            collection="shopping_guides",
            source_path=" ",
            force=False,
        )
    )

    assert result.status == "failed"
    assert result.exit_code == 2
    assert result.error == "source_path must not be blank"


@pytest.mark.integration
def test_ingestion_operation_service_returns_failed_result_for_empty_folder(
    tmp_path: Path,
) -> None:
    """Require empty folder ingestion requests to explain there are no candidates."""

    from src.observability.services import (
        IngestionOperationRequest,
        IngestionOperationService,
    )

    service = IngestionOperationService(runner=lambda *args, **kwargs: 0)

    result = service.run_ingestion(
        IngestionOperationRequest(
            collection="shopping_guides",
            source_path=str(tmp_path),
        )
    )

    assert result.status == "failed"
    assert result.exit_code == 2
    assert result.error == "No supported ingestion sources selected."


@pytest.mark.integration
def test_ingestion_operation_service_saves_uploads_and_runs_each_selected_source(
    tmp_path: Path,
) -> None:
    """Require uploaded files and selected local paths to be ingested as a batch."""

    from src.observability.services import (
        IngestionOperationRequest,
        IngestionOperationService,
        UploadedIngestionFile,
    )

    local_source = tmp_path / "local.md"
    local_source.write_text("# Local", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_runner(
        argv: list[str],
        *,
        output: object,
        error_output: object,
    ) -> int:
        """Record each source-specific CLI call and emit an ingest result."""

        calls.append(argv)
        source = argv[argv.index("--path") + 1]
        output(
            json.dumps(
                {
                    "collection": "shopping_guides",
                    "force": False,
                    "processed": 1,
                    "results": [
                        {
                            "source": source,
                            "status": "success",
                            "trace_id": f"trace-{len(calls)}",
                            "summary": {"chunk_count": 1},
                        }
                    ],
                }
            )
        )
        return 0

    service = IngestionOperationService(
        runner=fake_runner,
        upload_root=tmp_path / "uploads",
    )

    result = service.run_ingestion(
        IngestionOperationRequest(
            collection="shopping_guides",
            source_paths=(str(local_source),),
            uploaded_files=(
                UploadedIngestionFile(filename="folder/upload.md", content=b"# Upload"),
            ),
        )
    )

    saved_upload = (
        tmp_path
        / "uploads"
        / "shopping_guides"
        / "dashboard_uploads"
        / "folder"
        / "upload.md"
    )
    assert saved_upload.read_bytes() == b"# Upload"
    assert [call[call.index("--path") + 1] for call in calls] == [
        str(local_source.resolve()),
        str(saved_upload.resolve()),
    ]
    assert result.status == "success"
    assert result.processed == 2
    assert result.trace_ids == ("trace-1", "trace-2")


@pytest.mark.integration
def test_ingestion_operation_service_discovers_supported_files_in_folder(
    tmp_path: Path,
) -> None:
    """Require folder paths to expand into supported ingestion candidates."""

    from src.observability.services import IngestionOperationService

    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "ignore.txt").write_text("ignore", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "c.markdown").write_text("# C", encoding="utf-8")

    service = IngestionOperationService(runner=lambda *args, **kwargs: 0)

    assert service.discover_source_candidates(str(tmp_path)) == (
        str((tmp_path / "a.md").resolve()),
        str((tmp_path / "b.pdf").resolve()),
        str((nested / "c.markdown").resolve()),
    )


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


@pytest.mark.integration
def test_data_browser_page_builds_and_renders_document_chunk_details() -> None:
    """Require data browser page to show documents, chunks, images, and source data."""

    from src.observability.pages.data_browser import (
        build_data_browser_page_model,
        render_data_browser_page,
    )
    from src.observability.services import (
        ChunkBrowserRow,
        DocumentBrowserRow,
        ImageBrowserRow,
    )

    document = DocumentBrowserRow(
        document_id="doc-data",
        collection_id="shopping_guides",
        title="Wireless guide",
        source_path="data/raw/wireless.md",
        source_hash="c" * 64,
        lifecycle_status="success",
        chunk_count=1,
        image_count=1,
    )
    chunk = ChunkBrowserRow(
        chunk_id="chunk-data",
        document_id="doc-data",
        collection_id="shopping_guides",
        chunk_index=0,
        text="Wireless headphones should balance comfort and battery life.",
        text_preview="Wireless headphones should balance comfort and battery life.",
        text_length=61,
        content_hash="d" * 64,
        start_offset=0,
        end_offset=61,
        dense_indexed=True,
        bm25_term_count=5,
        image_refs=("image-data",),
        metadata={"section_path": ["Audio"]},
    )
    image = ImageBrowserRow(
        image_id="image-data",
        file_path="data/images/shopping_guides/image-data.png",
        collection_id="shopping_guides",
        document_id="doc-data",
        page_num=1,
        width=640,
        height=480,
        mime_type="image/png",
        quality_status="ok",
    )

    class _DataBrowser:
        """Return fixed data-browser DTOs for page builder tests."""

        def list_documents(self, collection_id: str) -> list[DocumentBrowserRow]:
            """Return collection documents."""

            assert collection_id == "shopping_guides"
            return [document]

        def list_chunks(self, document_id: str) -> list[ChunkBrowserRow]:
            """Return chunks for the selected document."""

            assert document_id == "doc-data"
            return [chunk]

        def get_chunk_detail(self, chunk_id: str) -> ChunkBrowserRow | None:
            """Return the selected chunk detail."""

            assert chunk_id == "chunk-data"
            return chunk

        def list_images(self, collection_id: str) -> list[ImageBrowserRow]:
            """Return collection images."""

            assert collection_id == "shopping_guides"
            return [image]

    model = build_data_browser_page_model(
        data_browser=_DataBrowser(),
        collection_id="shopping_guides",
    )
    fake_ui = _FakeStreamlit()
    selection = render_data_browser_page(model, ui=fake_ui)

    assert model.documents == (document,)
    assert model.chunks == (chunk,)
    assert model.selected_chunk == chunk
    assert model.images == (image,)
    assert selection.document_id == "doc-data"
    assert selection.chunk_id == "chunk-data"
    document_rows = _dataframe_payload(fake_ui, 0)
    image_rows = _dataframe_payload(fake_ui, 2)
    assert document_rows[0]["created_at"] is None
    assert document_rows[0]["updated_at"] is None
    assert image_rows[0]["created_at"] is None
    assert image_rows[0]["updated_at"] is None
    call_names = [name for name, _, _ in fake_ui.calls]
    assert "title" in call_names
    assert call_names.count("dataframe") >= 3
    assert "write" in call_names


@pytest.mark.integration
def test_query_trace_page_builds_and_renders_retrieval_comparisons() -> None:
    """Require Query Trace page to show candidate frequency and stage flow."""

    from datetime import datetime

    from src.observability.pages.query_trace import (
        build_query_trace_page_model,
        render_query_trace_page,
    )
    from src.observability.services import (
        TraceDetail,
        TraceHistoryItem,
        TraceStageWaterfallItem,
    )

    history = TraceHistoryItem(
        trace_id="query-page",
        trace_type="query",
        collection_id="shopping_guides",
        status="success",
        display_input="wireless headphones",
        started_at=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        finished_at=None,
        duration_ms=128.0,
        stage_count=4,
        fallback_used=False,
    )
    detail = TraceDetail(
        trace_id="query-page",
        trace_type="query",
        collection_id="shopping_guides",
        status="success",
        display_input="wireless headphones",
        started_at=history.started_at,
        finished_at=None,
        duration_ms=128.0,
        waterfall=(
            TraceStageWaterfallItem(
                stage="dense",
                duration_ms=30.0,
                status="success",
                candidate_count=5,
                details={"chunk_ids": ["chunk-a", "chunk-b"]},
            ),
            TraceStageWaterfallItem(
                stage="sparse",
                duration_ms=18.0,
                status="success",
                candidate_count=4,
                details={"chunk_ids": ["chunk-a", "chunk-c"]},
            ),
            TraceStageWaterfallItem(
                stage="fusion",
                duration_ms=12.0,
                status="success",
                candidate_count=3,
                details={
                    "fused_candidates": [
                        {"chunk_id": "chunk-a", "rank": 1, "score": 0.81},
                        {"chunk_id": "chunk-c", "rank": 2, "score": 0.72},
                        {"chunk_id": "chunk-b", "rank": 3, "score": 0.61},
                    ],
                },
            ),
            TraceStageWaterfallItem(
                stage="filter",
                duration_ms=8.0,
                status="success",
                candidate_count=2,
                details={
                    "before_candidates": [
                        {"chunk_id": "chunk-a", "rank": 1, "score": 0.81},
                        {"chunk_id": "chunk-c", "rank": 2, "score": 0.72},
                        {"chunk_id": "chunk-b", "rank": 3, "score": 0.61},
                    ],
                    "after_candidates": [
                        {"chunk_id": "chunk-a", "rank": 1, "score": 0.81},
                        {"chunk_id": "chunk-b", "rank": 2, "score": 0.61},
                    ],
                    "rejected_candidates": [
                        {"chunk_id": "chunk-c", "reason": "doc_type_mismatch"},
                    ],
                },
            ),
            TraceStageWaterfallItem(
                stage="rerank",
                duration_ms=20.0,
                status="success",
                candidate_count=3,
                details={
                    "before_candidates": [
                        {"chunk_id": "chunk-a", "rank": 1, "score": 0.81},
                        {"chunk_id": "chunk-b", "rank": 2, "score": 0.61},
                    ],
                    "after_candidates": [
                        {"chunk_id": "chunk-b", "rank": 1, "score": 0.95},
                        {"chunk_id": "chunk-a", "rank": 2, "score": 0.91},
                    ],
                },
            ),
        ),
        candidate_counts={"dense": 5, "sparse": 4, "fusion": 6, "rerank": 3},
        query_result={
            "contexts": [
                {"chunk_id": "chunk-b", "score": 0.95, "rank": 1},
                {"chunk_id": "chunk-a", "score": 0.91, "rank": 2},
            ],
            "content": "[1] context",
            "citations": [],
            "images": [],
        },
        summary_metrics={"top_score": 0.95},
        evaluation_metrics={"query_document_relevance": 0.92},
        rerank_delta={"chunk-b": -1, "chunk-a": 1},
    )

    class _TraceReader:
        """Return fixed Query Trace DTOs for page builder tests."""

        def list_query_traces(self, collection_id: str) -> list[TraceHistoryItem]:
            """Return query trace history for the requested collection."""

            assert collection_id == "shopping_guides"
            return [history]

        def get_query_trace_detail(self, trace_id: str) -> TraceDetail | None:
            """Return selected query trace detail."""

            assert trace_id == "query-page"
            return detail

    model = build_query_trace_page_model(
        trace_reader=_TraceReader(),
        collection_id="shopping_guides",
    )
    fake_ui = _FakeStreamlit()
    selected_trace_id = render_query_trace_page(model, ui=fake_ui)

    assert model.history == (history,)
    assert model.selected_trace == detail
    assert selected_trace_id == "query-page"
    call_names = [name for name, _, _ in fake_ui.calls]
    assert "title" in call_names
    assert call_names.count("dataframe") >= 5
    assert "bar_chart" in call_names
    assert "metric" in call_names
    subheaders = [args[0] for name, args, _kwargs in fake_ui.calls if name == "subheader"]
    assert "Chunk Frequency Summary" in subheaders
    assert "Chunk Flow Matrix" in subheaders
    assert "Final Context" in subheaders
    history_rows = _dataframe_payload(fake_ui, 0)
    assert history_rows[0]["started_at"] == history.started_at
    assert history_rows[0]["finished_at"] is None
    text_area_call = next(
        (args, kwargs)
        for name, args, kwargs in fake_ui.calls
        if name == "text_area"
    )
    assert text_area_call[0] == ("query_result.content",)
    assert text_area_call[1]["value"] == "[1] context"
    assert text_area_call[1]["disabled"] is True
    dataframe_payloads = [
        args[0] for name, args, _kwargs in fake_ui.calls if name == "dataframe"
    ]
    frequency_rows = next(
        rows for rows in dataframe_payloads if rows and "appeared_count" in rows[0]
    )
    flow_rows = next(rows for rows in dataframe_payloads if rows and "fusion_rank" in rows[0])
    assert frequency_rows[0] == {
        "chunk_id": "chunk-a",
        "appeared_count": 8,
        "stages": (
            "dense, sparse, fusion, filter_before, filter_after, "
            "rerank_before, rerank_after, final"
        ),
        "final_rank": 2,
        "best_score": 0.91,
        "filtered_reason": "",
    }
    assert flow_rows == [
        {
            "chunk_id": "chunk-b",
            "dense": "hit",
            "sparse": "",
            "fusion_rank": 3,
            "filter": "kept",
            "rerank_rank": 1,
            "final_rank": 1,
        },
        {
            "chunk_id": "chunk-a",
            "dense": "hit",
            "sparse": "hit",
            "fusion_rank": 1,
            "filter": "kept",
            "rerank_rank": 2,
            "final_rank": 2,
        },
        {
            "chunk_id": "chunk-c",
            "dense": "",
            "sparse": "hit",
            "fusion_rank": 2,
            "filter": "rejected:doc_type_mismatch",
            "rerank_rank": None,
            "final_rank": None,
        },
    ]
    _args, selectbox_kwargs = next(
        (args, kwargs)
        for name, args, kwargs in fake_ui.calls
        if name == "selectbox"
    )
    assert selectbox_kwargs["key"] == "query_trace_id"
    assert selectbox_kwargs["index"] == 0


@pytest.mark.integration
def test_ingestion_trace_page_builds_and_renders_stage_timing() -> None:
    """Require Ingestion Trace page to show history, timing, and processing stats."""

    from datetime import datetime

    from src.observability.pages.ingestion_trace import (
        build_ingestion_trace_page_model,
        render_ingestion_trace_page,
    )
    from src.observability.services import (
        TraceDetail,
        TraceHistoryItem,
        TraceStageWaterfallItem,
        TraceTransformSnapshotItem,
        TraceTransformStepItem,
    )

    history = TraceHistoryItem(
        trace_id="ingestion-page",
        trace_type="ingestion",
        collection_id="shopping_guides",
        status="success",
        display_input="data/raw/wireless.md",
        started_at=datetime(2026, 1, 1, 7, 0, tzinfo=UTC),
        finished_at=None,
        duration_ms=420.0,
        stage_count=3,
        fallback_used=False,
    )
    detail = TraceDetail(
        trace_id="ingestion-page",
        trace_type="ingestion",
        collection_id="shopping_guides",
        status="success",
        display_input="data/raw/wireless.md",
        started_at=history.started_at,
        finished_at=None,
        duration_ms=420.0,
        waterfall=(
            TraceStageWaterfallItem(stage="load", duration_ms=40.0, status="success"),
            TraceStageWaterfallItem(
                stage="transform",
                duration_ms=235.0,
                status="success",
            ),
            TraceStageWaterfallItem(stage="upsert", duration_ms=120.0, status="success"),
        ),
        transform_steps=(
            TraceTransformStepItem(
                name="metadata_enrich",
                duration_ms=5.0,
                status="success",
                input_count=4,
                output_count=4,
                changed_count=3,
                unchanged_count=1,
                method="transform",
                provider="MetadataEnricher",
            ),
            TraceTransformStepItem(
                name="rewrite_chunk",
                duration_ms=230.0,
                status="success",
                input_count=4,
                output_count=4,
                changed_count=3,
                unchanged_count=1,
                method="transform",
                provider="ChunkRewriter",
                snapshots=(
                    TraceTransformSnapshotItem(
                        step_name="rewrite_chunk",
                        step_color="#8B5CF6",
                        chunk_id="chunk-1",
                        chunk_index=0,
                        change_type="changed",
                        before_preview="Original buying guide.",
                        after_preview="Rewritten buying guide.",
                        before_truncated=False,
                        after_truncated=False,
                    ),
                ),
            ),
            TraceTransformStepItem(
                name="image_captioner",
                duration_ms=35.0,
                status="success",
                input_count=4,
                output_count=4,
                changed_count=0,
                unchanged_count=4,
                method="transform",
                provider="ImageCaptioner",
                details={
                    "provider": "dashscope",
                    "model": "qwen-vl-max",
                    "image_count": 3,
                    "caption_count": 0,
                    "status_counts": {"failed": 3},
                    "failures": [
                        {
                            "image_id": "image-1",
                            "status": "failed",
                            "reason": "provider unavailable",
                        }
                    ],
                },
            ),
        ),
        summary_metrics={
            "document_status": "success",
            "chunk_count": 4,
            "embedded_count": 4,
            "skipped_count": 0,
        },
        evaluation_metrics={"embedding_coverage": 1.0, "index_ready": True},
    )

    class _TraceReader:
        """Return fixed Ingestion Trace DTOs for page builder tests."""

        def list_ingestion_traces(self, collection_id: str) -> list[TraceHistoryItem]:
            """Return ingestion trace history for the requested collection."""

            assert collection_id == "shopping_guides"
            return [history]

        def get_ingestion_trace_detail(self, trace_id: str) -> TraceDetail | None:
            """Return selected ingestion trace detail."""

            assert trace_id == "ingestion-page"
            return detail

    model = build_ingestion_trace_page_model(
        trace_reader=_TraceReader(),
        collection_id="shopping_guides",
    )
    fake_ui = _FakeStreamlit()
    selected_trace_id = render_ingestion_trace_page(model, ui=fake_ui)

    assert model.history == (history,)
    assert model.selected_trace == detail
    assert selected_trace_id == "ingestion-page"
    call_names = [name for name, _, _ in fake_ui.calls]
    assert "title" in call_names
    assert call_names.count("dataframe") >= 3
    assert call_names.count("bar_chart") == 2
    assert any(
        args == ("Transform Breakdown",)
        for name, args, _kwargs in fake_ui.calls
        if name == "subheader"
    )
    assert any(
        args == ("Transform Result Diff",)
        for name, args, _kwargs in fake_ui.calls
        if name == "subheader"
    )
    diff_markdown = "\n".join(
        str(args[0])
        for name, args, _kwargs in fake_ui.calls
        if name == "markdown"
    )
    assert "transform-diff-card" in diff_markdown
    assert "border-left:6px solid #8B5CF6" in diff_markdown
    assert "transform-diff-removed" in diff_markdown
    assert "transform-diff-added" in diff_markdown
    transform_tables = [
        args[0]
        for name, args, _kwargs in fake_ui.calls
        if name == "dataframe"
        and isinstance(args[0], list)
        and args[0]
        and isinstance(args[0][0], dict)
        and "name" in args[0][0]
    ]
    assert transform_tables[0][2]["details"]["model"] == "qwen-vl-max"
    assert "Original" in diff_markdown
    assert "Rewritten" in diff_markdown
    assert "buying guide." in diff_markdown
    history_rows = _dataframe_payload(fake_ui, 0)
    transform_rows = _dataframe_payload(fake_ui, 2)
    assert history_rows[0]["started_at"] == history.started_at
    assert history_rows[0]["finished_at"] is None
    assert transform_rows[1]["changed_count"] == 3
    assert transform_rows[1]["unchanged_count"] == 1
    assert "metric" in call_names
    assert "write" in call_names
    _args, selectbox_kwargs = next(
        (args, kwargs)
        for name, args, kwargs in fake_ui.calls
        if name == "selectbox"
    )
    assert selectbox_kwargs["key"] == "ingestion_trace_id"
    assert selectbox_kwargs["index"] == 0


@pytest.mark.integration
def test_transform_result_diff_highlights_chinese_changes_without_strikethrough() -> None:
    """Require readable Chinese diff highlights without crossing out paragraphs."""

    from src.observability.pages.ingestion_trace import _highlight_preview_diff

    before_html, after_html = _highlight_preview_diff(
        before="无线耳机选购核心不是参数越高越好，应关注音质和降噪。",
        after="无线耳机选购应关注佩戴、连接稳定性和降噪。",
    )

    assert "无线耳机选购" in before_html
    assert "无线耳机选购" in after_html
    assert "transform-diff-equal" in before_html
    assert "transform-diff-equal" in after_html
    assert "style='color:#0F172A;'" in before_html
    assert "style='color:#0F172A;'" in after_html
    assert "!important" not in before_html
    assert "-webkit-text-fill-color" not in before_html
    assert "transform-diff-removed" in before_html
    assert "transform-diff-added" in after_html
    assert "text-decoration:line-through" not in before_html
    assert "text-decoration:line-through" not in after_html


@pytest.mark.integration
def test_trace_page_models_honor_current_collection_selection_and_reject_stale_ids() -> None:
    """Trace builders should load valid selections and reject stale state."""

    from src.observability.pages.ingestion_trace import (
        build_ingestion_trace_page_model,
    )
    from src.observability.pages.query_trace import build_query_trace_page_model
    from src.observability.services import TraceHistoryItem

    newest_query = TraceHistoryItem(
        trace_id="query-newest",
        trace_type="query",
        collection_id="shopping_guides",
        status="success",
        display_input="newest query",
        started_at=datetime(2026, 1, 2, tzinfo=UTC),
        finished_at=None,
        duration_ms=1.0,
        stage_count=1,
        fallback_used=False,
    )
    selected_query = TraceHistoryItem(
        trace_id="query-selected",
        trace_type="query",
        collection_id="shopping_guides",
        status="success",
        display_input="selected query",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=None,
        duration_ms=2.0,
        stage_count=1,
        fallback_used=False,
    )
    newest_ingestion = TraceHistoryItem(
        trace_id="ingestion-newest",
        trace_type="ingestion",
        collection_id="shopping_guides",
        status="success",
        display_input="newest.pdf",
        started_at=datetime(2026, 1, 2, tzinfo=UTC),
        finished_at=None,
        duration_ms=3.0,
        stage_count=1,
        fallback_used=False,
    )
    selected_ingestion = TraceHistoryItem(
        trace_id="ingestion-selected",
        trace_type="ingestion",
        collection_id="shopping_guides",
        status="success",
        display_input="selected.pdf",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=None,
        duration_ms=4.0,
        stage_count=1,
        fallback_used=False,
    )

    class _TraceReader:
        """Return two records per trace type and record requested details."""

        def list_query_traces(self, _collection_id: str) -> list[TraceHistoryItem]:
            """Return newest-first Query Trace history."""

            return [newest_query, selected_query]

        def list_ingestion_traces(self, _collection_id: str) -> list[TraceHistoryItem]:
            """Return newest-first Ingestion Trace history."""

            return [newest_ingestion, selected_ingestion]

        def get_query_trace_detail(self, trace_id: str) -> Mock:
            """Return a detail marker for the requested Query Trace."""

            return Mock(trace_id=trace_id)

        def get_ingestion_trace_detail(self, trace_id: str) -> Mock:
            """Return a detail marker for the requested Ingestion Trace."""

            return Mock(trace_id=trace_id)

    reader = _TraceReader()

    query_model = build_query_trace_page_model(
        trace_reader=reader,
        collection_id="shopping_guides",
        trace_id="query-selected",
    )
    ingestion_model = build_ingestion_trace_page_model(
        trace_reader=reader,
        collection_id="shopping_guides",
        trace_id="ingestion-selected",
    )
    stale_query_model = build_query_trace_page_model(
        trace_reader=reader,
        collection_id="shopping_guides",
        trace_id="query-from-other-collection",
    )
    stale_ingestion_model = build_ingestion_trace_page_model(
        trace_reader=reader,
        collection_id="shopping_guides",
        trace_id="ingestion-from-other-collection",
    )

    assert query_model.selected_trace.trace_id == "query-selected"
    assert ingestion_model.selected_trace.trace_id == "ingestion-selected"
    assert stale_query_model.selected_trace.trace_id == "query-newest"
    assert stale_ingestion_model.selected_trace.trace_id == "ingestion-newest"


@pytest.mark.integration
def test_evaluation_page_builds_and_renders_metric_trends() -> None:
    """Require Evaluation page to show runs, metric details, and trends."""

    from datetime import datetime

    from src.observability.pages.evaluation import (
        build_evaluation_page_model,
        render_evaluation_page,
    )
    from src.observability.services import (
        EvaluationMetricTrendPoint,
        EvaluationRunDetail,
        EvaluationRunSummary,
    )

    run = EvaluationRunSummary(
        run_id="eval-page",
        collection_id="shopping_guides",
        evaluator="fake",
        dataset_name="golden",
        status="success",
        started_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        finished_at=None,
        created_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        metric_count=2,
        metrics={"hit_rate_at_k": 0.95, "mrr": 0.88},
        summary={"sample_count": 10},
    )
    detail = EvaluationRunDetail(
        run_id="eval-page",
        collection_id="shopping_guides",
        evaluator="fake",
        dataset_name="golden",
        status="success",
        started_at=run.started_at,
        finished_at=None,
        created_at=run.created_at,
        metrics=run.metrics,
        metric_details={"mrr": {"sample_count": 10}},
        settings_snapshot={"retrieval": "hybrid"},
        summary=run.summary,
    )
    trends = {
        "hit_rate_at_k": (
            EvaluationMetricTrendPoint(
                run_id="eval-page",
                metric_name="hit_rate_at_k",
                metric_value=0.95,
                evaluator="fake",
                dataset_name="golden",
                status="success",
                created_at=run.created_at,
            ),
        ),
        "mrr": (
            EvaluationMetricTrendPoint(
                run_id="eval-page",
                metric_name="mrr",
                metric_value=0.88,
                evaluator="fake",
                dataset_name="golden",
                status="success",
                created_at=run.created_at,
            ),
        ),
    }

    class _EvaluationService:
        """Return fixed evaluation DTOs for page builder tests."""

        def list_runs(self, collection_id: str) -> list[EvaluationRunSummary]:
            """Return evaluation runs for the requested collection."""

            assert collection_id == "shopping_guides"
            return [run]

        def get_run_detail(self, run_id: str) -> EvaluationRunDetail | None:
            """Return selected evaluation run detail."""

            assert run_id == "eval-page"
            return detail

        def metric_trends(
            self,
            collection_id: str,
        ) -> dict[str, tuple[EvaluationMetricTrendPoint, ...]]:
            """Return metric trends for the requested collection."""

            assert collection_id == "shopping_guides"
            return trends

    model = build_evaluation_page_model(
        evaluation_service=_EvaluationService(),
        collection_id="shopping_guides",
    )
    fake_ui = _FakeStreamlit()
    fake_ui.button_value = True
    runner_calls: list[str] = []

    def _run_evaluation(collection_id: str) -> dict[str, object]:
        """Return a deterministic Dashboard evaluation result."""

        runner_calls.append(collection_id)
        return {
            "collection": collection_id,
            "status": "success",
            "run_id": "eval-new",
            "summary": {"sample_count": 3},
        }

    selection = render_evaluation_page(
        model,
        ui=fake_ui,
        evaluation_runner=_run_evaluation,
    )

    assert model.runs == (run,)
    assert model.selected_run == detail
    assert model.metric_trends == trends
    assert selection.run_id == "eval-page"
    assert selection.request_run is True
    assert runner_calls == ["shopping_guides"]
    run_rows = _dataframe_payload(fake_ui, 0)
    trend_rows = _dataframe_payload(fake_ui, 2)
    assert run_rows[0]["started_at"] == run.started_at
    assert run_rows[0]["created_at"] == run.created_at
    assert trend_rows[0]["created_at"] == run.created_at
    success_payload = next(
        args[0] for name, args, _kwargs in fake_ui.calls if name == "success"
    )
    assert success_payload["run_id"] == "eval-new"
    assert success_payload["summary"]["sample_count"] == 3
    call_names = [name for name, _, _ in fake_ui.calls]
    assert "title" in call_names
    assert call_names.count("dataframe") >= 3
    assert "bar_chart" in call_names
    assert "metric" in call_names


@pytest.mark.integration
def test_dashboard_app_loads_all_configured_page_modules() -> None:
    """Require the Streamlit app entry to import every Dashboard page module.

    F11 protects the executable app boundary instead of a single page. A
    failure here means the local Dashboard script could start Streamlit but
    break before operators can reach one of the six required pages.
    """

    from src.observability.dashboard.app import DASHBOARD_PAGE_MODULES, main

    fake_ui = _FakeStreamlit()

    loaded_pages = main(
        ui=fake_ui,
        page_renderer=lambda _page_name, _ui: None,
    )

    assert loaded_pages == DASHBOARD_PAGE_MODULES
    assert DASHBOARD_PAGE_MODULES == (
        "overview",
        "ingestion_manage",
        "data_browser",
        "query_trace",
        "ingestion_trace",
        "evaluation",
    )
    call_names = [name for name, _, _ in fake_ui.calls]
    assert "title" in call_names
    assert "caption" in call_names


@pytest.mark.integration
def test_dashboard_app_renders_sidebar_navigation_and_selected_page() -> None:
    """Require the app shell to expose all six pages in real navigation.

    Loading page modules is not enough for operators. The Streamlit app must
    present a sidebar page selector and dispatch the selected page renderer so
    the browser can reach System Overview, Ingestion Management, Data Browser,
    Query Trace, Ingestion Trace, and Evaluation.
    """

    from src.observability.dashboard.app import DASHBOARD_PAGE_LABELS, main

    fake_ui = _FakeStreamlit()
    rendered_pages: list[str] = []

    loaded_pages = main(
        ui=fake_ui,
        page_renderer=lambda page_name, _ui: rendered_pages.append(page_name),
    )

    assert tuple(DASHBOARD_PAGE_LABELS) == loaded_pages
    assert rendered_pages == ["overview"]
    radio_args, radio_kwargs = next(
        (args, kwargs)
        for name, args, kwargs in fake_ui.calls
        if name == "radio"
    )
    assert radio_args == ("Page",)
    assert radio_kwargs["options"] == list(DASHBOARD_PAGE_LABELS)
    assert radio_kwargs["format_func"]("query_trace") == "Query Trace"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("page_name", "session_key", "selected_trace_id", "builder_name", "renderer_name"),
    [
        (
            "query_trace",
            "query_trace_id",
            "query-selected",
            "build_query_trace_page_model",
            "render_query_trace_page",
        ),
        (
            "ingestion_trace",
            "ingestion_trace_id",
            "ingestion-selected",
            "build_ingestion_trace_page_model",
            "render_ingestion_trace_page",
        ),
    ],
)
def test_dashboard_trace_dispatch_uses_session_state_selection(
    monkeypatch: pytest.MonkeyPatch,
    page_name: str,
    session_key: str,
    selected_trace_id: str,
    builder_name: str,
    renderer_name: str,
) -> None:
    """Trace page reruns must rebuild detail for the selected widget value."""

    from src.observability.dashboard import app as dashboard_app

    settings = SimpleNamespace(
        project=SimpleNamespace(default_collection="shopping_guides"),
        database=object(),
    )
    pool = Mock()
    trace_reader = Mock()
    page_model = object()
    builder = Mock(return_value=page_model)
    renderer = Mock()
    monkeypatch.setattr(
        dashboard_app,
        "load_settings",
        lambda **_kwargs: settings,
    )
    monkeypatch.setattr(
        dashboard_app.PostgresPool,
        "from_settings",
        lambda _settings: pool,
    )
    monkeypatch.setattr(dashboard_app, "init_schema", Mock())
    monkeypatch.setattr(dashboard_app, "ConfigReaderService", Mock())
    monkeypatch.setattr(dashboard_app, "DataBrowserService", Mock())
    monkeypatch.setattr(
        dashboard_app,
        "TraceReaderService",
        Mock(return_value=trace_reader),
    )
    monkeypatch.setattr(dashboard_app, "EvaluationService", Mock())
    monkeypatch.setattr(dashboard_app, "IngestionOperationService", Mock())
    monkeypatch.setattr(dashboard_app, builder_name, builder)
    monkeypatch.setattr(dashboard_app, renderer_name, renderer)
    ui = SimpleNamespace(session_state={session_key: selected_trace_id})

    dashboard_app.render_dashboard_page(page_name, ui)

    builder.assert_called_once_with(
        trace_reader=trace_reader,
        collection_id="shopping_guides",
        trace_id=selected_trace_id,
    )
    renderer.assert_called_once_with(page_model, ui=ui)
    pool.close.assert_called_once_with()


@pytest.mark.integration
def test_dashboard_evaluation_runner_executes_collection_golden_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require Dashboard Evaluation to delegate to the real CLI orchestration."""

    from src.observability.dashboard import app as dashboard_app

    calls: list[list[str]] = []

    def _fake_cli(
        argv: list[str],
        *,
        output: object,
        error_output: object,
    ) -> int:
        """Capture CLI arguments and emit a JSON run payload."""

        calls.append(argv)
        output(
            json.dumps(
                {
                    "run_id": "eval-cli",
                    "collection": "shopping_guides",
                    "status": "success",
                    "summary": {"sample_count": 3},
                }
            )
        )
        return 0

    monkeypatch.setattr(dashboard_app, "run_evaluation_cli", _fake_cli)

    result = dashboard_app.run_dashboard_evaluation("shopping_guides")

    assert calls == [["--collection", "shopping_guides"]]
    assert result["status"] == "success"
    assert result["run_id"] == "eval-cli"
    assert result["summary"]["sample_count"] == 3
    assert result["exit_code"] == 0


@pytest.mark.integration
def test_run_dashboard_dry_run_loads_app_and_prints_streamlit_command() -> None:
    """Require the launcher to verify the app and avoid browser startup in tests."""

    from src.scripts.run_dashboard import run_dashboard

    output: list[str] = []

    exit_code = run_dashboard(["--dry-run", "--port", "8502"], output=output.append)

    assert exit_code == 0
    payload = json.loads(output[0])
    assert payload["app_path"].endswith("src/observability/dashboard/app.py")
    assert payload["loaded_pages"] == [
        "overview",
        "ingestion_manage",
        "data_browser",
        "query_trace",
        "ingestion_trace",
        "evaluation",
    ]
    assert payload["command"][:3] == [sys.executable, "-m", "streamlit"]
    assert "--server.port" in payload["command"]
    assert "8502" in payload["command"]
    assert "--server.headless" in payload["command"]
    assert "true" in payload["command"]


@pytest.mark.integration
def test_run_dashboard_invokes_injected_command_runner_without_opening_browser() -> None:
    """Require non-dry launch mode to be testable without starting Streamlit."""

    from src.scripts.run_dashboard import run_dashboard

    commands: list[list[str]] = []

    def _record_command(command: list[str]) -> int:
        """Capture the generated Streamlit command instead of executing it."""

        commands.append(command)
        return 0

    exit_code = run_dashboard(
        ["--port", "8503"],
        command_runner=_record_command,
        output=lambda _message: None,
    )

    assert exit_code == 0
    assert len(commands) == 1
    assert commands[0][:3] == [sys.executable, "-m", "streamlit"]
    assert "--server.port" in commands[0]
    assert "8503" in commands[0]
