"""Define stable file contracts between RPA exports and dataset processors.

This module is the dependency-free boundary shared by Yingdao workflow assets
and the future pandas-based data-ops runtime. It owns column semantics,
processor handoff types, and runtime directory names. It intentionally does not
read files, import pandas, create directories, or interact with a database.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from data_ops.core.validation import ValidationIssue


ColumnType = Literal["string", "integer", "decimal", "datetime", "url"]
FrameT = TypeVar("FrameT")

COMMON_WEB_EXPORT_COLUMNS = (
    "dataset_type",
    "batch_id",
    "input_index",
    "source_url",
    "captured_at",
    "crawl_status",
    "error_code",
)
COMMON_WEB_EXPORT_COLUMN_TYPES: Mapping[str, ColumnType] = MappingProxyType(
    {
        "dataset_type": "string",
        "batch_id": "string",
        "input_index": "integer",
        "source_url": "url",
        "captured_at": "datetime",
        "crawl_status": "string",
        "error_code": "string",
    }
)
COMMON_CRAWL_STATUSES = ("success", "partial", "failed")
COMMON_ERROR_CODES = (
    "invalid_input",
    "navigation_failed",
    "page_timeout",
    "field_missing",
    "manual_verification_required",
    "access_restricted",
    "adapter_error",
)

JD_PRODUCT_INPUT_COLUMNS = ("input_index", "product_url")
JD_PRODUCT_SITE_COLUMNS = (
    "jd_sku_id",
    "title",
    "display_price",
    "shop_name",
    "primary_image_url",
    "capture_region",
)

_DATASET_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SUPPORTED_COLUMN_TYPES = frozenset(
    {"string", "integer", "decimal", "datetime", "url"}
)


def _validate_column_names(columns: tuple[str, ...], *, label: str) -> None:
    """Validate that one ordered column group is non-empty and duplicate-free.

    Args:
        columns: Ordered column names declared by a contract.
        label: Human-readable group name included in validation errors.

    Raises:
        ValueError: If the group is empty, contains a blank name, or repeats a
            column name.
    """

    if not columns:
        raise ValueError(f"{label} must not be empty")
    if any(not column.strip() for column in columns):
        raise ValueError(f"{label} must not contain blank column names")
    if len(columns) != len(set(columns)):
        raise ValueError(f"{label} must not contain duplicate column names")


def _validate_dataset_type(dataset_type: str) -> None:
    """Validate the stable routing key used in filenames and processor lookup.

    Args:
        dataset_type: Candidate lowercase snake-case routing key.

    Raises:
        ValueError: If the routing key is not lowercase snake case.
    """

    if _DATASET_TYPE_PATTERN.fullmatch(dataset_type) is None:
        raise ValueError("dataset_type must use lowercase snake_case")


@dataclass(frozen=True, slots=True)
class WebPageExportContract:
    """Describe one site adapter's input and raw CSV extension boundary.

    The seven common output columns and three crawl statuses are fixed across
    every website. A site adapter may add output columns and error codes, but it
    cannot replace or reorder the common columns.
    """

    dataset_type: str
    input_columns: tuple[str, ...]
    site_output_columns: tuple[str, ...]
    error_codes: tuple[str, ...] = COMMON_ERROR_CODES
    common_output_columns: tuple[str, ...] = field(
        default=COMMON_WEB_EXPORT_COLUMNS,
        init=False,
    )
    crawl_statuses: tuple[str, ...] = field(
        default=COMMON_CRAWL_STATUSES,
        init=False,
    )

    def __post_init__(self) -> None:
        """Reject adapters that would change the generic CSV semantics.

        Raises:
            ValueError: If the dataset key, column groups, or error-code list is
                invalid or if a site column shadows a common column.
        """

        _validate_dataset_type(self.dataset_type)
        _validate_column_names(self.input_columns, label="input_columns")
        _validate_column_names(
            self.site_output_columns,
            label="site_output_columns",
        )
        overlap = set(self.common_output_columns) & set(self.site_output_columns)
        if overlap:
            raise ValueError(
                "site_output_columns must not shadow common columns: "
                + ", ".join(sorted(overlap))
            )
        if len(self.error_codes) != len(set(self.error_codes)):
            raise ValueError("error_codes must not contain duplicates")
        if any(not code.strip() for code in self.error_codes):
            raise ValueError("error_codes must not contain blank values")
        if not set(COMMON_ERROR_CODES).issubset(self.error_codes):
            raise ValueError("error_codes must preserve all common error codes")

    @property
    def output_columns(self) -> tuple[str, ...]:
        """Return the canonical raw CSV column order.

        Returns:
            Common columns followed by the site-specific extension columns.
        """

        return self.common_output_columns + self.site_output_columns


@dataclass(frozen=True, slots=True)
class DatasetContract:
    """Describe validation and output rules for one dataset processor.

    Required and optional columns describe CSV schema presence, not whether an
    individual field may be blank for a partial or failed crawl. Row-level
    validity remains the responsibility of the registered dataset processor.
    """

    dataset_type: str
    required_columns: tuple[str, ...]
    column_types: Mapping[str, ColumnType]
    unique_by: tuple[str, ...]
    normalized_filename_template: str
    failed_filename_template: str
    optional_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Freeze and validate the processor-facing dataset definition.

        Raises:
            ValueError: If columns overlap, type declarations are incomplete,
                uniqueness columns are unavailable, or output templates are not
                CSV filenames parameterized by dataset and batch identifiers.
        """

        _validate_dataset_type(self.dataset_type)
        _validate_column_names(self.required_columns, label="required_columns")
        if self.optional_columns:
            _validate_column_names(self.optional_columns, label="optional_columns")
        overlap = set(self.required_columns) & set(self.optional_columns)
        if overlap:
            raise ValueError(
                "required_columns and optional_columns must not overlap: "
                + ", ".join(sorted(overlap))
            )

        declared_columns = set(self.all_columns)
        frozen_types = dict(self.column_types)
        if set(frozen_types) != declared_columns:
            raise ValueError("column_types must define every declared column exactly once")
        unsupported_types = set(frozen_types.values()) - _SUPPORTED_COLUMN_TYPES
        if unsupported_types:
            raise ValueError(
                "unsupported column types: " + ", ".join(sorted(unsupported_types))
            )
        if not self.unique_by or not set(self.unique_by).issubset(declared_columns):
            raise ValueError("unique_by must reference declared columns")
        if len(self.unique_by) != len(set(self.unique_by)):
            raise ValueError("unique_by must not contain duplicates")

        for label, template in (
            ("normalized_filename_template", self.normalized_filename_template),
            ("failed_filename_template", self.failed_filename_template),
        ):
            if not template.endswith(".csv"):
                raise ValueError(f"{label} must produce a .csv file")
            if "{dataset_type}" not in template or "{batch_id}" not in template:
                raise ValueError(
                    f"{label} must contain {{dataset_type}} and {{batch_id}}"
                )

        object.__setattr__(
            self,
            "column_types",
            MappingProxyType(frozen_types),
        )

    @property
    def all_columns(self) -> tuple[str, ...]:
        """Return the complete ordered processor input schema.

        Returns:
            Required columns followed by optional columns.
        """

        return self.required_columns + self.optional_columns


