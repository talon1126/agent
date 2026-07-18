"""Validate generic RPA columns before dataset-specific processing begins.

The module owns only shared file invariants: schema presence, non-empty input,
supported crawl statuses, selected dataset routing, and one batch per source
file. Concrete processors remain responsible for their own fields and row
semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from data_ops.core.contracts import COMMON_CRAWL_STATUSES, COMMON_WEB_EXPORT_COLUMNS

_SAFE_BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Describe one stable validation failure without embedding source data."""

    code: str
    message: str
    row_index: Any | None = None
    column: str | None = None


class DatasetValidationError(ValueError):
    """Aggregate generic validation issues at the orchestration boundary."""

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        """Build a concise error while retaining structured issues.

        Args:
            issues: One or more validation issues that stopped processing.

        Raises:
            ValueError: If an empty issue collection is supplied.
        """

        if not issues:
            raise ValueError("DatasetValidationError requires at least one issue")
        self.issues = issues
        super().__init__("; ".join(f"{issue.code}: {issue.message}" for issue in issues))


def validate_common_columns(frame: pd.DataFrame) -> tuple[ValidationIssue, ...]:
    """Validate schema presence, row count, and common crawl status values.

    Args:
        frame: Raw table loaded from CSV or XLSX.

    Returns:
        Ordered validation issues. Missing columns stop checks that depend on
        those columns, while independent empty-file validation still runs.
    """

    issues: list[ValidationIssue] = []
    missing = tuple(column for column in COMMON_WEB_EXPORT_COLUMNS if column not in frame)
    if missing:
        issues.append(
            ValidationIssue(
                code="missing_common_columns",
                message="missing required columns: " + ", ".join(missing),
            )
        )
    if frame.empty:
        issues.append(
            ValidationIssue(
                code="empty_file",
                message="source file contains no data rows",
            )
        )
    if "crawl_status" in frame:
        supported = set(COMMON_CRAWL_STATUSES)
        for index, value in frame["crawl_status"].items():
            status = str(value).strip()
            if status not in supported:
                issues.append(
                    ValidationIssue(
                        code="invalid_crawl_status",
                        message=f"unsupported crawl_status: {status!r}",
                        row_index=index,
                        column="crawl_status",
                    )
                )
    return tuple(issues)


def validate_dataset_selection(
    frame: pd.DataFrame,
    dataset_type: str,
) -> tuple[ValidationIssue, ...]:
    """Ensure every input row matches the explicitly selected processor.

    Args:
        frame: Raw table containing the common dataset_type column.
        dataset_type: CLI-selected routing key.

    Returns:
        One issue per mismatched row, or an empty tuple when all rows match.
    """

    if "dataset_type" not in frame:
        return ()
    return tuple(
        ValidationIssue(
            code="dataset_type_mismatch",
            message=f"expected dataset_type {dataset_type!r}, got {str(value).strip()!r}",
            row_index=index,
            column="dataset_type",
        )
        for index, value in frame["dataset_type"].items()
        if str(value).strip() != dataset_type
    )


def validate_single_batch(frame: pd.DataFrame) -> tuple[ValidationIssue, ...]:
    """Require exactly one non-blank batch identifier per source file.

    Args:
        frame: Raw table containing the common batch_id column.

    Returns:
        A single issue when the batch is blank or mixed, otherwise no issues.
    """

    if "batch_id" not in frame or frame.empty:
        return ()
    batch_ids = {str(value).strip() for value in frame["batch_id"].tolist()}
    if "" in batch_ids:
        return (
            ValidationIssue(
                code="invalid_batch_id",
                message="batch_id must not be blank",
                column="batch_id",
            ),
        )
    if len(batch_ids) != 1:
        return (
            ValidationIssue(
                code="multiple_batch_ids",
                message="source file must contain exactly one batch_id",
                column="batch_id",
            ),
        )
    batch_id = next(iter(batch_ids))
    if _SAFE_BATCH_ID_PATTERN.fullmatch(batch_id) is None:
        return (
            ValidationIssue(
                code="invalid_batch_id",
                message="batch_id contains unsafe path characters",
                column="batch_id",
            ),
        )
    return ()


__all__ = [
    "DatasetValidationError",
    "ValidationIssue",
    "validate_common_columns",
    "validate_dataset_selection",
    "validate_single_batch",
]
