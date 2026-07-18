"""Protect generic file reading, validation, processing, and canonical output.

The suite uses a synthetic processor so the core remains independent from JD or
any other website. Failures indicate raw-value loss, unstable output bytes,
incorrect common-column validation, or a broken CLI error boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pytest

from data_ops.cli import main, process_dataset
from data_ops.core.contracts import (
    COMMON_WEB_EXPORT_COLUMNS,
    DatasetContract,
    ProcessorResult,
)
from data_ops.core.csv_io import (
    SourceFileError,
    read_source_file,
    write_canonical_csv,
)
from data_ops.core.validation import (
    DatasetValidationError,
    ValidationIssue,
    validate_common_columns,
)
from data_ops.processors.registry import register_processor

CORE_DATASET_TYPE = "core_example"
CORE_COLUMNS = COMMON_WEB_EXPORT_COLUMNS + ("external_id", "label")
CORE_CONTRACT = DatasetContract(
    dataset_type=CORE_DATASET_TYPE,
    required_columns=CORE_COLUMNS,
    column_types={
        "dataset_type": "string",
        "batch_id": "string",
        "input_index": "integer",
        "source_url": "url",
        "captured_at": "datetime",
        "crawl_status": "string",
        "error_code": "string",
        "external_id": "string",
        "label": "string",
    },
    unique_by=("batch_id", "input_index"),
    normalized_filename_template="{dataset_type}_{batch_id}_normalized.csv",
    failed_filename_template="{dataset_type}_{batch_id}_failed.csv",
)


class CoreExampleProcessor:
    """Normalize labels while preserving failed crawl rows for reconciliation."""

    dataset_type = CORE_DATASET_TYPE
    contract = CORE_CONTRACT

    def validate(self, frame: pd.DataFrame) -> Sequence[ValidationIssue]:
        """Report a row-level issue when the synthetic identifier is blank."""

        return tuple(
            ValidationIssue(
                code="external_id_missing",
                message="external_id must not be blank",
                row_index=index,
                column="external_id",
            )
            for index, value in frame["external_id"].items()
            if not str(value).strip()
        )

    def normalize(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Trim labels without changing source-column order."""

        normalized = frame.copy()
        normalized["label"] = normalized["label"].str.strip()
        return normalized

    def split_results(
        self,
        frame: pd.DataFrame,
        issues: Sequence[ValidationIssue],
    ) -> ProcessorResult[pd.DataFrame]:
        """Split rows using validation issue indexes and stable reconciliation totals."""

        failed_indexes = {issue.row_index for issue in issues if issue.row_index is not None}
        failed_mask = frame.index.isin(failed_indexes)
        normalized_rows = frame.loc[~failed_mask].copy()
        failed_rows = frame.loc[failed_mask].copy()
        return ProcessorResult(
            normalized_rows=normalized_rows,
            failed_rows=failed_rows,
            summary={
                "input_rows": len(frame),
                "normalized_rows": len(normalized_rows),
                "failed_rows": len(failed_rows),
            },
        )


@pytest.fixture
def raw_frame() -> pd.DataFrame:
    """Return a valid generic RPA frame with values that must remain strings."""

    return pd.DataFrame(
        [
            {
                "dataset_type": CORE_DATASET_TYPE,
                "batch_id": "batch_001",
                "input_index": "0001",
                "source_url": "https://example.invalid/1",
                "captured_at": "2026-07-18T00:00:00Z",
                "crawl_status": "success",
                "error_code": "",
                "external_id": "000123",
                "label": "  First  ",
            },
            {
                "dataset_type": CORE_DATASET_TYPE,
                "batch_id": "batch_001",
                "input_index": "0002",
                "source_url": "https://example.invalid/2",
                "captured_at": "2026-07-18T00:00:01Z",
                "crawl_status": "partial",
                "error_code": "field_missing",
                "external_id": "",
                "label": "Second",
            },
        ],
        columns=CORE_COLUMNS,
        dtype="string",
    )


