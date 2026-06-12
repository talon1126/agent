"""Validate the final RAG gate before AImodel integration.

Phase H1 is the acceptance boundary between the standalone RAG module and the
AImodel Agent integration work that follows. These tests intentionally compose
real ingestion, indexing, retrieval, trace persistence, Dashboard read models,
and MCP stdio transport with deterministic fake model providers. The goal is
to prove that the module is independently operable without calling external
LLM, Vision, or Embedding APIs during CI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = RAG_ROOT / "config" / "settings.example.yaml"
VECTOR_DIMENSIONS = 1536
sys.path.insert(0, str(RAG_ROOT))


def _database_url() -> str:
    """Return the local PostgreSQL URL required by H1 E2E tests.

    Returns:
        The configured database URL from ``DATABASE_URL``.

    Raises:
        pytest.Skip: When a developer machine or CI job has not supplied the
            local PostgreSQL connection string needed for integration storage.
    """

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("DATABASE_URL is required for H1 full RAG E2E")
    return database_url


@pytest.mark.e2e
def test_full_rag_flow_before_aimodel_integration(tmp_path: Path) -> None:
    """Run ingestion, indexing, query, trace, Dashboard read, and citations.

    The test protects the H1 gate by using the production orchestration classes
    rather than isolated unit doubles. Only model providers are fake: ingestion
    still writes documents, chunks, Dense vectors, BM25 terms, images, and
    traces to PostgreSQL; query still executes HybridSearch and ResponseBuilder;
    Dashboard services still read persisted assets and trace details.
    """

    from src.core.config import load_prompt, load_settings
    from src.core.query_engine import (
        DenseRoute,
        HybridSearch,
        QueryProcessor,
        RerankController,
        SparseRoute,
    )
    from src.core.response import KnowledgeHubResponseBuilder
    from src.ingestion import IngestionPipeline
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
    from src.libs.reranker import FakeReranker
    from src.libs.splitter import FakeSplitter
    from src.libs.vector_store import PgVectorStore
    from src.observability.services import DataBrowserService, TraceReaderService
    from src.scripts.query import QueryRuntime
    from src.storage.bm25_storage import BM25Storage
    from src.storage.image_storage import ImageStorage
    from src.storage.postgres import PostgresPool, init_schema
    from src.storage.repositories import (
        ChunkRepository,
        DocumentRepository,
        TraceRepository,
    )
    from src.storage.trace_log_storage import PostgresTraceWriter

    class FakeVisionLLM:
        """Return one deterministic caption for the fixture image."""

        def caption_image(
            self,
            image_path: str,
            *,
            prompt: object | None = None,
            image_type: str = "product",
        ) -> object:
            """Create a successful Vision caption without network access.

            Args:
                image_path: Local image path extracted by ``MarkdownLoader``.
                prompt: Loaded image caption prompt passed by ImageCaptioner.
                image_type: Logical image category used by the caption prompt.

            Returns:
                ``VisionCaptionResponse`` matching the real Vision LLM contract.
            """

            from src.libs.llm import VisionCaptionResponse

            assert Path(image_path).exists()
            assert prompt is not None
            assert image_type == "product"
            return VisionCaptionResponse(
                status="success",
                description="图片展示一副无线耳机和充电盒，适合说明佩戴与收纳场景。",
                reason="",
                provider="fake",
                model="fake-vision",
            )

    database_url = _database_url()
    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    collection_id = f"h1-full-rag-{uuid4().hex}"
    source_image = tmp_path / "wireless-earbuds.png"
    source_image.write_bytes(b"h1-wireless-earbuds-image")
    source_file = tmp_path / "wireless-earbuds-guide.md"
    source_file.write_text(
        "# 无线耳机选购指南\n\n"
        "![无线耳机](wireless-earbuds.png)\n\n"
        "高性价比无线耳机应重点关注连接稳定性、续航、佩戴舒适度和通话质量。\n"
        "通勤用户还应关注主动降噪、抗风噪表现和售后保障。",
        encoding="utf-8",
    )

    pool = PostgresPool.from_settings(
        settings.database,
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    try:
        init_schema(pool)
        document_repository = DocumentRepository(pool)
        chunk_repository = ChunkRepository(pool)
        trace_repository = TraceRepository(pool)
        postgres_trace_writer = PostgresTraceWriter(trace_repository)
        vector_store = PgVectorStore(pool=pool, embedding_dimensions=VECTOR_DIMENSIONS)
        bm25_storage = BM25Storage(pool)
        image_storage = ImageStorage(pool, root_dir=tmp_path / "managed-images")
        trace_payloads: list[dict[str, object]] = []
        embedding = FakeEmbedding(dimensions=VECTOR_DIMENSIONS)

        def trace_writer(payload: dict[str, object]) -> None:
            """Capture H1 trace evidence and persist it for Dashboard readers.

            Args:
                payload: Finished ingestion or query trace emitted by the
                    pipeline TraceController.

            Side Effects:
                Appends the raw payload to the in-memory assertion list and
                writes the same payload to PostgreSQL through ``TraceRepository``.
            """

            trace_payloads.append(payload)
            postgres_trace_writer(payload)

        pipeline = IngestionPipeline(
            loader=MarkdownLoader(),
            document_repository=document_repository,
            chunk_repository=chunk_repository,
            trace_sink=trace_writer,
            splitter_step=SplitterStep(DocumentChunker(splitter=FakeSplitter())),
            transform_pipeline=TransformPipeline(
                [
                    MetadataEnricher(),
                    ImageCaptioner(
                        vision_llm=FakeVisionLLM(),
                        prompt=load_prompt("config/prompts/image_caption_prompt.yaml"),
                        enabled=True,
                    ),
                ]
            ),
            embedding_step=EmbeddingStep(
                dense_encoder=DenseEncoder(embedding=embedding),
                bm25_indexer=BM25Indexer(),
                batch_processor=BatchProcessor(batch_size=4, max_retries=1),
            ),
            upsert_step=UpsertStep(
                pool=pool,
                document_repository=document_repository,
                chunk_repository=chunk_repository,
                vector_store=vector_store,
                bm25_storage=bm25_storage,
                image_storage=image_storage,
            ),
        )

        ingestion_result = pipeline.run(source_file, collection_id=collection_id)
        assert ingestion_result.status == "indexed"
        assert ingestion_result.document is not None
        assert ingestion_result.chunks
        assert ingestion_result.upsert_result is not None
        assert trace_payloads[0]["trace_type"] == "ingestion"
        assert trace_payloads[0]["summary_metrics"]["chunk_count"] == len(  # type: ignore[index]
            ingestion_result.chunks
        )

        ingestion_trace = trace_repository.get_ingestion_trace(ingestion_result.trace_id)
        assert ingestion_trace is not None
        assert ingestion_trace.status == "success"
        assert ingestion_trace.evaluation_metrics["index_ready"] is True

        query_processor = QueryProcessor(settings=settings)
        runtime = QueryRuntime(
            query_processor=query_processor,
            hybrid_search=HybridSearch(
                settings=settings,
                dense_route=DenseRoute(
                    settings=settings,
                    query_processor=query_processor,
                    embedding=embedding,
                    vector_store=vector_store,
                ),
                sparse_route=SparseRoute(
                    settings=settings,
                    query_processor=query_processor,
                    bm25_indexer=bm25_storage,
                    vector_store=vector_store,
                ),
            ),
            rerank_controller=RerankController(
                settings=settings,
                reranker=FakeReranker(
                    ordered_chunk_ids=[chunk.id for chunk in ingestion_result.chunks],
                ),
            ),
            response_builder=KnowledgeHubResponseBuilder(),
            trace_sink=trace_writer,
        )
        query_trace_id = f"h1-query-{uuid4().hex}"
        execution = runtime.execute(
            "如何挑选高性价比无线耳机？",
            collection=collection_id,
            top_k=3,
            no_rerank=False,
            trace_id=query_trace_id,
        )

        assert execution.response.is_empty is False
        assert execution.response.trace_id == query_trace_id
        assert execution.response.content
        assert execution.response.citations
        assert execution.final_results
        assert trace_payloads[-1]["trace_type"] == "query"
        assert trace_payloads[-1]["query_result"]["content"] == execution.response.content  # type: ignore[index]

        query_trace = trace_repository.get_query_trace(query_trace_id)
        assert query_trace is not None
        assert query_trace.status == "success"
        assert query_trace.query_result["contexts"]
        assert query_trace.query_result["content"] == execution.response.content
        assert query_trace.summary_metrics["candidate_count_by_stage"]["rerank"] >= 1

        data_browser = DataBrowserService(pool)
        trace_reader = TraceReaderService(pool)
        stats = data_browser.collection_stats(collection_id)
        documents = data_browser.list_documents(collection_id)
        chunks = data_browser.list_chunks(ingestion_result.document.id)
        query_detail = trace_reader.get_query_trace_detail(query_trace_id)
        ingestion_detail = trace_reader.get_ingestion_trace_detail(
            ingestion_result.trace_id,
        )

        assert stats.document_count == 1
        assert stats.chunk_count == len(ingestion_result.chunks)
        assert stats.bm25_indexed_chunk_count >= 1
        assert documents[0].summary is None or isinstance(documents[0].summary, str)
        assert chunks[0].dense_indexed is True
        assert chunks[0].bm25_term_count > 0
        assert query_detail is not None
        assert query_detail.query_result["contexts"]
        assert ingestion_detail is not None
        assert ingestion_detail.summary_metrics["document_status"] == "success"
    finally:
        with pool.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_collections WHERE id = %s",
                (collection_id,),
            )
        pool.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_rag_mcp_stdio_before_aimodel_integration() -> None:
    """Start the MCP stdio server and verify AImodel-visible contracts.

    The subprocess uses the same module entry point documented for AImodel.
    The tool call intentionally sends a blank query so the server returns a
    structured business error before loading settings, PostgreSQL, or model
    providers. That keeps the transport gate deterministic while still proving
    that clients can initialize, list tools, and call a core tool over stdio.
    """

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_server.server", "--transport", "stdio"],
        cwd=RAG_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(RAG_ROOT),
            "RAG_SETTINGS_PATH": str(SETTINGS_PATH),
        },
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert tool_names == {
                "query_knowledge_hub",
                "list_collections",
                "get_document_summary",
            }

            result = await session.call_tool(
                "query_knowledge_hub",
                {"query": "   "},
            )

    assert result.isError is False
    assert result.structuredContent == {
        "ok": False,
        "error": {
            "code": "invalid_request",
            "message": "query must not be blank",
        },
    }
