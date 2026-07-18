"""Expose generic contracts, I/O helpers, and validation primitives."""

from data_ops.core.batch_manifest import BatchManifest, replay_batch
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
    "BatchManifest",
    "ProcessorResult",
    "SourceFileError",
    "ValidationIssue",
    "read_source_file",
    "replay_batch",
    "validate_common_columns",
    "write_canonical_csv",
]
