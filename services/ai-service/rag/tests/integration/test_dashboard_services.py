"""Verify Dashboard read services for configuration and indexed data browsing.

F6 introduces service classes that Streamlit pages can call without knowing
settings internals, SQL table shapes, or repository implementation details.
These tests use a real PostgreSQL schema for data browsing because Dashboard
counts must match the same durable rows created by ingestion and retrieval.
"""

from __future__ import annotations

import os
import sys
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
