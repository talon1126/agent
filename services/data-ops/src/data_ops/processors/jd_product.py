"""Normalize Yingdao JD product exports without touching business storage.

This concrete processor owns JD field semantics, display-price parsing, and
duplicate classification. The generic data-ops core supplies file I/O and
orchestration; this module neither imports database clients nor maps rows to
the TalonMart items model.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import pandas as pd

from data_ops.core.contracts import (
    JD_PRODUCT_DATASET_CONTRACT,
    ProcessorResult,
)
from data_ops.core.validation import (
    ValidationIssue,
    validate_common_columns,
    validate_dataset_selection,
    validate_single_batch,
)
from data_ops.processors.registry import register_processor

_JD_SKU_PATTERN = re.compile(r"^[0-9]+$")
_PRICE_PATTERN = re.compile(
    r"^(?:(?:CNY|RMB|CN¥|人民币|¥|￥)\s*)?"
    r"(?P<amount>(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,2})?)"
    r"\s*(?:(?:CNY|RMB|人民币|元))?$",
    re.IGNORECASE,
)
_DUPLICATE_CODES = frozenset(
    {
        "duplicate_input_index",
        "duplicate_source_url",
        "duplicate_jd_sku_id",
    }
)
_HARD_FAILURE_CODES = _DUPLICATE_CODES | frozenset(
    {
        "invalid_input_index",
        "invalid_source_url",
        "invalid_jd_sku_id",
        "missing_jd_columns",
    }
)
_ISSUE_PRIORITY = {
    "missing_jd_columns": 0,
    "duplicate_input_index": 1,
    "duplicate_source_url": 2,
    "duplicate_jd_sku_id": 3,
    "invalid_input_index": 4,
    "invalid_source_url": 5,
    "invalid_captured_at": 6,
    "invalid_jd_sku_id": 7,
    "invalid_primary_image_url": 8,
    "field_missing": 9,
    "price_parse_failed": 10,
    "missing_error_code": 11,
}


def _clean_text(value: object) -> str:
    """Return one trimmed CSV value while treating pandas nulls as blank."""

    if pd.isna(value):
        return ""
    return str(value).strip()


def _collapse_space(value: object) -> str:
    """Trim human-readable text and collapse repeated internal whitespace."""

    return re.sub(r"\s+", " ", _clean_text(value))


def _is_http_url(value: object) -> bool:
    """Return whether a value is an absolute HTTP or HTTPS URL."""

    try:
        parsed = urlparse(_clean_text(value))
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _normalize_timestamp(value: object) -> str:
    """Convert one parseable timestamp to an ISO-8601 UTC value."""

    text = _clean_text(value)
    if not text:
        return ""
    try:
        timestamp = pd.to_datetime(text, utc=True, errors="raise")
    except (TypeError, ValueError):
        return ""
    if pd.isna(timestamp):
        return ""
    return timestamp.isoformat().replace("+00:00", "Z")


def _parse_display_price(value: object) -> str:
    """Return a fixed two-decimal amount or blank for unsupported price text.

    The parser accepts common RMB markers and grouping commas only when the
    whole value represents one amount. Promotional ranges or explanatory text
    stay unparsed so the row can be routed to the failed output instead of
    receiving a fabricated zero.
    """

    text = _clean_text(value)
    match = _PRICE_PATTERN.fullmatch(text)
    if match is None:
        return ""
    try:
        amount = Decimal(match.group("amount").replace(",", ""))
        return format(amount.quantize(Decimal("0.01")), "f")
    except InvalidOperation:
        return ""


def identify_jd_duplicates(frame: pd.DataFrame) -> tuple[ValidationIssue, ...]:
    """Mark later rows that repeat an input index, source URL, or nonblank SKU.

    Args:
        frame: Raw or normalized JD product rows.

    Returns:
        At most one duplicate issue per later row. Input-index collisions take
        precedence over URL collisions, which take precedence over SKU
        collisions, so the single failed-output error_code remains stable.
    """

    issues: list[ValidationIssue] = []
    claimed_indexes: set[object] = set()
    rules = (
        ("input_index", "duplicate_input_index"),
        ("source_url", "duplicate_source_url"),
        ("jd_sku_id", "duplicate_jd_sku_id"),
    )
    for column, code in rules:
        if column not in frame:
            continue
        values = frame[column].map(_clean_text)
        duplicate_mask = values.ne("") & values.duplicated(keep="first")
        for index in frame.index[duplicate_mask]:
            if index in claimed_indexes:
                continue
            claimed_indexes.add(index)
            issues.append(
                ValidationIssue(
                    code=code,
                    message=f"{column} repeats an earlier JD product row",
                    row_index=index,
                    column=column,
                )
            )
    issues.sort(key=lambda issue: frame.index.get_loc(issue.row_index))
    return tuple(issues)


class JdProductProcessor:
    """Process one jd_product batch into normalized and failed CSV rows.

    The processor expects the J3 Yingdao raw schema. It preserves every input
    row in exactly one output, reports duplicate rows as a failed-row subset,
    and adds only the contract-declared display_price_amount output field.
    """

    dataset_type = "jd_product"
    contract = JD_PRODUCT_DATASET_CONTRACT

    def validate(self, frame: pd.DataFrame) -> Sequence[ValidationIssue]:
        """Validate JD schema, trace fields, successful captures, and duplicates.

        Args:
            frame: Raw JD product export loaded with string-preserving I/O.

        Returns:
            Ordered global and row-level issues. Partial and failed capture rows
            may leave JD product fields blank, but must retain an error code.
        """

        issues = [
            *validate_common_columns(frame),
            *validate_dataset_selection(frame, self.dataset_type),
            *validate_single_batch(frame),
        ]
        missing = tuple(
            column for column in self.contract.required_columns if column not in frame
        )
        if missing:
            issues.append(
                ValidationIssue(
                    code="missing_jd_columns",
                    message="missing required JD columns: " + ", ".join(missing),
                )
            )
            return tuple(issues)

        for index, row in frame.iterrows():
            input_index = _clean_text(row["input_index"])
            source_url = _clean_text(row["source_url"])
            captured_at = _clean_text(row["captured_at"])
            status = _clean_text(row["crawl_status"]).lower()
            error_code = _clean_text(row["error_code"])
            sku = _clean_text(row["jd_sku_id"])
            price = _clean_text(row["display_price"])
            image_url = _clean_text(row["primary_image_url"])

            if not input_index.isdigit():
                issues.append(
                    ValidationIssue(
                        code="invalid_input_index",
                        message="input_index must contain decimal digits",
                        row_index=index,
                        column="input_index",
                    )
                )
            if not _is_http_url(source_url):
                issues.append(
                    ValidationIssue(
                        code="invalid_source_url",
                        message="source_url must be an absolute HTTP URL",
                        row_index=index,
                        column="source_url",
                    )
                )
            if not captured_at or not _normalize_timestamp(captured_at):
                issues.append(
                    ValidationIssue(
                        code="invalid_captured_at",
                        message="captured_at must be a parseable timestamp",
                        row_index=index,
                        column="captured_at",
                    )
                )

            if status == "success":
                required_values = {
                    "jd_sku_id": sku,
                    "title": _clean_text(row["title"]),
                    "display_price": price,
                    "shop_name": _clean_text(row["shop_name"]),
                    "primary_image_url": image_url,
                }
                missing_fields = tuple(
                    column for column, value in required_values.items() if not value
                )
                if missing_fields:
                    issues.append(
                        ValidationIssue(
                            code="field_missing",
                            message="successful JD row has blank fields: "
                            + ", ".join(missing_fields),
                            row_index=index,
                            column=missing_fields[0],
                        )
                    )
                if sku and _JD_SKU_PATTERN.fullmatch(sku) is None:
                    issues.append(
                        ValidationIssue(
                            code="invalid_jd_sku_id",
                            message="jd_sku_id must contain decimal digits",
                            row_index=index,
                            column="jd_sku_id",
                        )
                    )
                if price and not _parse_display_price(price):
                    issues.append(
                        ValidationIssue(
                            code="price_parse_failed",
                            message="display_price does not represent one RMB amount",
                            row_index=index,
                            column="display_price",
                        )
                    )
                if image_url and not _is_http_url(image_url):
                    issues.append(
                        ValidationIssue(
                            code="invalid_primary_image_url",
                            message="primary_image_url must be an absolute HTTP URL",
                            row_index=index,
                            column="primary_image_url",
                        )
                    )
            elif not error_code:
                issues.append(
                    ValidationIssue(
                        code="missing_error_code",
                        message="partial and failed rows must explain their status",
                        row_index=index,
                        column="error_code",
                    )
                )

        issues.extend(identify_jd_duplicates(frame))
        return tuple(issues)

    def normalize(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Return canonical JD values and the parsed display-price amount.

        Args:
            frame: Raw JD product rows. Missing JD schema columns are added as
                blanks so validation failures can still reconcile into output.

        Returns:
            A new DataFrame in the contract's canonical output-column order.
            Unparseable prices and timestamps remain blank in their normalized
            derivatives and are classified by split_results.
        """

        normalized = frame.copy()
        for column in self.contract.required_columns:
            if column not in normalized:
                normalized[column] = ""

        for column in self.contract.required_columns:
            normalized[column] = normalized[column].map(_clean_text).astype("string")
        normalized["dataset_type"] = normalized["dataset_type"].str.lower()
        normalized["crawl_status"] = normalized["crawl_status"].str.lower()
        normalized["input_index"] = normalized["input_index"].map(_clean_text)
        normalized["source_url"] = normalized["source_url"].map(_clean_text)
        normalized["captured_at"] = normalized["captured_at"].map(_normalize_timestamp)
        normalized["jd_sku_id"] = normalized["jd_sku_id"].map(
            lambda value: re.sub(r"\s+", "", _clean_text(value))
        )
        for column in ("title", "shop_name", "capture_region"):
            normalized[column] = normalized[column].map(_collapse_space)
        normalized["display_price"] = normalized["display_price"].map(_clean_text)
        normalized["primary_image_url"] = normalized["primary_image_url"].map(
            _clean_text
        )
        normalized["display_price_amount"] = normalized["display_price"].map(
            _parse_display_price
        )
        return normalized.loc[:, list(self.contract.all_columns)].astype("string")

    def split_results(
        self,
        frame: pd.DataFrame,
        issues: Sequence[ValidationIssue],
    ) -> ProcessorResult[pd.DataFrame]:
        """Split successful rows from crawl, validation, and duplicate failures.

        Args:
            frame: Canonically normalized JD rows.
            issues: Validation issues produced for the corresponding raw frame.

        Returns:
            Normalized successes, failed rows, and reconciliation totals.
            duplicate_rows is a subset of failed_rows rather than a third file.
        """

        global_issues = sorted(
            (issue for issue in issues if issue.row_index is None),
            key=lambda issue: _ISSUE_PRIORITY.get(issue.code, 100),
        )
        row_issues: dict[object, list[ValidationIssue]] = {}
        for issue in issues:
            if issue.row_index is not None:
                row_issues.setdefault(issue.row_index, []).append(issue)
        for indexed_issues in row_issues.values():
            indexed_issues.sort(
                key=lambda issue: _ISSUE_PRIORITY.get(issue.code, 100)
            )

        failed_indexes: list[object] = []
        duplicate_rows = 0
        classified = frame.copy()
        for index, row in classified.iterrows():
            issue = global_issues[0] if global_issues else None
            if index in row_issues:
                issue = row_issues[index][0]
            status = _clean_text(row["crawl_status"]).lower()
            existing_error = _clean_text(row["error_code"])
            should_fail = status != "success" or issue is not None
            if not should_fail:
                classified.at[index, "error_code"] = ""
                continue

            failed_indexes.append(index)
            if issue is not None and issue.code in _DUPLICATE_CODES:
                duplicate_rows += 1
                classified.at[index, "crawl_status"] = "failed"
                classified.at[index, "error_code"] = issue.code
            elif status == "success" and issue is not None:
                classified.at[index, "crawl_status"] = (
                    "failed" if issue.code in _HARD_FAILURE_CODES else "partial"
                )
                classified.at[index, "error_code"] = issue.code
            elif global_issues:
                classified.at[index, "crawl_status"] = "failed"
                classified.at[index, "error_code"] = global_issues[0].code
            elif not existing_error:
                classified.at[index, "error_code"] = (
                    issue.code
                    if issue is not None
                    else ("field_missing" if status == "partial" else "adapter_error")
                )

        failed_mask = classified.index.isin(failed_indexes)
        normalized_rows = classified.loc[~failed_mask].copy()
        failed_rows = classified.loc[failed_mask].copy()
        return ProcessorResult(
            normalized_rows=normalized_rows,
            failed_rows=failed_rows,
            summary={
                "input_rows": len(classified),
                "normalized_rows": len(normalized_rows),
                "failed_rows": len(failed_rows),
                "duplicate_rows": duplicate_rows,
            },
        )


def register_jd_product_processor() -> None:
    """Register jd_product through the generic factory-based registry.

    Raises:
        DuplicateProcessorError: If jd_product was already registered in the
            current process.

    Side Effects:
        Adds the JD factory to the in-memory processor registry.
    """

    register_processor(JdProductProcessor.dataset_type, JdProductProcessor)


__all__ = [
    "JdProductProcessor",
    "identify_jd_duplicates",
    "register_jd_product_processor",
]