def test_read_source_file_preserves_csv_strings_and_custom_delimiter(
    tmp_path: Path,
) -> None:
    """CSV loading preserves leading zeros, blanks, and explicit separators."""

    source = tmp_path / "input.csv"
    source.write_text("input_index;external_id;label\n0001;000123;\n", encoding="utf-8")

    frame = read_source_file(source, delimiter=";")

    assert frame.to_dict(orient="records") == [
        {"input_index": "0001", "external_id": "000123", "label": ""}
    ]
    assert all(str(dtype) == "string" for dtype in frame.dtypes)


def test_read_source_file_reads_selected_xlsx_sheet_as_strings(tmp_path: Path) -> None:
    """XLSX loading selects one sheet and protects identifier formatting."""

    source = tmp_path / "input.xlsx"
    with pd.ExcelWriter(source, engine="openpyxl") as writer:
        pd.DataFrame({"ignored": ["value"]}).to_excel(
            writer,
            sheet_name="Ignored",
            index=False,
        )
        pd.DataFrame({"input_index": ["0001"], "external_id": ["000123"]}).to_excel(
            writer,
            sheet_name="Products",
            index=False,
        )

    frame = read_source_file(source, sheet_name="Products")

    assert frame.to_dict(orient="records") == [
        {"input_index": "0001", "external_id": "000123"}
    ]


def test_read_source_file_rejects_empty_and_invalid_encoding(tmp_path: Path) -> None:
    """Unreadable raw files surface stable source errors instead of empty tables."""

    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    invalid = tmp_path / "invalid.csv"
    invalid.write_bytes(b"label\n\xff\n")

    with pytest.raises(SourceFileError, match="empty"):
        read_source_file(empty)
    with pytest.raises(SourceFileError, match="encoding"):
        read_source_file(invalid, encoding="utf-8")


def test_validate_common_columns_reports_schema_rows_and_statuses(
    raw_frame: pd.DataFrame,
) -> None:
    """Common validation reports missing columns, empty data, and bad statuses."""

    missing = validate_common_columns(raw_frame.drop(columns=["batch_id"]))
    empty = validate_common_columns(raw_frame.iloc[0:0])
    invalid = raw_frame.copy()
    invalid.loc[0, "crawl_status"] = "unknown"
    invalid_status = validate_common_columns(invalid)

    assert [issue.code for issue in missing] == ["missing_common_columns"]
    assert [issue.code for issue in empty] == ["empty_file"]
    assert [issue.code for issue in invalid_status] == ["invalid_crawl_status"]
    assert invalid_status[0].row_index == 0


def test_write_canonical_csv_is_ordered_atomic_and_repeatable(
    tmp_path: Path,
    raw_frame: pd.DataFrame,
) -> None:
    """Canonical writes use fixed columns and produce identical UTF-8 bytes."""

    destination = tmp_path / "nested" / "canonical.csv"
    reversed_frame = raw_frame.loc[:, list(reversed(CORE_COLUMNS))]

    write_canonical_csv(reversed_frame, destination, columns=CORE_COLUMNS)
    first_bytes = destination.read_bytes()
    write_canonical_csv(reversed_frame, destination, columns=CORE_COLUMNS)

    assert destination.read_bytes() == first_bytes
    assert first_bytes.decode("utf-8").splitlines()[0] == ",".join(CORE_COLUMNS)
    assert not list(destination.parent.glob("*.tmp"))


