"""Verify BM25 sparse indexing for ingestion and future Sparse Route tests.

C7 introduces an in-memory BM25Indexer that turns chunks into term statistics
and returns ranked ``chunk_id`` candidates for query keywords. Persistence,
batch retries, and Sparse Route chunk hydration are intentionally left to later
tasks; this suite protects only indexing correctness and reusable sparse
candidate output.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAG_ROOT))

types_module = importlib.import_module("src.core.types")
embedding_module = importlib.import_module("src.ingestion.embedding")

Chunk = types_module.Chunk
BM25Candidate = embedding_module.BM25Candidate
BM25Indexer = embedding_module.BM25Indexer
BatchProcessor = embedding_module.BatchProcessor


def make_chunk(
    *,
    chunk_id: str,
    text: str,
    chunk_index: int = 0,
) -> Chunk:
    """Create one valid chunk for sparse indexing tests.

    Args:
        chunk_id: Stable chunk identifier returned by BM25 candidates.
        text: Searchable text indexed by BM25.
        chunk_index: Source order used only to satisfy the shared chunk model.

    Returns:
        A validated ``Chunk`` object.
    """

    return Chunk(
        id=chunk_id,
        text=text,
        metadata={"document_id": "doc-audio", "source_path": "shopping_guides/audio.md"},
        chunk_index=chunk_index,
        start_offset=0,
        end_offset=len(text),
    )


def test_bm25_indexer_builds_term_frequencies_and_document_stats() -> None:
    """Require index() to expose sparse statistics needed by later storage.

    The index result should be useful beyond immediate querying: later C9
    storage can persist term frequencies and document lengths without
    re-tokenizing chunks.
    """

    chunks = [
        make_chunk(
            chunk_id="chunk-headphones",
            text="Wireless headphones headphones noise cancellation.",
            chunk_index=0,
        ),
        make_chunk(
            chunk_id="chunk-toy",
            text="Quiet silicone stress toy for office use.",
            chunk_index=1,
        ),
    ]
    indexer = BM25Indexer()

    result = indexer.index(chunks)

    assert result.chunk_count == 2
    assert result.average_document_length > 0
    assert result.term_document_frequency["headphones"] == 1
    assert result.term_frequencies["chunk-headphones"]["headphones"] == 2
    assert result.inverted_index["headphones"] == {"chunk-headphones": 2}


def test_bm25_query_ranks_keyword_candidates_and_limits_top_k() -> None:
    """Require keyword queries to return ranked sparse candidates only."""

    indexer = BM25Indexer()
    indexer.index(
        [
            make_chunk(
                chunk_id="chunk-best",
                text="Wireless headphones with wireless low latency audio.",
                chunk_index=0,
            ),
            make_chunk(
                chunk_id="chunk-secondary",
                text="Wireless earbuds with compact charging case.",
                chunk_index=1,
            ),
            make_chunk(
                chunk_id="chunk-unrelated",
                text="Quiet silicone stress toy for office desks.",
                chunk_index=2,
            ),
        ]
    )

    candidates = indexer.query(["wireless", "headphones"], top_k=2)

    assert all(isinstance(candidate, BM25Candidate) for candidate in candidates)
    assert [candidate.chunk_id for candidate in candidates] == [
        "chunk-best",
        "chunk-secondary",
    ]
    assert candidates[0].score > candidates[1].score > 0


def test_bm25_query_handles_blank_or_unknown_keywords() -> None:
    """Require empty sparse input to produce an empty candidate list."""

    indexer = BM25Indexer()
    indexer.index([make_chunk(chunk_id="chunk-1", text="Wireless headphones.")])

    assert indexer.query([], top_k=5) == []
    assert indexer.query(["   "], top_k=5) == []
    assert indexer.query(["not-indexed"], top_k=5) == []


def test_bm25_query_matches_chinese_terms_inside_long_sentences() -> None:
    """Require Chinese product terms to match within unsegmented sentences.

    The RAG knowledge base is expected to index Chinese shopping guides. Without
    a full Chinese word segmenter, BM25 still needs a deterministic fallback
    that can match a query term such as ``无线耳机`` inside longer continuous
    text like ``高性价比无线耳机选购指南``.
    """

    indexer = BM25Indexer()
    indexer.index(
        [
            make_chunk(
                chunk_id="chunk-audio",
                text="高性价比无线耳机选购指南，重点关注降噪和续航。",
                chunk_index=0,
            ),
            make_chunk(
                chunk_id="chunk-toy",
                text="桌面解压玩具适合办公室短暂放松。",
                chunk_index=1,
            ),
        ]
    )

    candidates = indexer.query(["无线耳机"], top_k=5)

    assert [candidate.chunk_id for candidate in candidates] == ["chunk-audio"]
    assert candidates[0].score > 0


def test_bm25_indexer_replaces_previous_index_state() -> None:
    """Require repeated index() calls to rebuild rather than append state."""

    indexer = BM25Indexer()
    indexer.index([make_chunk(chunk_id="chunk-old", text="Wireless headphones.")])
    indexer.index([make_chunk(chunk_id="chunk-new", text="Stress toy guide.")])

    assert indexer.query(["wireless"], top_k=5) == []
    assert [candidate.chunk_id for candidate in indexer.query(["stress"], top_k=5)] == [
        "chunk-new"
    ]


def test_bm25_query_rejects_invalid_top_k() -> None:
    """Require invalid result limits to fail before ranking work starts."""

    indexer = BM25Indexer()
    indexer.index([make_chunk(chunk_id="chunk-1", text="Wireless headphones.")])

    with pytest.raises(ValueError, match="top_k"):
        indexer.query(["wireless"], top_k=0)


def test_batch_processor_can_wrap_bm25_indexing_as_sparse_batch() -> None:
    """Require sparse indexing work to reuse the shared C8 batch boundary."""

    chunks = [
        make_chunk(chunk_id="chunk-1", text="Wireless headphones guide."),
        make_chunk(chunk_id="chunk-2", text="Quiet stress toy guide."),
    ]
    indexer = BM25Indexer()
    processor = BatchProcessor(batch_size=1, max_retries=0)

    result = processor.run([chunks], process_batch=lambda batch: [indexer.index(batch[0])])

    bm25_index = result.successful_values()[0]
    assert bm25_index.chunk_count == 2
    assert bm25_index.term_document_frequency["guide"] == 2
    assert result.failures == []
    assert result.batches_processed == 1
