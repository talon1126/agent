"""Protect generic batch manifests, archival ordering, quarantine, and replay.

The tests use synthetic datasets and never depend on a concrete site processor.
Failures indicate lost source files, non-atomic lifecycle transitions, secret
leakage, unstable deduplication keys, or replay contract drift.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pytest

from data_ops import cli as cli_module
from data_ops.cli import process_batch
from data_ops.core.batch_manifest import (
    BatchManifest,
    BatchManifestError,
    archive_successful_batch,
    build_batch_manifest,
    calculate_contract_sha256,
    calculate_file_sha256,
    find_duplicate_batch,
    load_batch_manifest,
    quarantine_failed_batch,
    replay_batch,
)
from data_ops.core.contracts import (
    COMMON_WEB_EXPORT_COLUMNS,
    DatasetContract,
    ProcessorResult,
)
from data_ops.core.csv_io import CanonicalWriteError
from data_ops.core.validation import ValidationIssue
from data_ops.processors.registry import register_processor

BATCH_COLUMNS = COMMON_WEB_EXPORT_COLUMNS + ("external_id",)


def _contract(dataset_type: str) -> DatasetContract:
    """Create a generic contract with no website-specific fields."""

    return DatasetContract(
        dataset_type=dataset_type,
        required_columns=BATCH_COLUMNS,
        column_types={
            "dataset_type": "string",
            "batch_id": "string",
            "input_index": "integer",
            "source_url": "url",
            "captured_at": "datetime",
            "crawl_status": "string",
            "error_code": "string",
            "external_id": "string",
        },
        unique_by=("batch_id", "input_index"),
        normalized_filename_template="{dataset_type}_{batch_id}_normalized.csv",
        failed_filename_template="{dataset_type}_{batch_id}_failed.csv",
    )


def _source_frame(dataset_type: str, batch_id: str) -> pd.DataFrame:
    """Return one valid source row for lifecycle and replay tests."""

    return pd.DataFrame(
        [
            {
                "dataset_type": dataset_type,
                "batch_id": batch_id,
                "input_index": "0001",
                "source_url": "https://example.invalid/1",
                "captured_at": "2026-07-18T00:00:00Z",
                "crawl_status": "success",
                "error_code": "",
                "external_id": "000123",
            }
        ],
        columns=BATCH_COLUMNS,
        dtype="string",
    )


class BatchProcessor:
    """Provide a generic processor that accepts and preserves every source row."""

    dataset_type = "batch_process"
    contract = _contract(dataset_type)

    def validate(self, frame: pd.DataFrame) -> Sequence[ValidationIssue]:
        """Return no dataset-specific issues."""

        return ()

    def normalize(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Return a defensive copy without changing canonical columns."""

        return frame.copy()

    def split_results(
        self,
        frame: pd.DataFrame,
        issues: Sequence[ValidationIssue],
    ) -> ProcessorResult[pd.DataFrame]:
        """Return every row as normalized with exact reconciliation counts."""

        return ProcessorResult(
            normalized_rows=frame,
            failed_rows=frame.iloc[0:0].copy(),
            summary={"input_rows": len(frame), "normalized_rows": len(frame), "failed_rows": 0},
        )


def test_calculate_hashes_and_manifest_are_deterministic_and_redacted(
    tmp_path: Path,
) -> None:
    """Manifest identity uses source bytes and contract without exposing secrets."""

    source = tmp_path / "source.csv"
    source.write_bytes(b"synthetic,row\n")
    contract = _contract("manifest_example")
    output = tmp_path / "manifest_example_batch_normalized.csv"
    output.write_text("header\n", encoding="utf-8")

    first = build_batch_manifest(
        batch_id="batch_001",
        dataset_type=contract.dataset_type,
        input_path=source,
        contract=contract,
        processor="tests.ManifestProcessor",
        row_counts={"input_rows": 1, "normalized_rows": 0, "failed_rows": 1},
        status="failed",
        output_files=(output,),
        error_code="processing_error",
        error_summary="token=top-secret password=hunter2",
    )
    second = build_batch_manifest(
        batch_id="batch_002",
        dataset_type=contract.dataset_type,
        input_path=source,
        contract=contract,
        processor="tests.ManifestProcessor",
        row_counts={"input_rows": 1, "normalized_rows": 0, "failed_rows": 1},
        status="failed",
        output_files=(output,),
        error_code="processing_error",
        error_summary="token=top-secret password=hunter2",
    )
    serialized = json.dumps(first.to_dict(), sort_keys=True)

    assert calculate_file_sha256(source) == hashlib.sha256(source.read_bytes()).hexdigest()
    assert first.input_sha256 == calculate_file_sha256(source)
    assert first.contract_sha256 == calculate_contract_sha256(contract)
    assert first.deduplication_key == second.deduplication_key
    assert "top-secret" not in serialized
    assert "hunter2" not in serialized
    assert "synthetic,row" not in serialized
    assert first.input_file == "source.csv"
    assert first.output_files == (output.name,)