@dataclass(frozen=True, slots=True)
class ProcessorResult[FrameT]:
    """Carry normalized rows, failed rows, and reconciliation statistics."""

    normalized_rows: FrameT
    failed_rows: FrameT
    summary: Mapping[str, int]

    def __post_init__(self) -> None:
        """Freeze the summary so processors cannot mutate reported totals later."""

        object.__setattr__(self, "summary", MappingProxyType(dict(self.summary)))


@runtime_checkable
class ProcessorContract(Protocol[FrameT]):
    """Define the high-level callable boundary for a dataset processor."""

    def process(
        self,
        frame: FrameT,
        contract: DatasetContract,
    ) -> ProcessorResult[FrameT]:
        """Process one in-memory table according to its registered contract.

        Args:
            frame: Input table, normally a pandas DataFrame in the J4 runtime.
            contract: Dataset definition selected by dataset_type.

        Returns:
            Normalized rows, failed rows, and integer reconciliation metrics.
        """

        ...


@runtime_checkable
class DatasetProcessor(Protocol[FrameT]):
    """Define the staged processing interface used by the generic J4 runtime.

    Implementations own one dataset contract and its row-level business rules.
    The generic core calls the stages in order and never imports concrete
    processor modules or site-specific field constants.
    """

    dataset_type: str
    contract: DatasetContract

    def validate(self, frame: FrameT) -> Sequence[ValidationIssue]:
        """Return row-level issues without mutating the source frame."""

        ...

    def normalize(self, frame: FrameT) -> FrameT:
        """Return a canonical table while preserving reconciliation columns."""

        ...

    def split_results(
        self,
        frame: FrameT,
        issues: Sequence[ValidationIssue],
    ) -> ProcessorResult[FrameT]:
        """Split normalized and failed rows with integer summary totals."""

        ...


