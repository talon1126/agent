"""Expose deterministic dataset processing through a small command-line API.

The CLI requires an explicit dataset type, source path, and output root. It
coordinates the generic reader, common validation, registered processor, and
canonical writers without importing a concrete dataset implementation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from data_ops.core.batch_manifest import (
    BatchManifest,
    BatchManifestError,
    DuplicateBatchError,
    archive_successful_batch,
    build_batch_manifest,
    calculate_file_sha256,
    quarantine_failed_batch,
)
from data_ops.core.contracts import ProcessorResult
from data_ops.core.csv_io import (
    CanonicalWriteError,
    SourceFileError,
    read_source_file,
    write_canonical_csv,
)
from data_ops.core.validation import (
    DatasetValidationError,
    ValidationIssue,
    validate_common_columns,
    validate_dataset_selection,
    validate_single_batch,
)
from data_ops.processors.registry import ProcessorRegistryError, get_processor

_SAFE_BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _raise_for_issues(issues: Sequence[ValidationIssue]) -> None:
    """Raise the shared validation exception when any issue is present."""

    if issues:
        raise DatasetValidationError(tuple(issues))


def _validate_processor_result(
    source: pd.DataFrame,
    result: ProcessorResult[pd.DataFrame],
) -> None:
    """Reject processor output that loses or duplicates source rows."""

    output_rows = len(result.normalized_rows) + len(result.failed_rows)
    if output_rows != len(source):
        raise DatasetValidationError(
            (
                ValidationIssue(
                    code="processor_reconciliation_failed",
                    message=(
                        f"processor returned {output_rows} rows for "
                        f"{len(source)} input rows"
                    ),
                ),
            )
        )
    if any(not isinstance(value, int) or value < 0 for value in result.summary.values()):
        raise DatasetValidationError(
            (
                ValidationIssue(
                    code="invalid_processor_summary",
                    message="processor summary values must be non-negative integers",
                ),
            )
        )
    expected_counts = {
        "input_rows": len(source),
        "normalized_rows": len(result.normalized_rows),
        "failed_rows": len(result.failed_rows),
    }
    mismatched_counts = {
        key: (result.summary.get(key), expected)
        for key, expected in expected_counts.items()
        if result.summary.get(key) != expected
    }
    if mismatched_counts:
        detail = ", ".join(
            f"{key}={actual!r} expected {expected}"
            for key, (actual, expected) in mismatched_counts.items()
        )
        raise DatasetValidationError(
            (
                ValidationIssue(
                    code="invalid_processor_summary",
                    message=f"processor summary does not reconcile: {detail}",
                ),
            )
        )


def process_dataset(
    dataset_type: str,
    input_path: str | Path,
    output_root: str | Path,
    *,
    encoding: str = "utf-8-sig",
    delimiter: str = ",",
    sheet_name: str | int = 0,
) -> ProcessorResult[pd.DataFrame]:
    """Process one raw file through its explicitly registered dataset processor.

    Args:
        dataset_type: Processor routing key selected by the caller.
        input_path: CSV or XLSX source path.
        output_root: Directory that receives normalized and failed CSV files.
        encoding: Explicit CSV source encoding.
        delimiter: Explicit CSV source delimiter.
        sheet_name: XLSX worksheet name or position.

    Returns:
        Processor result containing normalized rows, failed rows, and summary.

    Raises:
        ProcessorRegistryError: If dataset_type is unknown or misconfigured.
        SourceFileError: If the source file cannot be read.
        DatasetValidationError: If common, routing, batch, or reconciliation
            validation fails.
        CanonicalWriteError: If processor output violates its dataset contract.

    Side Effects:
        Writes canonical normalized and failed CSV files under output_root.
    """

    processor = get_processor(dataset_type)
    frame = read_source_file(
        input_path,
        encoding=encoding,
        delimiter=delimiter,
        sheet_name=sheet_name,
    )
    common_issues = (
        *validate_common_columns(frame),
        *validate_dataset_selection(frame, dataset_type),
        *validate_single_batch(frame),
    )
    _raise_for_issues(common_issues)

    processor_issues = tuple(processor.validate(frame.copy()))
    normalized = processor.normalize(frame.copy())
    result = processor.split_results(normalized, processor_issues)
    _validate_processor_result(frame, result)

    batch_id = str(frame["batch_id"].iloc[0]).strip()
    filename_values = {"dataset_type": dataset_type, "batch_id": batch_id}
    root = Path(output_root)
    normalized_path = root / processor.contract.normalized_filename_template.format(
        **filename_values
    )
    failed_path = root / processor.contract.failed_filename_template.format(
        **filename_values
    )
    write_canonical_csv(
        result.normalized_rows,
        normalized_path,
        columns=processor.contract.all_columns,
    )
    write_canonical_csv(
        result.failed_rows,
        failed_path,
        columns=processor.contract.all_columns,
    )
    return result


def _processor_name(processor: object) -> str:
    """Return a stable qualified class name without instance state."""

    processor_type = type(processor)
    return f"{processor_type.__module__}.{processor_type.__qualname__}"


def _failure_code(exc: Exception) -> str:
    """Map controlled and unexpected failures to stable manifest error codes."""

    if isinstance(exc, DatasetValidationError):
        return exc.issues[0].code
    if isinstance(exc, SourceFileError):
        return "source_file_error"
    if isinstance(exc, CanonicalWriteError):
        return "output_write_error"
    if isinstance(exc, BatchManifestError):
        return "batch_manifest_error"
    return "processing_error"


def _safe_failure_batch_id(candidate: str, source_hash: str) -> str:
    """Use a valid source batch ID or a deterministic hash-based fallback."""

    if _SAFE_BATCH_ID_PATTERN.fullmatch(candidate):
        return candidate
    return f"failed-{source_hash[:12]}"


def _output_paths(
    dataset_type: str,
    batch_id: str,
    output_root: Path,
    processor: object,
) -> tuple[Path, Path]:
    """Return normalized and failed output paths for one processor contract."""

    contract = processor.contract
    values = {"dataset_type": dataset_type, "batch_id": batch_id}
    return (
        output_root / contract.normalized_filename_template.format(**values),
        output_root / contract.failed_filename_template.format(**values),
    )


def process_batch(
    dataset_type: str,
    input_path: str | Path,
    runtime_root: str | Path,
    *,
    encoding: str = "utf-8-sig",
    delimiter: str = ",",
    sheet_name: str | int = 0,
) -> BatchManifest:
    """Process and transition one source file into archive or failed storage.

    Args:
        dataset_type: Explicit registered processor routing key.
        input_path: Immutable source CSV or XLSX path.
        runtime_root: Root containing normalized, archive, and failed states.
        encoding: Explicit CSV encoding.
        delimiter: Explicit CSV delimiter.
        sheet_name: XLSX sheet name or position.

    Returns:
        The success manifest published under archive.

    Raises:
        ProcessorRegistryError: If dataset_type is not registered.
        DuplicateBatchError: If source bytes and contract were already archived.
        BatchManifestError: If processing failed after the processor resolved;
            the source is quarantined before this error is raised.

    Side Effects:
        Writes canonical outputs, publishes one manifest directory, and moves
        the source into archive or failed storage.
    """

    source = Path(input_path)
    root = Path(runtime_root)
    processor = get_processor(dataset_type)
    source_hash = calculate_file_sha256(source)
    processor_name = _processor_name(processor)
    frame: pd.DataFrame | None = None
    batch_id = f"failed-{source_hash[:12]}"
    expected_outputs: tuple[Path, Path] = ()
    try:
        frame = read_source_file(
            source,
            encoding=encoding,
            delimiter=delimiter,
            sheet_name=sheet_name,
        )
        if "batch_id" in frame and not frame.empty:
            candidates = {str(value).strip() for value in frame["batch_id"].tolist()}
            if len(candidates) == 1:
                batch_id = _safe_failure_batch_id(candidates.pop(), source_hash)
        expected_outputs = _output_paths(
            dataset_type,
            batch_id,
            root / "normalized",
            processor,
        )
        result = process_dataset(
            dataset_type,
            source,
            root / "normalized",
            encoding=encoding,
            delimiter=delimiter,
            sheet_name=sheet_name,
        )
        manifest = build_batch_manifest(
            batch_id=batch_id,
            dataset_type=dataset_type,
            input_path=source,
            contract=processor.contract,
            processor=processor_name,
            row_counts=result.summary,
            status="success",
            output_files=expected_outputs,
        )
        archive_successful_batch(
            manifest,
            source_path=source,
            output_paths=expected_outputs,
            archive_root=root / "archive",
        )
        return manifest
    except DuplicateBatchError:
        raise
    except Exception as exc:
        if not source.is_file():
            raise BatchManifestError(
                "batch failed after source transition and cannot be quarantined"
            ) from exc
        existing_outputs = tuple(path for path in expected_outputs if path.is_file())
        error_code = _failure_code(exc)
        manifest = build_batch_manifest(
            batch_id=_safe_failure_batch_id(batch_id, source_hash),
            dataset_type=dataset_type,
            input_path=source,
            contract=processor.contract,
            processor=processor_name,
            row_counts={
                "input_rows": len(frame) if frame is not None else 0,
                "normalized_rows": 0,
                "failed_rows": len(frame) if frame is not None else 0,
            },
            status="failed",
            output_files=existing_outputs,
            error_code=error_code,
            error_summary=f"{error_code}: {type(exc).__name__}",
        )
        failed_path = quarantine_failed_batch(
            manifest,
            source_path=source,
            failed_root=root / "failed",
            output_paths=existing_outputs,
        )
        raise BatchManifestError(
            f"{error_code}: batch quarantined at {failed_path}"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    """Build the stable command-line contract for file processing."""

    parser = argparse.ArgumentParser(prog="talonmart-data-ops")
    parser.add_argument("--dataset-type", required=True)
    parser.add_argument("--input-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--delimiter", default=",")
    parser.add_argument("--sheet-name", default="0")
    return parser


def _parse_sheet_name(value: str) -> str | int:
    """Interpret numeric worksheet selectors as zero-based indexes."""

    return int(value) if value.isdigit() else value


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and convert controlled data errors to a non-zero exit code."""

    args = _build_parser().parse_args(argv)
    try:
        manifest = process_batch(
            args.dataset_type,
            args.input_path,
            args.output_root,
            encoding=args.encoding,
            delimiter=args.delimiter,
            sheet_name=_parse_sheet_name(args.sheet_name),
        )
    except (
        CanonicalWriteError,
        DatasetValidationError,
        DuplicateBatchError,
        BatchManifestError,
        ProcessorRegistryError,
        SourceFileError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(manifest.to_dict(), ensure_ascii=True, sort_keys=True))
    return 0


def entrypoint() -> None:
    """Invoke main as the installed console script."""

    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
