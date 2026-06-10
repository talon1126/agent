"""Verify C9 transactional persistence for one complete ingestion snapshot.

These tests exercise the PostgreSQL boundary used after C8 indexing. A single
``UpsertStep`` call must persist the canonical document, replace its complete
chunk set, attach dense vectors, rebuild BM25 term rows, and index managed image
files while preserving caller-provided chunk order.
"""

from __future__ import annotations

import os
import sys
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAG_ROOT))


def _database_settings() -> object:
    """Build typed PostgreSQL settings for integration tests."""

    from src.core.config import DatabaseSettings

    return DatabaseSettings(
        provider="postgresql",
        url_env="DATABASE_URL",
        pool_size=3,
        echo_sql=False,
    )


def _build_indexing_result(chunks: list[object]) -> object:
    """Create deterministic Dense and BM25 output for C9 persistence tests."""

    from src.ingestion.embedding import (
        BM25Indexer,
        DenseEncodingResult,
        EmbeddingBatchResult,
    )

    dense_results = [
        DenseEncodingResult(
            chunk_id=chunk.id,
            content_hash=sha256(chunk.text.encode("utf-8")).hexdigest(),
            vector=[
                1.0 if position == index else 0.0
                for position in range(1536)
            ],
            metadata={"chunk_index": chunk.chunk_index},
        )
        for index, chunk in enumerate(chunks)
    ]
    return EmbeddingBatchResult(
        dense_results=dense_results,
        bm25_index=BM25Indexer().index(chunks),
        dense_failures=[],
        bm25_failures=[],
        dense_batches_processed=1,
        bm25_batches_processed=1,
    )


