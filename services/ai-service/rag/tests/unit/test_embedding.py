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
BatchFailure = ingestion_embedding_module.BatchFailure
BatchProcessor = ingestion_embedding_module.BatchProcessor
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
            "document_id": "doc-stress-toys",
            "source_path": "shopping_guides/stress-toys.md",
            "section_path": ["Stress Toys", "Materials"],
        },
        chunk_index=0,
        start_offset=0,
        end_offset=len(text),
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


def test_dense_encoder_embeds_chunk_batches_with_provider_batch_api() -> None:
    """Require C8 Dense batching to call ``embed_batch`` once per chunk batch."""

    first = make_chunk(chunk_id="chunk-1", text="Wireless headphones.")
    second = make_chunk(chunk_id="chunk-2", text="Office stress toy.")
    embedding = Mock()
    embedding.embed_batch.return_value = [[0.1, 0.2], [0.3, 0.4]]
    encoder = DenseEncoder(embedding=embedding)

    results = encoder.encode_batch([first, second])

    assert [result.chunk_id for result in results] == ["chunk-1", "chunk-2"]
    assert [result.vector for result in results] == [[0.1, 0.2], [0.3, 0.4]]
    assert [result.content_hash for result in results] == [
        content_hash(first.text),
        content_hash(second.text),
    ]
    embedding.embed_batch.assert_called_once_with([first.text, second.text])
    assert not embedding.embed.called


def test_dense_encoder_wraps_batch_vector_count_mismatch() -> None:
    """Require invalid provider batch cardinality to fail before upsert."""

    chunks = [
        make_chunk(chunk_id="chunk-1", text="Wireless headphones."),
        make_chunk(chunk_id="chunk-2", text="Office stress toy."),
    ]
    embedding = Mock()
    embedding.embed_batch.return_value = [[0.1, 0.2]]
    encoder = DenseEncoder(embedding=embedding)

    with pytest.raises(IngestionError, match="Unable to encode dense vector batch"):
        encoder.encode_batch(chunks)


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


def test_batch_processor_splits_items_by_configured_size_and_preserves_order() -> None:
    """Require generic batch execution to honor configured batch size.

    C8 introduces BatchProcessor as the shared batching boundary for indexing
    work. The processor must split input deterministically and return successful
    values in original item order so later storage can preserve chunk ordering.
    """

    processor = BatchProcessor(batch_size=2, max_retries=0)
    observed_batches: list[list[int]] = []

    def process_batch(batch: list[int]) -> list[str]:
        """Record the batch and return one output per input item."""

        observed_batches.append(batch)
        return [f"encoded-{item}" for item in batch]

    result = processor.run([1, 2, 3, 4, 5], process_batch=process_batch)

    assert observed_batches == [[1, 2], [3, 4], [5]]
    assert result.successful_values() == [
        "encoded-1",
        "encoded-2",
        "encoded-3",
        "encoded-4",
        "encoded-5",
    ]
    assert result.failures == []


def test_batch_processor_isolates_failed_items_and_retries_successfully() -> None:
    """Require one failed item not to discard healthy items in the same batch."""

    processor = BatchProcessor(batch_size=2, max_retries=1)
    attempts: dict[str, int] = {"bad": 0}

    def process_batch(batch: list[str]) -> list[str]:
        """Fail mixed batches and make the bad item recover on retry."""

        if "bad" in batch and len(batch) > 1:
            raise RuntimeError("mixed batch failed")
        if batch == ["bad"]:
            attempts["bad"] += 1
            if attempts["bad"] == 1:
                raise RuntimeError("temporary item failure")
        return [f"ok-{item}" for item in batch]

    result = processor.run(["good", "bad", "tail"], process_batch=process_batch)

    assert result.successful_values() == ["ok-good", "ok-bad", "ok-tail"]
    assert result.failures == []
    assert attempts["bad"] == 2