def test_manifest_loader_rejects_unknown_schema_and_non_boolean_replay_flag(
    tmp_path: Path,
) -> None:
    """Version and replay flags must retain strict JSON schema types."""

    source = tmp_path / "source.csv"
    source.write_text("value\n", encoding="utf-8")
    contract = _contract("manifest_schema")
    manifest = build_batch_manifest(
        batch_id="batch_schema",
        dataset_type=contract.dataset_type,
        input_path=source,
        contract=contract,
        processor="tests.ManifestProcessor",
        row_counts={"input_rows": 1, "normalized_rows": 1, "failed_rows": 0},
        status="success",
    )
    unknown_version = manifest.to_dict()
    unknown_version["schema_version"] = 2
    invalid_replay = manifest.to_dict()
    invalid_replay["replayable"] = "false"

    with pytest.raises(BatchManifestError, match="schema_version"):
        BatchManifest.from_dict(unknown_version)
    with pytest.raises(BatchManifestError, match="replayable"):
        BatchManifest.from_dict(invalid_replay)


def test_archive_successful_batch_stages_outputs_before_moving_source(
    tmp_path: Path,
) -> None:
    """A successful archive contains source, outputs, and manifest as one batch."""

    source = tmp_path / "incoming.csv"
    source.write_text("raw\nvalue\n", encoding="utf-8")
    normalized = tmp_path / "normalized.csv"
    normalized.write_text("value\nnormalized\n", encoding="utf-8")
    failed = tmp_path / "failed.csv"
    failed.write_text("value\n", encoding="utf-8")
    contract = _contract("archive_example")
    manifest = build_batch_manifest(
        batch_id="batch_archive",
        dataset_type=contract.dataset_type,
        input_path=source,
        contract=contract,
        processor="tests.ArchiveProcessor",
        row_counts={"input_rows": 1, "normalized_rows": 1, "failed_rows": 0},
        status="success",
        output_files=(normalized, failed),
    )

    batch_path = archive_successful_batch(
        manifest,
        source_path=source,
        output_paths=(normalized, failed),
        archive_root=tmp_path / "archive",
    )

    assert not source.exists()
    assert normalized.exists()
    assert failed.exists()
    assert (batch_path / manifest.input_file).read_text(encoding="utf-8") == "raw\nvalue\n"
    assert (batch_path / normalized.name).exists()
    assert (batch_path / failed.name).exists()
    assert load_batch_manifest(batch_path / "manifest.json") == manifest
    assert find_duplicate_batch(
        tmp_path / "archive",
        manifest.deduplication_key,
    ) == batch_path / "manifest.json"


