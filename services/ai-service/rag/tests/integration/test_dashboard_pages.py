"""Verify the six Dashboard pages render from service-backed test data.

F12 is the final Dashboard acceptance gate before later AImodel integration
work. The tests in this module use the same PostgreSQL projections, settings
reader, trace reader, and evaluation service that the local Streamlit Dashboard
uses at runtime. Rendering still goes through a fake Streamlit recorder so the
test can validate page behavior without launching a browser.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = RAG_ROOT / "config" / "settings.example.yaml"
VECTOR_DIMENSIONS = 1536
sys.path.insert(0, str(RAG_ROOT))


@dataclass(frozen=True, slots=True)
class DashboardFixture:
    """Describe the database rows seeded for one Dashboard page test."""

    collection_id: str
    document_id: str
    chunk_id: str
    image_id: str
    query_trace_id: str
    ingestion_trace_id: str
    evaluation_run_id: str


class FakeStreamlit:
    """Record Streamlit-like calls without starting a browser session.

    The fake intentionally implements only the UI methods used by the six
    Dashboard pages. Selectors return the first available option so tests can
    verify selected IDs deterministically.
    """

    def __init__(self) -> None:
        """Create an empty call log with deterministic widget defaults."""

        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.text_input_value = ""
        self.checkbox_value = False
        self.button_value = False
        self.uploaded_files: dict[str, list[object]] = {}

    def title(self, *args: object, **kwargs: object) -> None:
        """Record a page title call."""

        self.calls.append(("title", args, kwargs))

    def caption(self, *args: object, **kwargs: object) -> None:
        """Record contextual page caption text."""

        self.calls.append(("caption", args, kwargs))

    def subheader(self, *args: object, **kwargs: object) -> None:
        """Record a page section header call."""

        self.calls.append(("subheader", args, kwargs))

    def dataframe(self, *args: object, **kwargs: object) -> None:
        """Record a table render call."""

        self.calls.append(("dataframe", args, kwargs))

    def metric(self, *args: object, **kwargs: object) -> None:
        """Record a metric display call."""

        self.calls.append(("metric", args, kwargs))

    def bar_chart(self, *args: object, **kwargs: object) -> None:
        """Record a chart render call."""

        self.calls.append(("bar_chart", args, kwargs))

    def write(self, *args: object, **kwargs: object) -> None:
        """Record structured diagnostic output."""

        self.calls.append(("write", args, kwargs))

    def info(self, *args: object, **kwargs: object) -> None:
        """Record informational empty or pending states."""

        self.calls.append(("info", args, kwargs))

    def warning(self, *args: object, **kwargs: object) -> None:
        """Record warning state output."""

        self.calls.append(("warning", args, kwargs))

    def text_input(self, *args: object, **kwargs: object) -> str:
        """Record a text input and return the configured fake value."""

        self.calls.append(("text_input", args, kwargs))
        return self.text_input_value or str(kwargs.get("value", ""))

    def checkbox(self, *args: object, **kwargs: object) -> bool:
        """Record a checkbox and return the configured fake value."""

        self.calls.append(("checkbox", args, kwargs))
        return self.checkbox_value

    def button(self, *args: object, **kwargs: object) -> bool:
        """Record a button and return the configured fake value."""

        self.calls.append(("button", args, kwargs))
        return self.button_value

    def file_uploader(self, *args: object, **kwargs: object) -> list[object]:
        """Record file uploader calls and return configured uploaded files."""

        self.calls.append(("file_uploader", args, kwargs))
        label = str(args[0]) if args else ""
        return self.uploaded_files.get(label, [])

    def selectbox(self, *args: object, **kwargs: object) -> object | None:
        """Record a selector and return the first available option."""

        self.calls.append(("selectbox", args, kwargs))
        options = kwargs.get("options")
        if options is None and len(args) >= 2:
            options = args[1]
        if not options:
            return None
        return tuple(options)[0]


def _database_settings() -> object:
    """Build PostgreSQL settings used by service-backed Dashboard tests."""

    from src.core.config import DatabaseSettings

    return DatabaseSettings(
        provider="postgresql",
        url_env="DATABASE_URL",
        pool_size=3,
        echo_sql=False,
    )


@pytest.mark.integration
def test_dashboard_six_pages_render_from_services_and_test_database(
    tmp_path: Path,
) -> None:
    """Require all six Dashboard pages to render from real service DTOs.

    The seeded rows cover every page dependency: configuration overview,
    document/chunk/image browsing, query trace history, ingestion trace
    history, and evaluation trend data. A failure here means the Dashboard is
    no longer ready for the H1 pre-AImodel integration gate.
    """

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for Dashboard page integration")

    from src.core.config import load_settings
    from src.observability.pages.data_browser import (
        build_data_browser_page_model,
        render_data_browser_page,
    )
    from src.observability.pages.evaluation import (
        build_evaluation_page_model,
        render_evaluation_page,
    )
    from src.observability.pages.ingestion_manage import (
        build_ingestion_manage_page_model,
        render_ingestion_manage_page,
    )
    from src.observability.pages.ingestion_trace import (
        build_ingestion_trace_page_model,
        render_ingestion_trace_page,
    )
    from src.observability.pages.overview import (
        build_overview_page_model,
        render_overview_page,
    )
    from src.observability.pages.query_trace import (
        build_query_trace_page_model,
        render_query_trace_page,
    )
    from src.observability.services import (
        ConfigReaderService,
        DataBrowserService,
        EvaluationService,
        TraceReaderService,
    )
    from src.storage.postgres import PostgresPool, init_schema

    pool = PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        fixture = _seed_dashboard_fixture(pool, tmp_path)
        settings = load_settings(SETTINGS_PATH, validate_environment=False)
        config_reader = ConfigReaderService(settings_loader=lambda: settings)
        data_browser = DataBrowserService(pool)
        trace_reader = TraceReaderService(pool)
        evaluation_service = EvaluationService(pool)

        overview_model = build_overview_page_model(
            config_reader=config_reader,
            data_browser=data_browser,
            trace_reader=trace_reader,
            collection_id=fixture.collection_id,
        )
        ingestion_model = build_ingestion_manage_page_model(
            config_reader=config_reader,
            data_browser=data_browser,
            collection_id=fixture.collection_id,
        )
        data_model = build_data_browser_page_model(
            data_browser=data_browser,
            collection_id=fixture.collection_id,
        )
        query_model = build_query_trace_page_model(
            trace_reader=trace_reader,
            collection_id=fixture.collection_id,
        )
        ingestion_trace_model = build_ingestion_trace_page_model(
            trace_reader=trace_reader,
            collection_id=fixture.collection_id,
        )
        evaluation_model = build_evaluation_page_model(
            evaluation_service=evaluation_service,
            collection_id=fixture.collection_id,
        )

        fake_ui = FakeStreamlit()
        ingestion_selection = render_ingestion_manage_page(
            ingestion_model,
            ui=fake_ui,
        )
        data_selection = render_data_browser_page(data_model, ui=fake_ui)
        query_trace_id = render_query_trace_page(query_model, ui=fake_ui)
        ingestion_trace_id = render_ingestion_trace_page(
            ingestion_trace_model,
            ui=fake_ui,
        )
        evaluation_selection = render_evaluation_page(
            evaluation_model,
            ui=fake_ui,
        )
        render_overview_page(overview_model, ui=fake_ui)

        assert overview_model.collection_stats.document_count == 1
        assert overview_model.collection_stats.chunk_count == 2
        assert overview_model.latest_query.trace_id == fixture.query_trace_id
        assert overview_model.latest_ingestion.trace_id == fixture.ingestion_trace_id

        assert ingestion_model.documents[0].document_id == fixture.document_id
        assert ingestion_selection.delete_document_id == fixture.document_id
        assert data_model.selected_chunk is not None
        assert data_model.selected_chunk.chunk_id == fixture.chunk_id
        assert data_selection.document_id == fixture.document_id
        assert data_selection.chunk_id == fixture.chunk_id
        assert data_model.images[0].image_id == fixture.image_id

        assert query_trace_id == fixture.query_trace_id
        assert query_model.selected_trace is not None
        assert query_model.selected_trace.candidate_counts["dense"] == 3
        assert ingestion_trace_id == fixture.ingestion_trace_id
        assert ingestion_trace_model.selected_trace is not None
        assert ingestion_trace_model.selected_trace.summary_metrics["chunk_count"] == 2

        assert evaluation_selection.run_id == fixture.evaluation_run_id
        assert evaluation_model.selected_run is not None
        assert evaluation_model.selected_run.metrics["mrr"] == 0.87
        assert "hit_rate_at_k" in evaluation_model.metric_trends

        _assert_rendered_titles(
            fake_ui,
            {
                "System Overview",
                "Ingestion Management",
                "Data Browser",
                "Query Trace",
                "Ingestion Trace",
                "Evaluation",
            },
        )
        _assert_call_count_at_least(fake_ui, "dataframe", 13)
        _assert_call_count_at_least(fake_ui, "metric", 12)
        _assert_call_count_at_least(fake_ui, "bar_chart", 3)
    finally:
        with pool.transaction() as connection:
            if "fixture" in locals():
                connection.execute(
                    "DELETE FROM rag_collections WHERE id = %s",
                    (fixture.collection_id,),
                )
        pool.close()


def _seed_dashboard_fixture(pool: object, tmp_path: Path) -> DashboardFixture:
    """Insert a complete Dashboard fixture into PostgreSQL.

    Args:
        pool: Open ``PostgresPool`` used by repositories and services.
        tmp_path: Pytest temporary directory used for image file storage.

    Returns:
        Stable identifiers for rows that page assertions should select.
    """

    from src.core.types import Chunk, Document
    from src.ingestion.embedding import BM25Indexer
    from src.libs.vector_store import PgVectorStore
    from src.observability.services import EvaluationService
    from src.storage.bm25_storage import BM25Storage
    from src.storage.image_storage import ImageStorage
    from src.storage.repositories import (
        ChunkRepository,
        DocumentRepository,
        TraceRepository,
    )

    collection_id = f"f12-dashboard-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    image_id = f"image-{uuid4().hex}"
    source_path = f"data/raw/{collection_id}/wireless-guide.md"
    source_hash = sha256(source_path.encode("utf-8")).hexdigest()
    query_trace_id = f"query-{uuid4().hex}"
    ingestion_trace_id = f"ingestion-{uuid4().hex}"
    evaluation_run_id = f"eval-{uuid4().hex}"

    document = Document(
        id=document_id,
        text=(
            "# Wireless Headphones Guide\n"
            "Comfort, stable Bluetooth, and battery life matter most."
        ),
        metadata={
            "title": "Wireless headphones guide",
            "doc_type": "buying_guide",
            "images": [
                {
                    "id": image_id,
                    "path": "wireless-headphones.png",
                    "page": 1,
                    "text_offset": 32,
                    "text_length": 24,
                    "position": {"x": 10, "y": 20, "width": 320, "height": 180},
                }
            ],
        },
    )
    chunks = [
        Chunk(
            id=f"chunk-{uuid4().hex}",
            text="Wireless headphones should balance comfort and battery life.",
            metadata={
                "collection": collection_id,
                "doc_type": "buying_guide",
                "image_refs": [image_id],
            },
            chunk_index=0,
            start_offset=0,
            end_offset=61,
            source_ref={
                "document_id": document_id,
                "source_path": source_path,
                "section_path": ["Wireless Headphones Guide"],
                "collection": collection_id,
            },
        ),
        Chunk(
            id=f"chunk-{uuid4().hex}",
            text="Stable Bluetooth and clear calls are useful for commuting.",
            metadata={
                "collection": collection_id,
                "doc_type": "buying_guide",
            },
            chunk_index=1,
            start_offset=62,
            end_offset=120,
            source_ref={
                "document_id": document_id,
                "source_path": source_path,
                "section_path": ["Wireless Headphones Guide", "Commute"],
                "collection": collection_id,
            },
        ),
    ]

    documents = DocumentRepository(pool)
    chunk_repository = ChunkRepository(pool)
    documents.upsert(
        document,
        collection_id=collection_id,
        source_path=source_path,
        source_hash=source_hash,
        title="Wireless headphones guide",
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
        metadata={"caption": "Black wireless headphones on a desk."},
    )
    documents.mark_success(document_id)

    _seed_trace_rows(
        TraceRepository(pool),
        collection_id=collection_id,
        query_trace_id=query_trace_id,
        ingestion_trace_id=ingestion_trace_id,
        source_path=source_path,
        source_hash=source_hash,
    )
    _seed_evaluation_run(
        EvaluationService(pool),
        collection_id=collection_id,
        run_id=evaluation_run_id,
    )
    return DashboardFixture(
        collection_id=collection_id,
        document_id=document_id,
        chunk_id=chunks[0].id,
        image_id=image_id,
        query_trace_id=query_trace_id,
        ingestion_trace_id=ingestion_trace_id,
        evaluation_run_id=evaluation_run_id,
    )


def _seed_trace_rows(
    trace_repository: object,
    *,
    collection_id: str,
    query_trace_id: str,
    ingestion_trace_id: str,
    source_path: str,
    source_hash: str,
) -> None:
    """Insert Query and Ingestion Trace records for Dashboard pages."""

    from src.storage.repositories import IngestionTraceRecord, QueryTraceRecord

    started_at = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    trace_repository.upsert_query_trace(
        QueryTraceRecord(
            trace_id=query_trace_id,
            collection_id=collection_id,
            raw_query="How should I choose wireless headphones?",
            request_source="dashboard-pages-test",
            started_at=started_at,
            finished_at=started_at + timedelta(milliseconds=150),
            status="success",
            basic_info={"collection": collection_id},
            stages=(
                {
                    "stage": "dense",
                    "duration_ms": 30.0,
                    "candidate_count": 3,
                    "status": "success",
                },
                {
                    "stage": "sparse",
                    "duration_ms": 20.0,
                    "candidate_count": 2,
                    "status": "success",
                },
                {
                    "stage": "rerank",
                    "duration_ms": 40.0,
                    "candidate_count": 2,
                    "status": "success",
                },
            ),
            summary_metrics={
                "total_duration_ms": 150.0,
                "candidate_count_by_stage": {
                    "dense": 3,
                    "sparse": 2,
                    "fusion": 4,
                    "rerank": 2,
                },
                "top_k_results": [{"chunk_id": "chunk-dashboard"}],
            },
            evaluation_metrics={"query_document_relevance": 0.93},
        )
    )
    trace_repository.upsert_ingestion_trace(
        IngestionTraceRecord(
            trace_id=ingestion_trace_id,
            collection_id=collection_id,
            source_uri=source_path,
            source_hash=source_hash,
            started_at=started_at - timedelta(minutes=5),
            finished_at=started_at - timedelta(minutes=4, milliseconds=500),
            status="success",
            basic_info={"collection": collection_id, "source_uri": source_path},
            stages=(
                {"stage": "load", "duration_ms": 30.0, "status": "success"},
                {"stage": "split", "duration_ms": 25.0, "status": "success"},
                {"stage": "upsert", "duration_ms": 80.0, "status": "success"},
            ),
            summary_metrics={
                "total_duration_ms": 500.0,
                "document_status": "success",
                "chunk_count": 2,
                "embedded_count": 2,
                "skipped_count": 0,
            },
            evaluation_metrics={"embedding_coverage": 1.0, "index_ready": True},
        )
    )


def _seed_evaluation_run(
    evaluation_service: object,
    *,
    collection_id: str,
    run_id: str,
) -> None:
    """Insert one evaluation run and metric set for the Evaluation page."""

    evaluation_service.run_evaluation(
        collection_id=collection_id,
        evaluator="fake",
        dataset_name="shopping-guide-golden",
        dataset=[
            {
                "id": "wireless-001",
                "question": "How should I choose wireless headphones?",
                "expected_sources": ["wireless-guide.md"],
            }
        ],
        predictions=[
            {
                "id": "wireless-001",
                "answer": "Compare comfort, Bluetooth stability, and battery.",
                "sources": ["wireless-guide.md"],
            }
        ],
        evaluator_options={
            "metrics": {
                "hit_rate_at_k": 0.95,
                "mrr": 0.87,
            }
        },
        settings_snapshot={"retrieval": "hybrid", "rerank": "fake"},
        run_id=run_id,
    )


def _assert_rendered_titles(
    fake_ui: FakeStreamlit,
    expected_titles: set[str],
) -> None:
    """Assert that every required Dashboard page title was rendered."""

    rendered_titles = {
        str(args[0])
        for name, args, _kwargs in fake_ui.calls
        if name == "title" and args
    }
    assert rendered_titles == expected_titles


def _assert_call_count_at_least(
    fake_ui: FakeStreamlit,
    call_name: str,
    expected_count: int,
) -> None:
    """Assert that a Streamlit call occurred at least the expected count."""

    actual_count = sum(1 for name, _args, _kwargs in fake_ui.calls if name == call_name)
    assert actual_count >= expected_count