def test_batch_processor_records_non_retryable_failures_without_blocking() -> None:
    """Require permanent failures to be reported while other items continue."""

    processor = BatchProcessor(batch_size=2, max_retries=1)

    def process_batch(batch: list[str]) -> list[str]:
        """Always fail the bad item and succeed healthy single-item retries."""

        if "bad" in batch:
            raise RuntimeError("permanent item failure")
        return [f"ok-{item}" for item in batch]

    result = processor.run(["good", "bad", "tail"], process_batch=process_batch)

    assert result.successful_values() == ["ok-good", "ok-tail"]
    assert len(result.failures) == 1
    assert isinstance(result.failures[0], BatchFailure)
    assert result.failures[0].item_index == 1
    assert result.failures[0].attempts == 2
    assert result.failures[0].error_message == "permanent item failure"


def test_batch_processor_throttles_between_top_level_batches() -> None:
    """Require optional throttling to run between configured top-level batches."""

    sleep_calls: list[float] = []
    processor = BatchProcessor(
        batch_size=2,
        max_retries=0,
        throttle_seconds=0.25,
        sleeper=sleep_calls.append,
    )

    result = processor.run(
        [1, 2, 3, 4, 5],
        process_batch=lambda batch: [f"ok-{item}" for item in batch],
    )

    assert result.successful_values() == ["ok-1", "ok-2", "ok-3", "ok-4", "ok-5"]
    assert sleep_calls == [0.25, 0.25]


def test_embedding_step_run_batch_orchestrates_dense_and_bm25() -> None:
    """Require C8 orchestration to batch Dense work and build one BM25 index."""

    chunks = [
        make_chunk(chunk_id="chunk-1", text="Wireless headphones buying guide."),
        make_chunk(chunk_id="chunk-2", text="Noise cancellation and battery life."),
        make_chunk(chunk_id="chunk-3", text="Office stress toy material guide."),
    ]
    embedding = Mock()
    embedding.embed_batch.side_effect = [
        [[1.0, 0.0], [0.5, 0.5]],
        [[0.0, 1.0]],
    ]
    step = EmbeddingStep(
        dense_encoder=DenseEncoder(embedding=embedding),
        batch_processor=BatchProcessor(batch_size=2, max_retries=0),
    )

    result = step.run_batch(chunks)

    assert [dense.chunk_id for dense in result.dense_results] == [
        "chunk-1",
        "chunk-2",
        "chunk-3",
    ]
    assert result.bm25_index.chunk_count == 3
    assert result.bm25_index.term_document_frequency["wireless"] == 1
    assert result.dense_failures == []
    assert result.bm25_failures == []
    assert result.dense_batches_processed == 2
    assert result.bm25_batches_processed == 1
    assert embedding.embed_batch.call_args_list[0].args[0] == [
        chunks[0].text,
        chunks[1].text,
    ]
    assert embedding.embed_batch.call_args_list[1].args[0] == [chunks[2].text]
    assert not embedding.embed.called


def test_embedding_step_run_batch_reuses_vectors_and_expands_duplicate_content() -> None:
    """Require batch indexing to return one dense result per ordered chunk.

    Persisted vectors must be reused by content hash without calling the
    provider again. Duplicate content introduced within the current run must
    also share one provider result while still producing distinct storage
    records for each chunk ID.
    """

    persisted = make_chunk(chunk_id="chunk-existing", text="Stable guidance.")
    new = make_chunk(chunk_id="chunk-new", text="New guidance.")
    duplicate = make_chunk(chunk_id="chunk-duplicate", text="New guidance.")
    embedding = Mock()
    embedding.embed_batch.return_value = [[0.25, 0.75]]
    step = EmbeddingStep(
        dense_encoder=DenseEncoder(embedding=embedding),
        batch_processor=BatchProcessor(batch_size=8, max_retries=0),
    )

    result = step.run_batch(
        [persisted, new, duplicate],
        existing_vectors_by_hash={
            content_hash(persisted.text): [1.0, 0.0],
        },
    )

    assert [item.chunk_id for item in result.dense_results] == [
        persisted.id,
        new.id,
        duplicate.id,
    ]
    assert [item.vector for item in result.dense_results] == [
        [1.0, 0.0],
        [0.25, 0.75],
        [0.25, 0.75],
    ]
    embedding.embed_batch.assert_called_once_with([new.text])