@pytest.mark.integration
def test_upsert_step_persists_and_replaces_complete_ingestion_snapshot(
    tmp_path: Path,
) -> None:
    """Require idempotent ordered writes across document indexing stores."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for ingestion upsert integration")

    from src.core.types import Chunk, Document
    from src.ingestion.storage import UpsertStep
    from src.libs.vector_store import PgVectorStore
    from src.storage.bm25_storage import BM25Storage
    from src.storage.image_storage import ImageStorage
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import ChunkRepository, DocumentRepository

    collection_id = f"c9-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    source_path = f"fixtures/{document_id}.md"
    image_id = f"image-{uuid4().hex}"
    source_image = tmp_path / "source-product.png"
    source_image.write_bytes(b"fake-png-content")
    placeholder = f"[[image:{image_id}]]"
    document_text = f"Wireless headphones guide.\n{placeholder}\nOffice stress toy guide."
    image_offset = document_text.index(placeholder)
    document = Document(
        id=document_id,
        text=document_text,
        metadata={
            "source_path": source_path,
            "images": [
                {
                    "id": image_id,
                    "path": str(source_image),
                    "page": 1,
                    "text_offset": image_offset,
                    "text_length": len(placeholder),
                    "position": {"width": 640, "height": 480},
                }
            ],
        },
    )
    chunks = [
        Chunk(
            id=f"chunk-{uuid4().hex}",
            text="Wireless headphones guide.",
            metadata={
                "source_path": source_path,
                "image_refs": [image_id],
                "image_captions": [
                    {
                        "image_id": image_id,
                        "status": "success",
                        "description": "Black wireless headphones.",
                    }
                ],
            },
            chunk_index=0,
            start_offset=0,
            end_offset=26,
            source_ref={"document_id": document_id},
        ),
        Chunk(
            id=f"chunk-{uuid4().hex}",
            text="Office stress toy guide.",
            metadata={"source_path": source_path},
            chunk_index=1,
            start_offset=document_text.index("Office"),
            end_offset=len(document_text),
            source_ref={"document_id": document_id},
        ),
    ]

    pool = PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        document_repository = DocumentRepository(pool)
        chunk_repository = ChunkRepository(pool)
        image_storage = ImageStorage(
            pool,
            root_dir=tmp_path / "managed-images",
        )
        step = UpsertStep(
            pool=pool,
            document_repository=document_repository,
            chunk_repository=chunk_repository,
            vector_store=PgVectorStore(pool=pool, embedding_dimensions=1536),
            bm25_storage=BM25Storage(pool),
            image_storage=image_storage,
        )

        first = step.run(
            document=document,
            chunks=chunks,
            indexing_result=_build_indexing_result(chunks),
            collection_id=collection_id,
            source_path=source_path,
            source_hash=sha256(document_text.encode("utf-8")).hexdigest(),
            title="C9 fixture",
        )
        repeated = step.run(
            document=document,
            chunks=chunks,
            indexing_result=_build_indexing_result(chunks),
            collection_id=collection_id,
            source_path=source_path,
            source_hash=sha256(document_text.encode("utf-8")).hexdigest(),
            title="C9 fixture",
        )

        assert first.chunk_ids == [chunk.id for chunk in chunks]
        assert first.vector_chunk_ids == first.chunk_ids
        assert first.bm25_chunk_ids == first.chunk_ids
        assert first.image_ids == [image_id]
        assert repeated == first

        replacement = chunks[0].model_copy(
            update={
                "id": f"chunk-{uuid4().hex}",
                "text": "Updated wireless headphones buying advice.",
            }
        )
        replacement_chunks = [replacement, chunks[1]]
        changed = step.run(
            document=document,
            chunks=replacement_chunks,
            indexing_result=_build_indexing_result(replacement_chunks),
            collection_id=collection_id,
            source_path=source_path,
            source_hash=sha256(b"document-v2").hexdigest(),
            title="C9 fixture updated",
        )

        assert changed.chunk_ids == [replacement.id, chunks[1].id]
        assert changed.chunk_ids[0] != first.chunk_ids[0]
        assert chunk_repository.get_by_id(first.chunk_ids[0]) is None
        assert [
            chunk.id
            for chunk in chunk_repository.list_by_document(document_id)
        ] == changed.chunk_ids

        with pool.connection() as connection:
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM rag_documents WHERE id = %s),
                    (SELECT COUNT(*) FROM rag_chunks WHERE document_id = %s),
                    (
                        SELECT COUNT(*)
                        FROM rag_chunks
                        WHERE document_id = %s AND embedding IS NOT NULL
                    ),
                    (SELECT COUNT(*) FROM rag_bm25_terms WHERE document_id = %s),
                    (SELECT COUNT(*) FROM image_index WHERE document_id = %s)
                """,
                (
                    document_id,
                    document_id,
                    document_id,
                    document_id,
                    document_id,
                ),
            ).fetchone()
            assert counts is not None
            assert counts[0] == 1
            assert counts[1] == 2
            assert counts[2] == 2
            assert counts[3] > 0
            assert counts[4] == 1

        image_record = image_storage.find_by_collection(collection_id)[0]
        assert Path(image_record.file_path).parent.name == collection_id
        assert Path(image_record.file_path).read_bytes() == b"fake-png-content"
        assert image_record.quality_status == "ok"
        assert image_record.metadata["caption"] == "Black wireless headphones."
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_upsert_step_rolls_back_database_snapshot_when_vector_write_fails(
    tmp_path: Path,
) -> None:
    """Require a downstream vector failure to roll back every database row."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for ingestion rollback integration")

    from src.core.errors import IngestionError
    from src.core.types import Chunk, Document
    from src.ingestion.embedding import (
        BM25Indexer,
        DenseEncodingResult,
        EmbeddingBatchResult,
    )
    from src.ingestion.storage import UpsertStep
    from src.libs.vector_store import PgVectorStore
    from src.storage.bm25_storage import BM25Storage
    from src.storage.image_storage import ImageStorage
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import ChunkRepository, DocumentRepository

    collection_id = f"c9-rollback-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    source_path = f"fixtures/{document_id}.md"
    source_image = tmp_path / "rollback-source.png"
    source_image.write_bytes(b"new-image-content")
    image_id = f"image-{uuid4().hex}"
    placeholder = f"[[image:{image_id}]]"
    document_text = f"Rollback fixture text.\n{placeholder}"
    document = Document(
        id=document_id,
        text=document_text,
        metadata={
            "source_path": source_path,
            "images": [
                {
                    "id": image_id,
                    "path": str(source_image),
                    "page": 0,
                    "text_offset": document_text.index(placeholder),
                    "text_length": len(placeholder),
                    "position": {"width": 10, "height": 10},
                }
            ],
        },
    )
    chunk = Chunk(
        id=f"chunk-{uuid4().hex}",
        text="Rollback fixture text.",
        metadata={
            "source_path": source_path,
            "image_refs": [image_id],
        },
        chunk_index=0,
        start_offset=0,
        end_offset=len(document.text),
        source_ref={"document_id": document_id},
    )
    indexing_result = EmbeddingBatchResult(
        dense_results=[
            DenseEncodingResult(
                chunk_id=chunk.id,
                content_hash=sha256(chunk.text.encode("utf-8")).hexdigest(),
                vector=[1.0, 0.0],
                metadata={"chunk_index": 0},
            )
        ],
        bm25_index=BM25Indexer().index([chunk]),
        dense_failures=[],
        bm25_failures=[],
        dense_batches_processed=1,
        bm25_batches_processed=1,
    )

    pool = PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        image_storage = ImageStorage(pool, root_dir=tmp_path / "images")
        managed_image = image_storage.save_image(
            collection_id,
            image_id,
            b"previous-image-content",
            suffix=".png",
        )
        step = UpsertStep(
            pool=pool,
            document_repository=DocumentRepository(pool),
            chunk_repository=ChunkRepository(pool),
            vector_store=PgVectorStore(pool=pool, embedding_dimensions=1536),
            bm25_storage=BM25Storage(pool),
            image_storage=image_storage,
        )

        with pytest.raises(IngestionError, match="Unable to persist ingestion snapshot"):
            step.run(
                document=document,
                chunks=[chunk],
                indexing_result=indexing_result,
                collection_id=collection_id,
                source_path=source_path,
                source_hash=sha256(document.text.encode("utf-8")).hexdigest(),
            )

        with pool.connection() as connection:
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM rag_documents WHERE id = %s),
                    (SELECT COUNT(*) FROM rag_chunks WHERE document_id = %s),
                    (SELECT COUNT(*) FROM rag_bm25_terms WHERE document_id = %s),
                    (SELECT COUNT(*) FROM image_index WHERE document_id = %s)
                """,
                (document_id, document_id, document_id, document_id),
            ).fetchone()
        assert counts == (0, 0, 0, 0)
        assert managed_image.read_bytes() == b"previous-image-content"
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_ingestion_pipeline_runs_complete_mvp_and_skips_unchanged_source(
    tmp_path: Path,
) -> None:
    """Require C10 to compose the complete offline indexing workflow."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for ingestion pipeline integration")

    from src.ingestion import IngestionPipeline, IngestionPipelineResult
    from src.ingestion.chunk import DocumentChunker, SplitterStep
    from src.ingestion.embedding import (
        BatchProcessor,
        BM25Indexer,
        DenseEncoder,
        EmbeddingStep,
    )
    from src.ingestion.storage import UpsertStep
    from src.ingestion.transform import (
        ImageCaptioner,
        MetadataEnricher,
        TransformPipeline,
    )
    from src.libs.embedding import FakeEmbedding
    from src.libs.loader import MarkdownLoader
    from src.libs.splitter import FakeSplitter
    from src.libs.vector_store import PgVectorStore
    from src.storage.bm25_storage import BM25Storage
    from src.storage.image_storage import ImageStorage
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import (
        ChunkRepository,
        DocumentRepository,
    )

    class FakeImageTransform:
        """Return a deterministic caption without an external Vision provider."""

        def transform(
            self,
            image: dict[str, object],
            *,
            document_context: str = "",
        ) -> dict[str, object]:
            """Generate one stable success caption for the referenced image."""

            assert image["id"]
            assert document_context
            return {
                "status": "success",
                "description": "Product comparison image for wireless headphones.",
                "extracted_text": "wireless headphones",
                "key_facts": ["product comparison"],
                "provider": "fake",
                "model": "fake-vision",
            }

    collection_id = f"c10-{uuid4().hex}"
    source_image = tmp_path / "product.png"
    source_image.write_bytes(b"pipeline-image")
    source = tmp_path / "guide.md"
    source.write_text(
        "# Wireless Headphones\n\n"
        "![Product](product.png)\n\n"
        "Choose headphones by comfort, battery life, and noise cancellation.",
        encoding="utf-8",
    )

    pool = PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        documents = DocumentRepository(pool)
        chunks = ChunkRepository(pool)
        embedding = Mock(wraps=FakeEmbedding(dimensions=1536))
        trace_payloads: list[dict[str, object]] = []
        pipeline = IngestionPipeline(
            loader=MarkdownLoader(),
            document_repository=documents,
            chunk_repository=chunks,
            trace_sink=trace_payloads.append,
            splitter_step=SplitterStep(
                DocumentChunker(splitter=FakeSplitter())
            ),
            transform_pipeline=TransformPipeline(
                [
                    MetadataEnricher(),
                    ImageCaptioner(
                        image_transform=FakeImageTransform(),
                        enabled=True,
                    ),
                ]
            ),
            embedding_step=EmbeddingStep(
                dense_encoder=DenseEncoder(embedding=embedding),
                bm25_indexer=BM25Indexer(),
                batch_processor=BatchProcessor(batch_size=8, max_retries=1),
            ),
            upsert_step=UpsertStep(
                pool=pool,
                document_repository=documents,
                chunk_repository=chunks,
                vector_store=PgVectorStore(
                    pool=pool,
                    embedding_dimensions=1536,
                ),
                bm25_storage=BM25Storage(pool),
                image_storage=ImageStorage(
                    pool,
                    root_dir=tmp_path / "managed-images",
                ),
            ),
        )

        first = pipeline.run(source, collection_id=collection_id)
        second = pipeline.run(source, collection_id=collection_id)

        assert isinstance(first, IngestionPipelineResult)
        assert first.status == "indexed"
        assert first.document is not None
        assert len(first.chunks) == 1
        assert first.chunks[0].metadata["image_caption_status"] == "success"
        assert first.indexing_result is not None
        assert first.indexing_result.bm25_index.chunk_count == 1
        assert first.upsert_result is not None
        assert first.upsert_result.chunk_ids == [first.chunks[0].id]
        assert documents.get_lifecycle_status(first.document.id) == "success"

        assert second.status == "skipped"
        assert second.document is None
        assert second.chunks == ()
        assert embedding.embed_batch.call_count == 1
        assert len(trace_payloads) == 2

        indexed_trace, skipped_trace = trace_payloads
        assert indexed_trace["trace_type"] == "ingestion"
        assert indexed_trace["trace_id"] == first.trace_id
        assert indexed_trace["status"] == "success"
        assert [
            stage["stage"]
            for stage in indexed_trace["stages"]  # type: ignore[index]
        ] == [
            "dedup",
            "load",
            "split",
            "transform",
            "image_caption",
            "embed",
            "upsert",
        ]
        indexed_summary = indexed_trace["summary_metrics"]
        indexed_evaluation = indexed_trace["evaluation_metrics"]
        assert isinstance(indexed_summary, dict)
        assert isinstance(indexed_evaluation, dict)
        assert indexed_summary["document_status"] == "success"
        assert indexed_summary["chunk_count"] == 1
        assert indexed_summary["embedded_count"] == 1
        assert indexed_evaluation["index_ready"] is True
        transform_stage = next(
            stage
            for stage in indexed_trace["stages"]  # type: ignore[index]
            if stage["stage"] == "transform"
        )
        assert [
            step["name"] for step in transform_stage["sub_stages"]
        ] == ["metadata_enrich", "image_to_text"]
        assert [
            step["provider"] for step in transform_stage["sub_stages"]
        ] == ["MetadataEnricher", "ImageCaptioner"]
        assert all(
            step["status"] == "success"
            for step in transform_stage["sub_stages"]
        )

        assert skipped_trace["trace_type"] == "ingestion"
        assert skipped_trace["trace_id"] == second.trace_id
        assert skipped_trace["status"] == "skipped"
        assert [
            stage["stage"]
            for stage in skipped_trace["stages"]  # type: ignore[index]
        ] == ["dedup"]
        skipped_summary = skipped_trace["summary_metrics"]
        assert isinstance(skipped_summary, dict)
        assert skipped_summary["document_status"] == "skipped"

        with pool.connection() as connection:
            counts = connection.execute(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM rag_chunks
                        WHERE document_id = %s AND embedding IS NOT NULL
                    ),
                    (
                        SELECT COUNT(*)
                        FROM rag_bm25_terms
                        WHERE document_id = %s
                    ),
                    (
                        SELECT COUNT(*)
                        FROM image_index
                        WHERE document_id = %s
                    )
                """,
                (first.document.id, first.document.id, first.document.id),
            ).fetchone()
        assert counts is not None
        assert counts[0] == 1
        assert counts[1] > 0
        assert counts[2] == 1
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.integration
def test_ingestion_pipeline_reuses_dense_vectors_for_unchanged_chunks(
    tmp_path: Path,
) -> None:
    """Require changed documents to embed only chunks with new content hashes."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for differential ingestion integration")

    from src.ingestion import IngestionPipeline
    from src.ingestion.chunk import DocumentChunker, SplitterStep
    from src.ingestion.embedding import (
        BatchProcessor,
        BM25Indexer,
        DenseEncoder,
        EmbeddingStep,
    )
    from src.ingestion.storage import UpsertStep
    from src.ingestion.transform import MetadataEnricher, TransformPipeline
    from src.libs.embedding import FakeEmbedding
    from src.libs.loader import MarkdownLoader
    from src.libs.splitter import BaseSplitter
    from src.libs.vector_store import PgVectorStore
    from src.storage.bm25_storage import BM25Storage
    from src.storage.image_storage import ImageStorage
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import (
        ChunkRepository,
        DocumentRepository,
    )

    class ParagraphSplitter(BaseSplitter):
        """Split the fixture into stable paragraph-level business chunks."""

        def split(self, text: str) -> list[str]:
            """Return non-blank paragraphs in source order."""

            return [part for part in text.split("\n\n") if part.strip()]

    collection_id = f"c10-differential-{uuid4().hex}"
    source = tmp_path / "differential.md"
    source.write_text(
        "Stable product guidance.\n\nChanging recommendation v1.",
        encoding="utf-8",
    )

    pool = PostgresPool.from_settings(
        _database_settings(),
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        documents = DocumentRepository(pool)
        chunks = ChunkRepository(pool)
        embedding = Mock(wraps=FakeEmbedding(dimensions=1536))
        pipeline = IngestionPipeline(
            loader=MarkdownLoader(),
            document_repository=documents,
            chunk_repository=chunks,
            splitter_step=SplitterStep(
                DocumentChunker(splitter=ParagraphSplitter())
            ),
            transform_pipeline=TransformPipeline([MetadataEnricher()]),
            embedding_step=EmbeddingStep(
                dense_encoder=DenseEncoder(embedding=embedding),
                bm25_indexer=BM25Indexer(),
                batch_processor=BatchProcessor(batch_size=8, max_retries=1),
            ),
            upsert_step=UpsertStep(
                pool=pool,
                document_repository=documents,
                chunk_repository=chunks,
                vector_store=PgVectorStore(
                    pool=pool,
                    embedding_dimensions=1536,
                ),
                bm25_storage=BM25Storage(pool),
                image_storage=ImageStorage(
                    pool,
                    root_dir=tmp_path / "managed-images-differential",
                ),
            ),
        )

        first = pipeline.run(source, collection_id=collection_id)
        source.write_text(
            "Stable product guidance.\n\nChanging recommendation v2.",
            encoding="utf-8",
        )
        second = pipeline.run(source, collection_id=collection_id)

        assert first.status == "indexed"
        assert second.status == "indexed"
        assert embedding.embed_batch.call_count == 2
        assert [
            text.strip() for text in embedding.embed_batch.call_args_list[0].args[0]
        ] == [
            "Stable product guidance.",
            "Changing recommendation v1.",
        ]
        assert [
            text.strip() for text in embedding.embed_batch.call_args_list[1].args[0]
        ] == [
            "Changing recommendation v2."
        ]
        assert second.indexing_result is not None
        assert [item.chunk_id for item in second.indexing_result.dense_results] == [
            chunk.id for chunk in second.chunks
        ]
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


def test_ingestion_pipeline_rejects_partial_complete_mode_configuration() -> None:
    """Require Loader-only or fully configured construction, never a partial chain."""

    from src.ingestion import IngestionPipeline

    with pytest.raises(ValueError, match="Complete ingestion mode requires"):
        IngestionPipeline(
            loader=Mock(),
            document_repository=Mock(),
            splitter_step=Mock(),
        )


def test_ingestion_pipeline_rejects_empty_indexing_snapshot() -> None:
    """Require empty Splitter/Transform output to remain a failed ingestion."""

    from src.core.errors import IngestionError
    from src.core.types import Document
    from src.ingestion import IngestionPipeline

    embedding_step = Mock()
    upsert_step = Mock()
    pipeline = IngestionPipeline(
        loader=Mock(),
        document_repository=Mock(),
        chunk_repository=Mock(),
        splitter_step=Mock(),
        transform_pipeline=Mock(),
        embedding_step=embedding_step,
        upsert_step=upsert_step,
    )
    document = Document(id="doc-empty", text="Source text.", metadata={})

    with pytest.raises(IngestionError, match="without searchable chunks"):
        pipeline.run_indexing(
            document=document,
            chunks=[],
            collection_id="shopping_guides",
            source_path="D:/fixtures/empty.md",
            source_hash=sha256(b"Source text.").hexdigest(),
        )

    embedding_step.run_batch.assert_not_called()
    upsert_step.run.assert_not_called()
