"""Protect document deduplication before any source loader is invoked.

C1 establishes the first executable boundary of ``IngestionPipeline``. These
tests verify that hashing uses original file bytes, successful unchanged
sources stop before Loader work, force mode bypasses deduplication, and skipped
runs persist a complete ingestion trace summary.
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
pipeline_module = importlib.import_module("src.ingestion.pipeline")

Document = types_module.Document
IngestionError = errors_module.IngestionError
IngestionPipeline = pipeline_module.IngestionPipeline
calculate_sha256 = pipeline_module.calculate_sha256
should_skip_document = pipeline_module.should_skip_document


def test_calculate_sha256_hashes_original_file_bytes(tmp_path: Path) -> None:
    """Require byte-stable hashing before parsing or Markdown conversion."""

    source = tmp_path / "guide.md"
    content = b"# Guide\r\n\r\nOriginal source bytes.\x00"
    source.write_bytes(content)

    digest = calculate_sha256(source, block_size=7)

    assert digest == sha256(content).hexdigest()


def test_calculate_sha256_wraps_unreadable_sources() -> None:
    """Require filesystem failures to use the ingestion error boundary."""

    missing = Path("missing-c1-source.md")

    with pytest.raises(IngestionError) as captured:
        calculate_sha256(missing)

    assert captured.value.context["operation"] == "source_hash"
    assert captured.value.context["source"].endswith("missing-c1-source.md")


def test_should_skip_document_uses_repository_and_force_bypasses_lookup() -> None:
    """Require force mode to continue without consulting persisted success state."""

    documents = Mock()
    documents.has_successful_source_hash.return_value = True

    assert (
        should_skip_document(
            documents,
            collection_id="shopping_guides",
            source_path="D:/data/guide.md",
            source_hash="a" * 64,
        )
        is True
    )
    assert (
        should_skip_document(
            documents,
            collection_id="shopping_guides",
            source_path="D:/data/guide.md",
            source_hash="a" * 64,
            force=True,
        )
        is False
    )
    documents.has_successful_source_hash.assert_called_once_with(
        collection_id="shopping_guides",
        source_path="D:/data/guide.md",
        source_hash="a" * 64,
    )


def test_ingestion_pipeline_skips_before_loader_and_persists_trace(
    tmp_path: Path,
) -> None:
    """Require unchanged successful sources to stop before expensive stages."""

    source = tmp_path / "guide.md"
    source.write_text("# Existing guide", encoding="utf-8")
    loader = Mock()
    documents = Mock()
    documents.has_successful_source_hash.return_value = True
    traces = Mock()
    traces.upsert_ingestion_trace.side_effect = lambda trace: trace
    pipeline = IngestionPipeline(
        loader=loader,
        document_repository=documents,
        trace_repository=traces,
        trace_id_factory=lambda: "trace-c1-skip",
    )

    result = pipeline.run(source, collection_id="shopping_guides")

    assert result.status == "skipped"
    assert result.document is None
    assert result.trace_id == "trace-c1-skip"
    assert result.trace_summary["skip_reason"] == "successful_source_hash_match"
    loader.load.assert_not_called()
    stored_trace = traces.upsert_ingestion_trace.call_args.args[0]
    assert stored_trace.status == "skipped"
    assert stored_trace.source_uri == str(source.resolve())
    assert stored_trace.source_hash == calculate_sha256(source)
    assert stored_trace.basic_info["trace_type"] == "ingestion"
    assert stored_trace.stages[0]["stage"] == "dedup"
    assert stored_trace.stages[0]["matched"] is True
    assert stored_trace.summary_metrics["skipped"] is True
    assert stored_trace.finished_at is not None


def test_ingestion_pipeline_calls_loader_when_source_changed_or_forced(
    tmp_path: Path,
) -> None:
    """Require changed and force-requested sources to continue into Loader."""

    source = tmp_path / "new-guide.md"
    source.write_text("# New guide", encoding="utf-8")
    loaded_document = Document(
        id="doc-new",
        text="# New guide",
        metadata={"source_path": str(source.resolve())},
    )
    loader = Mock()
    loader.load.return_value = loaded_document
    documents = Mock()
    documents.has_successful_source_hash.return_value = False
    traces = Mock()
    pipeline = IngestionPipeline(
        loader=loader,
        document_repository=documents,
        trace_repository=traces,
        trace_id_factory=lambda: "trace-c1-load",
    )

    changed_result = pipeline.run(source, collection_id="shopping_guides")
    forced_result = pipeline.run(
        source,
        collection_id="shopping_guides",
        force=True,
    )

    assert changed_result.status == "loaded"
    assert changed_result.document == loaded_document
    assert forced_result.status == "loaded"
    assert forced_result.document == loaded_document
    assert loader.load.call_count == 2
    loader.load.assert_called_with(source.resolve())
    documents.has_successful_source_hash.assert_called_once()
    traces.upsert_ingestion_trace.assert_not_called()