def test_archive_failure_before_manifest_commit_preserves_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A staging failure cannot remove the immutable incoming source file."""

    from data_ops.core import batch_manifest as module

    source = tmp_path / "incoming.csv"
    source.write_text("raw\n", encoding="utf-8")
    output = tmp_path / "normalized.csv"
    output.write_text("value\n", encoding="utf-8")
    contract = _contract("archive_failure")
    manifest = build_batch_manifest(
        batch_id="batch_failure",
        dataset_type=contract.dataset_type,
        input_path=source,
        contract=contract,
        processor="tests.ArchiveProcessor",
        row_counts={"input_rows": 1, "normalized_rows": 1, "failed_rows": 0},
        status="success",
        output_files=(output,),
    )

    def fail_manifest_write(*args, **kwargs):
        raise OSError("simulated manifest write failure")

    monkeypatch.setattr(module, "_write_manifest_atomic", fail_manifest_write)

    with pytest.raises(OSError, match="simulated"):
        archive_successful_batch(
            manifest,
            source_path=source,
            output_paths=(output,),
            archive_root=tmp_path / "archive",
        )

    assert source.exists()
    assert not list((tmp_path / "archive").rglob("manifest.json"))


def test_quarantine_failed_batch_preserves_replay_entry(
    tmp_path: Path,
) -> None:
    """Failed processing moves source into a replayable manifest directory."""

    source = tmp_path / "invalid.csv"
    source.write_text("broken\n", encoding="utf-8")
    contract = _contract("quarantine_example")
    manifest = build_batch_manifest(
        batch_id="batch_failed",
        dataset_type=contract.dataset_type,
        input_path=source,
        contract=contract,
        processor="tests.QuarantineProcessor",
        row_counts={"input_rows": 0, "normalized_rows": 0, "failed_rows": 0},
        status="failed",
        error_code="source_file_error",
        error_summary="source file could not be decoded",
    )

    batch_path = quarantine_failed_batch(
        manifest,
        source_path=source,
        failed_root=tmp_path / "failed",
    )

    assert not source.exists()
    assert (batch_path / manifest.input_file).exists()
    loaded = load_batch_manifest(batch_path / "manifest.json")
    assert loaded.error_code == "source_file_error"
    assert loaded.replayable is True


def test_replay_batch_uses_manifest_dataset_and_current_contract(
    tmp_path: Path,
) -> None:
    """Replay resolves the manifest dataset and rejects contract drift."""

    class ReplayProcessor(BatchProcessor):
        dataset_type = "batch_replay"
        contract = _contract("batch_replay")

    register_processor(ReplayProcessor.dataset_type, ReplayProcessor)
    source = tmp_path / "replay.csv"
    _source_frame(ReplayProcessor.dataset_type, "batch_replay_001").to_csv(
        source,
        index=False,
    )
    manifest = build_batch_manifest(
        batch_id="batch_replay_001",
        dataset_type=ReplayProcessor.dataset_type,
        input_path=source,
        contract=ReplayProcessor.contract,
        processor="tests.ReplayProcessor",
        row_counts={"input_rows": 1, "normalized_rows": 0, "failed_rows": 1},
        status="failed",
        error_code="processing_error",
        error_summary="processor unavailable",
    )
    batch_path = quarantine_failed_batch(
        manifest,
        source_path=source,
        failed_root=tmp_path / "failed",
    )

    result = replay_batch(
        batch_path / "manifest.json",
        output_root=tmp_path / "replayed",
    )

    assert result.summary == {
        "input_rows": 1,
        "normalized_rows": 1,
        "failed_rows": 0,
    }
    assert (tmp_path / "replayed" / "batch_replay_batch_replay_001_normalized.csv").exists()


def test_process_batch_archives_success_and_script_uses_uv(
    tmp_path: Path,
) -> None:
    """The CLI lifecycle archives successful input and the wrapper uses uv."""

    register_processor(BatchProcessor.dataset_type, BatchProcessor)
    source = tmp_path / "batch.csv"
    _source_frame(BatchProcessor.dataset_type, "batch_process_001").to_csv(
        source,
        index=False,
    )

    manifest = process_batch(
        BatchProcessor.dataset_type,
        source,
        tmp_path / "runtime",
    )

    assert manifest.status == "success"
    assert not source.exists()
    assert (
        tmp_path
        / "runtime"
        / "archive"
        / BatchProcessor.dataset_type
        / "batch_process_001"
        / "manifest.json"
    ).exists()
    script = Path("scripts/run_data_ops.ps1").read_text(encoding="utf-8")
    assert "uv run --project services/data-ops talonmart-data-ops" in script
    assert "function Invoke-JdProductProcessing" in script
    assert "function Invoke-DataOpsProcessing" in script
    assert "Copy-Item -LiteralPath" in script
    assert 'Join-Path $runtimeRoot "inbox"' in script
    assert "--dataset-type $DatasetType" in script
    assert "Unsupported DatasetType" not in script
    assert "InputPath" in script
    assert "DatasetType" in script
    assert "OutputRoot" in script
    project = Path("services/data-ops/pyproject.toml").read_text(encoding="utf-8")
    assert 'talonmart-data-ops = "data_ops.app:entrypoint"' in project
    assert "var/rpa/" in Path(".gitignore").read_text(encoding="utf-8")


def test_process_batch_quarantines_processor_failure(tmp_path: Path) -> None:
    """A processor exception moves source to failed with a stable replay manifest."""

    class FailingProcessor(BatchProcessor):
        dataset_type = "batch_processing_failure"
        contract = _contract("batch_processing_failure")

        def normalize(self, frame: pd.DataFrame) -> pd.DataFrame:
            raise RuntimeError("synthetic row details must not reach manifest")

    register_processor(FailingProcessor.dataset_type, FailingProcessor)
    source = tmp_path / "failing.csv"
    _source_frame(FailingProcessor.dataset_type, "batch_failure_001").to_csv(
        source,
        index=False,
    )
    runtime_root = tmp_path / "runtime"

    with pytest.raises(BatchManifestError, match="processing_error"):
        process_batch(FailingProcessor.dataset_type, source, runtime_root)

    manifest_path = (
        runtime_root
        / "failed"
        / FailingProcessor.dataset_type
        / "batch_failure_001"
        / "manifest.json"
    )
    manifest = load_batch_manifest(manifest_path)
    assert not source.exists()
    assert manifest.error_code == "processing_error"
    assert "synthetic row details" not in manifest.error_summary
    assert manifest.row_counts == {
        "input_rows": 1,
        "normalized_rows": 0,
        "failed_rows": 1,
    }
    assert (manifest_path.parent / manifest.input_file).exists()


def test_process_batch_quarantine_records_partial_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second output-write failure keeps the first artifact in failed metadata."""

    class PartialOutputProcessor(BatchProcessor):
        dataset_type = "batch_partial_output"
        contract = _contract("batch_partial_output")

    register_processor(PartialOutputProcessor.dataset_type, PartialOutputProcessor)
    source = tmp_path / "partial.csv"
    _source_frame(PartialOutputProcessor.dataset_type, "batch_partial_001").to_csv(
        source,
        index=False,
    )
    runtime_root = tmp_path / "runtime"
    original_writer = cli_module.write_canonical_csv
    write_count = 0

    def fail_second_write(*args, **kwargs):
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise CanonicalWriteError("simulated failed CSV write")
        return original_writer(*args, **kwargs)

    monkeypatch.setattr(cli_module, "write_canonical_csv", fail_second_write)

    with pytest.raises(BatchManifestError, match="output_write_error"):
        process_batch(PartialOutputProcessor.dataset_type, source, runtime_root)

    manifest_path = (
        runtime_root
        / "failed"
        / PartialOutputProcessor.dataset_type
        / "batch_partial_001"
        / "manifest.json"
    )
    manifest = load_batch_manifest(manifest_path)
    normalized_name = "batch_partial_output_batch_partial_001_normalized.csv"
    assert manifest.output_files == (normalized_name,)
    assert (manifest_path.parent / normalized_name).exists()