@dataclass(frozen=True, slots=True)
class RuntimeDirectoryContract:
    """Expose the four file lifecycle locations without creating them."""

    root: Path = Path("var/rpa")

    @property
    def inbox(self) -> Path:
        """Return the immutable raw-file handoff location."""

        return self.root / "inbox"

    @property
    def normalized(self) -> Path:
        """Return the canonical successful-output location."""

        return self.root / "normalized"

    @property
    def archive(self) -> Path:
        """Return the completed-batch archive location."""

        return self.root / "archive"

    @property
    def failed(self) -> Path:
        """Return the failed-batch isolation and replay location."""

        return self.root / "failed"

    def as_mapping(self) -> dict[str, Path]:
        """Return lifecycle names mapped to their repository-relative paths.

        Returns:
            A new mapping suitable for CLI and manifest consumers.
        """

        return {
            "inbox": self.inbox,
            "normalized": self.normalized,
            "archive": self.archive,
            "failed": self.failed,
        }


JD_PRODUCT_WEB_EXPORT_CONTRACT = WebPageExportContract(
    dataset_type="jd_product",
    input_columns=JD_PRODUCT_INPUT_COLUMNS,
    site_output_columns=JD_PRODUCT_SITE_COLUMNS,
)

JD_PRODUCT_DATASET_CONTRACT = DatasetContract(
    dataset_type="jd_product",
    required_columns=JD_PRODUCT_WEB_EXPORT_CONTRACT.output_columns,
    optional_columns=(),
    column_types={
        **COMMON_WEB_EXPORT_COLUMN_TYPES,
        "jd_sku_id": "string",
        "title": "string",
        "display_price": "string",
        "shop_name": "string",
        "primary_image_url": "url",
        "capture_region": "string",
    },
    unique_by=("batch_id", "input_index"),
    normalized_filename_template="{dataset_type}_{batch_id}_normalized.csv",
    failed_filename_template="{dataset_type}_{batch_id}_failed.csv",
)

DEFAULT_RUNTIME_DIRECTORIES = RuntimeDirectoryContract()


__all__ = [
    "COMMON_CRAWL_STATUSES",
    "COMMON_ERROR_CODES",
    "COMMON_WEB_EXPORT_COLUMNS",
    "DatasetProcessor",
    "DatasetContract",
    "DEFAULT_RUNTIME_DIRECTORIES",
    "JD_PRODUCT_DATASET_CONTRACT",
    "JD_PRODUCT_INPUT_COLUMNS",
    "JD_PRODUCT_SITE_COLUMNS",
    "JD_PRODUCT_WEB_EXPORT_CONTRACT",
    "ProcessorContract",
    "ProcessorResult",
    "RuntimeDirectoryContract",
    "WebPageExportContract",
]
