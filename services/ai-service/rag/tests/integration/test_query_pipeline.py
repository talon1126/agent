"""Verify the complete Retrieval pipeline against PostgreSQL and pgvector.

These integration tests persist isolated document, chunk, dense-vector, and
BM25 fixtures, then exercise the same QueryProcessor, DenseRoute, SparseRoute,
HybridSearch, RerankController, response builder, and query CLI boundaries used
by production composition. Model providers remain deterministic local fakes so
the suite validates module collaboration without network calls or credentials.

Every test owns UUID-based collections and removes them in ``finally`` blocks.
The canonical schema cascades cleanup to documents, chunks, vectors, and BM25
postings, leaving the shared local PostgreSQL instance reusable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from src.core.config import RagSettings, load_settings
from src.core.errors import ProviderError
from src.core.query_engine import (
    DenseRoute,
    HybridSearch,
    QueryProcessor,
    RerankController,
    SparseRoute,
)
from src.core.response import KnowledgeHubResponseBuilder
from src.core.types import Chunk, Document
from src.ingestion.embedding import BM25Indexer
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.reranker import FakeReranker
from src.libs.vector_store import PgVectorStore
from src.scripts.query import QueryRuntime, run_query_cli
from src.storage.bm25_storage import BM25Storage
from src.storage.postgres import PostgresPool, init_schema
from src.storage.repositories import ChunkRepository, DocumentRepository

RAG_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = RAG_ROOT / "config" / "settings.example.yaml"
VECTOR_DIMENSIONS = 1536


class FixedQueryEmbedding(BaseEmbedding):
    """Return one fixed vector for online query embedding.

    The fixture vectors are written explicitly through ``PgVectorStore``. A
    fixed query vector makes dense ranking deterministic while retaining the
    real pgvector cosine-search implementation.
    """

    def __init__(self, vector: list[float]) -> None:
        """Store the vector returned for every non-blank query.

        Args:
            vector: Query vector matching the canonical pgvector dimensions.
        """

        self._vector = list(vector)

    def embed(self, text: str) -> list[float]:
        """Return the configured vector for a non-blank query.

        Args:
            text: Normalized query text.

        Returns:
            A defensive copy of the configured vector.

        Raises:
            ProviderError: If query processing supplies blank text.
        """

        if not text.strip():
            raise ProviderError("Cannot embed blank integration query")
        return list(self._vector)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed an ordered batch using the same fixed-vector contract."""

        return [self.embed(text) for text in texts]