def test_replay_rejects_manifest_contract_mismatch(tmp_path: Path) -> None:
    """Replay stops when the registered contract differs from archived identity."""

    class DriftedProcessor(BatchProcessor):
        dataset_type = "batch_drift"
        contract = _contract("batch_drift")

    register_processor(DriftedProcessor.dataset_type, DriftedProcessor)
    source = tmp_path / "drift.csv"
    _source_frame(DriftedProcessor.dataset_type, "batch_drift_001").to_csv(
        source,
        index=False,
    )
    original_contract = _contract("batch_drift")
    manifest = build_batch_manifest(
        batch_id="batch_drift_001",
        dataset_type=DriftedProcessor.dataset_type,
        input_path=source,
        contract=original_contract,
        processor="tests.DriftedProcessor",
        row_counts={"input_rows": 1, "normalized_rows": 0, "failed_rows": 1},
        status="failed",
        error_code="processing_error",
        error_summary="retry later",
    )
    batch_path = quarantine_failed_batch(
        manifest,
        source_path=source,
        failed_root=tmp_path / "failed",
    )
    DriftedProcessor.contract = DatasetContract(
        dataset_type="batch_drift",
        required_columns=BATCH_COLUMNS + ("new_column",),
        column_types={**dict(original_contract.column_types), "new_column": "string"},
        unique_by=original_contract.unique_by,
        normalized_filename_template=original_contract.normalized_filename_template,
        failed_filename_template=original_contract.failed_filename_template,
    )

    with pytest.raises(BatchManifestError, match="contract"):
        replay_batch(batch_path / "manifest.json", output_root=tmp_path / "replayed")


def test_replay_rejects_corrected_source_from_another_batch(tmp_path: Path) -> None:
    """Corrected replay input must retain the failed manifest's batch identity."""

    class IdentityProcessor(BatchProcessor):
        dataset_type = "batch_replay_identity"
        contract = _contract(dataset_type)

    register_processor(IdentityProcessor.dataset_type, IdentityProcessor)
    source = tmp_path / "source.csv"
    _source_frame(IdentityProcessor.dataset_type, "batch_identity_001").to_csv(
        source,
        index=False,
    )
    manifest = build_batch_manifest(
        batch_id="batch_identity_001",
        dataset_type=IdentityProcessor.dataset_type,
        input_path=source,
        contract=IdentityProcessor.contract,
        processor="tests.IdentityProcessor",
        row_counts={"input_rows": 1, "normalized_rows": 0, "failed_rows": 1},
        status="failed",
        error_code="processing_error",
        error_summary="retry with corrected copy",
    )
    failed_batch = quarantine_failed_batch(
        manifest,
        source_path=source,
        failed_root=tmp_path / "failed",
    )
    corrected = tmp_path / "corrected.csv"
    _source_frame(IdentityProcessor.dataset_type, "batch_identity_002").to_csv(
        corrected,
        index=False,
    )

    with pytest.raises(BatchManifestError, match="batch_id"):
        replay_batch(
            failed_batch / "manifest.json",
            output_root=tmp_path / "replayed",
            source_path=corrected,
        )
