"""Verify DenseEncoder and embedding-stage orchestration contracts.

C6 keeps dense encoding deliberately narrow: it computes a stable content hash,
skips chunks whose content hash already exists, and embeds one chunk at a time.
Batch sizing, retries, and failure isolation are reserved for C8 so this suite
guards against prematurely mixing batch-processing responsibilities into the
dense encoder.
"""

from __future__ import annotations

import importlib
import sys
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAG_ROOT))

errors_module = importlib.import_module("src.core.errors")
types_module = importlib.import_module("src.core.types")
embedding_module = importlib.import_module("src.libs.embedding")
ingestion_embedding_module = importlib.import_module("src.ingestion.embedding")

Chunk = types_module.Chunk
FakeEmbedding = embedding_module.FakeEmbedding
IngestionError = errors_module.IngestionError
DenseEncoder = ingestion_embedding_module.DenseEncoder
DenseEncodingResult = ingestion_embedding_module.DenseEncodingResult
EmbeddingStep = ingestion_embedding_module.EmbeddingStep


def make_chunk(
    *,
    chunk_id: str = "chunk-1",
    text: str = "Quiet silicone stress toys are suitable for office use.",
) -> Chunk:
    """Create a valid chunk for dense-encoding tests.

    Args:
        chunk_id: Stable chunk identifier used in expected results.
        text: Searchable text to hash and embed.

    Returns:
        A validated ``Chunk`` matching the existing ``core.types`` contract.
    """

    return Chunk(
        id=chunk_id,
        text=text,
        metadata={
            "source_path": "shopping_guides/stress-toys.md",
            "section_path": ["Stress Toys", "Materials"],
        },
        chunk_index=0,
        start_offset=0,
        end_offset=len(text),
        source_ref={"document_id": "doc-stress-toys"},
    )


def content_hash(text: str) -> str:
    """Return the expected C6 SHA256 content hash for test assertions."""

    return sha256(text.encode("utf-8")).hexdigest()


def test_dense_encoder_skips_chunks_with_existing_content_hash() -> None:
    """Require differential encoding to be driven by chunk text hashes.

    If the current chunk text hash already exists in storage, DenseEncoder must
    report that embedding is unnecessary. This prevents repeated ingestion from
    re-calling the embedding provider for unchanged content.
    """

    chunk = make_chunk()
    encoder = DenseEncoder(embedding=FakeEmbedding(dimensions=4))

    assert encoder.content_hash(chunk) == content_hash(chunk.text)
    assert encoder.should_encode(chunk, existing_content_hashes=set()) is True
    assert (
        encoder.should_encode(
            chunk,
            existing_content_hashes={content_hash(chunk.text)},
        )
        is False
    )


def test_dense_encoder_embeds_one_chunk_without_batching() -> None:
    """Require DenseEncoder.encode() to produce one vector record per call."""

    chunk = make_chunk()
    embedding = Mock()
    embedding.embed.return_value = [0.1, 0.2, 0.3]
    encoder = DenseEncoder(embedding=embedding)

    result = encoder.encode(chunk)

    assert isinstance(result, DenseEncodingResult)
    assert result.chunk_id == chunk.id
    assert result.content_hash == content_hash(chunk.text)
    assert result.vector == [0.1, 0.2, 0.3]
    assert result.metadata == {"chunk_index": 0}
    embedding.embed.assert_called_once_with(chunk.text)
    assert not embedding.embed_batch.called


def test_dense_encoder_wraps_provider_failures_as_ingestion_errors() -> None:
    """Require embedding provider errors to cross the ingestion error boundary."""

    chunk = make_chunk()
    embedding = Mock()
    embedding.embed.side_effect = RuntimeError("provider unavailable")
    encoder = DenseEncoder(embedding=embedding)

    with pytest.raises(IngestionError, match="Unable to encode dense vector") as captured:
        encoder.encode(chunk)

    assert captured.value.context == {
        "operation": "dense_encode",
        "chunk_id": chunk.id,
        "content_hash": content_hash(chunk.text),
    }


def test_dense_encoder_rejects_non_finite_vectors_before_storage() -> None:
    """Require invalid numeric vectors to fail before pgvector upsert.

    Dense vectors containing NaN or infinity cannot be safely ranked or stored.
    C6 should surface this as an ingestion-stage failure instead of passing the
    invalid payload to later storage tasks.
    """

    chunk = make_chunk()
    embedding = Mock()
    embedding.embed.return_value = [1.0, float("nan")]
    encoder = DenseEncoder(embedding=embedding)

    with pytest.raises(IngestionError, match="Unable to encode dense vector"):
        encoder.encode(chunk)


def test_embedding_step_runs_dense_encoding_and_skips_existing_hashes() -> None:
    """Require EmbeddingStep.run_dense() to preserve chunk order for new vectors.

    C6 orchestration must skip unchanged content while keeping output order for
    chunks that still need Dense vectors. It must not call ``embed_batch()``,
    because C8 owns batch processing and retry behavior.
    """

    first = make_chunk(chunk_id="chunk-1", text="Already indexed content.")
    second = make_chunk(chunk_id="chunk-2", text="New searchable content.")
    embedding = Mock()
    embedding.embed.return_value = [1.0, 0.0]
    encoder = DenseEncoder(embedding=embedding)
    step = EmbeddingStep(dense_encoder=encoder)

    results = step.run_dense(
        [first, second],
        existing_content_hashes={content_hash(first.text)},
    )

    assert [result.chunk_id for result in results] == ["chunk-2"]
    assert results[0].content_hash == content_hash(second.text)
    embedding.embed.assert_called_once_with(second.text)
    assert not embedding.embed_batch.called


def test_embedding_step_deduplicates_current_run_without_mutating_input_set() -> None:
    """Require run_dense() to keep caller-owned skip state immutable.

    The step may track hashes generated during the current run so duplicate
    chunk content does not trigger two provider calls. That internal state must
    not mutate the caller's ``existing_content_hashes`` set, because future
    repository implementations will own when a hash becomes durable.
    """

    first = make_chunk(chunk_id="chunk-1", text="Duplicate chunk content.")
    duplicate = make_chunk(chunk_id="chunk-2", text="Duplicate chunk content.")
    existing_hashes = {"0" * 64}
    embedding = Mock()
    embedding.embed.return_value = [0.25, 0.75]
    step = EmbeddingStep(dense_encoder=DenseEncoder(embedding=embedding))

    results = step.run_dense(
        [first, duplicate],
        existing_content_hashes=existing_hashes,
    )

    assert [result.chunk_id for result in results] == ["chunk-1"]
    assert existing_hashes == {"0" * 64}
    embedding.embed.assert_called_once_with(first.text)