class FailingQueryEmbedding(BaseEmbedding):
    """Simulate an unavailable Dense provider for Hybrid fallback testing."""

    def embed(self, text: str) -> list[float]:
        """Raise a provider failure instead of producing a query vector."""

        raise ProviderError("Dense integration provider unavailable")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Raise the same failure for interface completeness."""

        raise ProviderError("Dense integration provider unavailable")


@dataclass(frozen=True, slots=True)
class RetrievalFixture:
    """Hold persisted fixture identities and query components."""

    collection_id: str
    other_collection_id: str
    primary_chunk: Chunk
    rerank_chunk: Chunk
    filtered_chunk: Chunk
    settings: RagSettings
    vector_store: PgVectorStore
    bm25_storage: BM25Storage
    query_vector: list[float]


def _database_url() -> str:
    """Return the integration database URL or skip when unavailable."""

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("DATABASE_URL is required for Retrieval integration")
    return database_url


def _settings(collection_id: str) -> RagSettings:
    """Load validated settings with isolated Retrieval limits and collection."""

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    retrieval = settings.retrieval.model_copy(
        update={
            "query_rewrite_enabled": False,
            "dense_top_k": 5,
            "sparse_top_k": 5,
            "fusion_top_k": 5,
            "final_top_k": 2,
            "rrf_k": 60,
            "filters": settings.retrieval.filters.model_copy(
                update={"default_collection": collection_id}
            ),
        }
    )
    rerank = settings.rerank.model_copy(update={"enabled": True, "top_k": 2})
    return settings.model_copy(update={"retrieval": retrieval, "rerank": rerank})


def _unit_vector(index: int) -> list[float]:
    """Build one canonical-dimension basis vector for deterministic cosine rank."""

    vector = [0.0] * VECTOR_DIMENSIONS
    vector[index] = 1.0
    return vector


def _chunk(
    *,
    chunk_id: str,
    document_id: str,
    collection_id: str,
    text: str,
    chunk_index: int,
    doc_type: str,
) -> Chunk:
    """Build one citation-ready chunk persisted by the integration fixture."""

    return Chunk(
        id=chunk_id,
        text=text,
        metadata={
            "collection": collection_id,
            "doc_type": doc_type,
            "source_type": "markdown",
            "document_status": "published",
            "lifecycle_status": "success",
            "permissions": ["public"],
        },
        chunk_index=chunk_index,
        start_offset=chunk_index * 100,
        end_offset=chunk_index * 100 + len(text),
        source_ref={
            "document_id": document_id,
            "source_path": f"fixtures/{document_id}.md",
            "title": f"Retrieval fixture {document_id}",
            "section_path": [doc_type],
        },
    )


def _persist_fixture(
    pool: PostgresPool,
    *,
    collection_id: str,
    other_collection_id: str,
) -> RetrievalFixture:
    """Persist two target chunks and one cross-collection filter candidate.

    Args:
        pool: Open PostgreSQL pool.
        collection_id: Test-owned target collection known before setup starts.
        other_collection_id: Test-owned collection used to verify filtering.

    Returns:
        Persisted fixture contracts and storage adapters.

    Notes:
        Callers own both collection IDs before invoking this function, allowing
        ``finally`` cleanup even when fixture persistence fails midway.
    """
    document_id = f"doc-{uuid4().hex}"
    other_document_id = f"doc-{uuid4().hex}"
    primary_chunk = _chunk(
        chunk_id=f"chunk-{uuid4().hex}",
        document_id=document_id,
        collection_id=collection_id,
        text="无线耳机推荐重点关注连接稳定性、续航和佩戴舒适度。",
        chunk_index=0,
        doc_type="buying_guide",
    )
    rerank_chunk = _chunk(
        chunk_id=f"chunk-{uuid4().hex}",
        document_id=document_id,
        collection_id=collection_id,
        text="办公场景可以选择安静的解压玩具，并比较材质和耐用性。",
        chunk_index=1,
        doc_type="comparison",
    )
    filtered_chunk = _chunk(
        chunk_id=f"chunk-{uuid4().hex}",
        document_id=other_document_id,
        collection_id=other_collection_id,
        text="无线耳机推荐的跨集合内容不应进入最终结果。",
        chunk_index=0,
        doc_type="foreign_guide",
    )
    document = Document(
        id=document_id,
        text=f"{primary_chunk.text}\n{rerank_chunk.text}",
        metadata={"collection": collection_id},
    )
    other_document = Document(
        id=other_document_id,
        text=filtered_chunk.text,
        metadata={"collection": other_collection_id},
    )
    document_repository = DocumentRepository(pool)
    chunk_repository = ChunkRepository(pool)
    document_repository.upsert(
        document,
        collection_id=collection_id,
        source_path=f"fixtures/{document_id}.md",
        source_hash=sha256(document.text.encode()).hexdigest(),
        title="D14 Retrieval fixture",
    )
    document_repository.upsert(
        other_document,
        collection_id=other_collection_id,
        source_path=f"fixtures/{other_document_id}.md",
        source_hash=sha256(other_document.text.encode()).hexdigest(),
        title="D14 filtered fixture",
    )
    chunk_repository.upsert_many(
        [primary_chunk, rerank_chunk],
        collection_id=collection_id,
        document_id=document_id,
    )
    chunk_repository.upsert_many(
        [filtered_chunk],
        collection_id=other_collection_id,
        document_id=other_document_id,
    )

    vector_store = PgVectorStore(
        pool=pool,
        embedding_dimensions=VECTOR_DIMENSIONS,
    )
    query_vector = _unit_vector(0)
    vector_store.upsert(
        [primary_chunk, rerank_chunk, filtered_chunk],
        [query_vector, _unit_vector(1), query_vector],
    )
    bm25_storage = BM25Storage(pool)
    bm25_storage.upsert_index(
        BM25Indexer().index([primary_chunk, rerank_chunk]),
        collection_id=collection_id,
        document_id=document_id,
    )
    bm25_storage.upsert_index(
        BM25Indexer().index([filtered_chunk]),
        collection_id=other_collection_id,
        document_id=other_document_id,
    )
    return RetrievalFixture(
        collection_id=collection_id,
        other_collection_id=other_collection_id,
        primary_chunk=primary_chunk,
        rerank_chunk=rerank_chunk,
        filtered_chunk=filtered_chunk,
        settings=_settings(collection_id),
        vector_store=vector_store,
        bm25_storage=bm25_storage,
        query_vector=query_vector,
    )


def _runtime(
    fixture: RetrievalFixture,
    *,
    embedding: BaseEmbedding,
    reranker: FakeReranker | None,
    pool: PostgresPool | None = None,
    trace_sink: object | None = None,
) -> QueryRuntime:
    """Compose the real Retrieval stages around deterministic model adapters.

    Args:
        fixture: Persisted fixture identities and settings.
        embedding: Deterministic or failing query embedding adapter.
        reranker: Optional deterministic reranker.
        pool: Optional pool owned by the caller, such as the CLI lifecycle
            pool. When omitted, reuse the fixture storage adapters.

    Returns:
        A complete QueryRuntime using real PostgreSQL storage boundaries.
    """

    processor = QueryProcessor(settings=fixture.settings)
    vector_store = (
        PgVectorStore(pool=pool, embedding_dimensions=VECTOR_DIMENSIONS)
        if pool is not None
        else fixture.vector_store
    )
    bm25_storage = BM25Storage(pool) if pool is not None else fixture.bm25_storage
    hybrid = HybridSearch(
        settings=fixture.settings,
        dense_route=DenseRoute(
            settings=fixture.settings,
            query_processor=processor,
            embedding=embedding,
            vector_store=vector_store,
        ),
        sparse_route=SparseRoute(
            settings=fixture.settings,
            query_processor=processor,
            bm25_indexer=bm25_storage,
            vector_store=vector_store,
        ),
    )
    controller = (
        RerankController(settings=fixture.settings, reranker=reranker)
        if reranker is not None
        else None
    )
    return QueryRuntime(
        query_processor=processor,
        hybrid_search=hybrid,
        rerank_controller=controller,
        response_builder=KnowledgeHubResponseBuilder(),
        trace_sink=trace_sink,
    )


def _delete_collections(pool: PostgresPool, collection_ids: list[str]) -> None:
    """Delete fixture collections and all schema-cascaded Retrieval data."""

    with pool.transaction() as connection:
        connection.execute(
            "DELETE FROM rag_collections WHERE id = ANY(%s)",
            (collection_ids,),
        )


def _delete_stale_d14_collections(pool: PostgresPool) -> None:
    """Remove only D14-prefixed leftovers from interrupted local test runs."""

    with pool.transaction() as connection:
        connection.execute(
            """
            DELETE FROM rag_collections
            WHERE id LIKE 'd14-retrieval-%'
               OR id LIKE 'd14-filtered-%'
            """
        )


@pytest.mark.integration
def test_query_pipeline_hybrid() -> None:
    """Run Dense, BM25, RRF, Filter, Rerank, Response, and CLI end to end."""

    database_url = _database_url()
    base_settings = load_settings(SETTINGS_PATH, validate_environment=False)
    pool = PostgresPool.from_settings(
        base_settings.database,
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    collection_id = f"d14-retrieval-{uuid4().hex}"
    other_collection_id = f"d14-filtered-{uuid4().hex}"
    fixture: RetrievalFixture | None = None
    schema_ready = False
    try:
        init_schema(pool)
        schema_ready = True
        _delete_stale_d14_collections(pool)
        fixture = _persist_fixture(
            pool,
            collection_id=collection_id,
            other_collection_id=other_collection_id,
        )
        reranker = FakeReranker(
            ordered_chunk_ids=[
                fixture.rerank_chunk.id,
                fixture.primary_chunk.id,
            ]
        )
        trace_payloads: list[dict[str, object]] = []
        runtime = _runtime(
            fixture,
            embedding=FixedQueryEmbedding(fixture.query_vector),
            reranker=reranker,
            trace_sink=trace_payloads.append,
        )

        execution = runtime.execute(
            "无线耳机推荐",
            collection=fixture.collection_id,
            top_k=2,
            no_rerank=False,
            trace_id="d14-hybrid-query",
        )

        assert execution.dense_results[0].chunk_id in {
            fixture.primary_chunk.id,
            fixture.filtered_chunk.id,
        }
        assert fixture.filtered_chunk.id in {
            result.chunk_id for result in execution.dense_results
        }
        assert [result.chunk_id for result in execution.sparse_results] == [
            fixture.primary_chunk.id
        ]
        assert fixture.filtered_chunk.id in {
            result.chunk_id for result in execution.fused_results
        }
        assert fixture.filtered_chunk.id not in {
            result.chunk_id for result in execution.filtered_results
        }
        assert [result.chunk_id for result in execution.final_results] == [
            fixture.rerank_chunk.id,
            fixture.primary_chunk.id,
        ]
        assert execution.rerank_applied is True
        assert execution.fallback_used is False
        assert execution.response.is_empty is False
        assert [citation.chunk_id for citation in execution.response.citations] == [
            fixture.rerank_chunk.id,
            fixture.primary_chunk.id,
        ]
        assert len(trace_payloads) == 1
        query_trace = trace_payloads[0]
        assert query_trace["trace_type"] == "query"
        assert query_trace["trace_id"] == "d14-hybrid-query"
        assert query_trace["status"] == "success"
        assert [
            stage["stage"]
            for stage in query_trace["stages"]  # type: ignore[index]
        ] == [
            "query_processing",
            "dense",
            "sparse",
            "fusion",
            "filter",
            "rerank",
            "response",
        ]
        query_summary = query_trace["summary_metrics"]
        query_evaluation = query_trace["evaluation_metrics"]
        assert isinstance(query_summary, dict)
        assert isinstance(query_evaluation, dict)
        query_counts = query_summary["candidate_count_by_stage"]
        assert isinstance(query_counts, dict)
        assert query_summary["fallback_used"] is False
        assert query_counts["rerank"] == 2
        assert query_evaluation["empty_result"] is False

        output: list[str] = []
        cli_pool = PostgresPool.from_settings(
            fixture.settings.database,
            environ={"DATABASE_URL": database_url},
        )
        exit_code = run_query_cli(
            [
                "--query",
                "无线耳机推荐",
                "--collection",
                fixture.collection_id,
                "--top-k",
                "2",
                "--verbose",
            ],
            settings_loader=lambda: fixture.settings,
            pool_factory=lambda _: cli_pool,
            runtime_builder=lambda _settings, _pool, _no_rerank: _runtime(
                fixture,
                embedding=FixedQueryEmbedding(fixture.query_vector),
                reranker=reranker,
                pool=_pool,
            ),
            trace_id_factory=lambda: "d14-cli-query",
            output=output.append,
        )

        assert exit_code == 0
        assert cli_pool.is_open is False
        payload = json.loads(output[0])
        assert payload["collection"] == fixture.collection_id
        assert payload["rerank_applied"] is True
        assert payload["response"]["trace_id"] == "d14-cli-query"
        assert payload["debug"]["filter"] == [
            {
                "chunk_id": fixture.primary_chunk.id,
                "score": pytest.approx(
                    next(
                        result.score
                        for result in execution.filtered_results
                        if result.chunk_id == fixture.primary_chunk.id
                    )
                ),
            },
            {
                "chunk_id": fixture.rerank_chunk.id,
                "score": pytest.approx(
                    next(
                        result.score
                        for result in execution.filtered_results
                        if result.chunk_id == fixture.rerank_chunk.id
                    )
                ),
            },
        ]
        assert "metadata" not in output[0]
    finally:
        if schema_ready:
            _delete_collections(pool, [collection_id, other_collection_id])
        pool.close()


@pytest.mark.integration
def test_query_pipeline_falls_back_to_sparse_when_dense_provider_fails() -> None:
    """Keep real BM25 retrieval available when Dense query embedding fails."""

    database_url = _database_url()
    base_settings = load_settings(SETTINGS_PATH, validate_environment=False)
    pool = PostgresPool.from_settings(
        base_settings.database,
        environ={"DATABASE_URL": database_url},
    )
    pool.open()
    collection_id = f"d14-retrieval-{uuid4().hex}"
    other_collection_id = f"d14-filtered-{uuid4().hex}"
    fixture: RetrievalFixture | None = None
    schema_ready = False
    try:
        init_schema(pool)
        schema_ready = True
        _delete_stale_d14_collections(pool)
        fixture = _persist_fixture(
            pool,
            collection_id=collection_id,
            other_collection_id=other_collection_id,
        )
        trace_payloads: list[dict[str, object]] = []
        runtime = _runtime(
            fixture,
            embedding=FailingQueryEmbedding(),
            reranker=None,
            trace_sink=trace_payloads.append,
        )

        execution = runtime.execute(
            "无线耳机推荐",
            collection=fixture.collection_id,
            top_k=2,
            no_rerank=True,
            trace_id="d14-sparse-fallback",
        )

        assert execution.dense_results == ()
        assert [result.chunk_id for result in execution.sparse_results] == [
            fixture.primary_chunk.id
        ]
        assert [result.chunk_id for result in execution.final_results] == [
            fixture.primary_chunk.id
        ]
        assert execution.rerank_applied is False
        assert execution.fallback_used is True
        assert execution.response.citations[0].chunk_id == fixture.primary_chunk.id
        assert len(trace_payloads) == 1
        fallback_trace = trace_payloads[0]
        assert fallback_trace["status"] == "degraded"
        assert [
            stage["stage"]
            for stage in fallback_trace["stages"]  # type: ignore[index]
        ] == [
            "query_processing",
            "dense",
            "sparse",
            "fusion",
            "filter",
            "rerank",
            "response",
        ]
        fallback_summary = fallback_trace["summary_metrics"]
        assert isinstance(fallback_summary, dict)
        fallback_counts = fallback_summary["candidate_count_by_stage"]
        assert isinstance(fallback_counts, dict)
        assert fallback_summary["fallback_used"] is True
        assert fallback_counts["dense"] == 0
        assert fallback_counts["sparse"] == 1
    finally:
        if schema_ready:
            _delete_collections(pool, [collection_id, other_collection_id])
        pool.close()