def test_process_dataset_routes_processor_and_writes_both_outputs(
    tmp_path: Path,
    raw_frame: pd.DataFrame,
) -> None:
    """Generic orchestration resolves a processor and reconciles output rows."""

    register_processor(CORE_DATASET_TYPE, CoreExampleProcessor)
    source = tmp_path / "raw.csv"
    raw_frame.to_csv(source, index=False)
    output_root = tmp_path / "output"

    result = process_dataset(CORE_DATASET_TYPE, source, output_root)

    normalized_path = output_root / "core_example_batch_001_normalized.csv"
    failed_path = output_root / "core_example_batch_001_failed.csv"
    normalized_bytes = normalized_path.read_bytes()
    failed_bytes = failed_path.read_bytes()
    assert result.summary == {
        "input_rows": 2,
        "normalized_rows": 1,
        "failed_rows": 1,
    }
    assert normalized_path.exists()
    assert failed_path.exists()
    assert pd.read_csv(normalized_path, dtype="string", keep_default_na=False)[
        "label"
    ].tolist() == ["First"]
    assert pd.read_csv(failed_path, dtype="string", keep_default_na=False)[
        "input_index"
    ].tolist() == ["0002"]

    process_dataset(CORE_DATASET_TYPE, source, output_root)
    assert normalized_path.read_bytes() == normalized_bytes
    assert failed_path.read_bytes() == failed_bytes


def test_process_dataset_rejects_mismatched_dataset_type(
    tmp_path: Path,
    raw_frame: pd.DataFrame,
) -> None:
    """CLI-selected routing cannot process rows that declare another dataset."""

    mismatch_type = "core_mismatch"

    class MismatchProcessor(CoreExampleProcessor):
        dataset_type = mismatch_type
        contract = DatasetContract(
            dataset_type=mismatch_type,
            required_columns=CORE_COLUMNS,
            column_types=CORE_CONTRACT.column_types,
            unique_by=CORE_CONTRACT.unique_by,
            normalized_filename_template=CORE_CONTRACT.normalized_filename_template,
            failed_filename_template=CORE_CONTRACT.failed_filename_template,
        )

    register_processor(mismatch_type, MismatchProcessor)
    source = tmp_path / "mismatch.csv"
    raw_frame.to_csv(source, index=False)

    with pytest.raises(DatasetValidationError, match="dataset_type"):
        process_dataset(mismatch_type, source, tmp_path / "output")


def test_process_dataset_rejects_inconsistent_processor_summary(
    tmp_path: Path,
    raw_frame: pd.DataFrame,
) -> None:
    """Processor summary counts must match the actual split DataFrames."""

    summary_type = "core_bad_summary"

    class BadSummaryProcessor(CoreExampleProcessor):
        dataset_type = "core_bad_summary"
        contract = DatasetContract(
            dataset_type=summary_type,
            required_columns=CORE_COLUMNS,
            column_types=CORE_CONTRACT.column_types,
            unique_by=CORE_CONTRACT.unique_by,
            normalized_filename_template=CORE_CONTRACT.normalized_filename_template,
            failed_filename_template=CORE_CONTRACT.failed_filename_template,
        )

        def split_results(
            self,
            frame: pd.DataFrame,
            issues: Sequence[ValidationIssue],
        ) -> ProcessorResult[pd.DataFrame]:
            result = super().split_results(frame, issues)
            return ProcessorResult(
                normalized_rows=result.normalized_rows,
                failed_rows=result.failed_rows,
                summary={"input_rows": 99, "normalized_rows": 99, "failed_rows": 0},
            )

    register_processor(summary_type, BadSummaryProcessor)
    source = tmp_path / "bad-summary.csv"
    selected = raw_frame.copy()
    selected["dataset_type"] = summary_type
    selected.to_csv(source, index=False)

    with pytest.raises(DatasetValidationError, match="summary"):
        process_dataset(summary_type, source, tmp_path / "output")


def test_cli_returns_nonzero_for_unknown_dataset_type(
    tmp_path: Path,
    raw_frame: pd.DataFrame,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown dataset routing is a controlled CLI failure with no traceback."""

    source = tmp_path / "raw.csv"
    raw_frame.to_csv(source, index=False)

    exit_code = main(
        [
            "--dataset-type",
            "not_registered",
            "--input-path",
            str(source),
            "--output-root",
            str(tmp_path / "output"),
        ]
    )

    assert exit_code != 0
    assert "not_registered" in capsys.readouterr().err
