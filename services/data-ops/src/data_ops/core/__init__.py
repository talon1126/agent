"""Expose generic contracts, I/O helpers, and validation primitives."""

from data_ops.core.contracts import DatasetContract, DatasetProcessor, ProcessorResult
from data_ops.core.csv_io import SourceFileError, read_source_file, write_canonical_csv
from data_ops.core.validation import (
    DatasetValidationError,
    ValidationIssue,
    validate_common_columns,
)

__all__ = [
    "DatasetContract",
    "DatasetProcessor",
    "DatasetValidationError",
    "ProcessorResult",
    "SourceFileError",
    "ValidationIssue",
    "read_source_file",
    "validate_common_columns",
    "write_canonical_csv",
]
