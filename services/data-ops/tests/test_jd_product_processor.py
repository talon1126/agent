"""Protect the JD product processor's file-only normalization contract.

The suite exercises the first concrete pandas processor against the synthetic
Yingdao export and targeted edge cases. Failures indicate lost traceability,
silent zero-price substitution, duplicate-row leakage, or accidental coupling
between JD rules and the generic data-ops core.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from data_ops.cli import process_batch, process_dataset
from data_ops.core.batch_manifest import (
    BatchManifestError,
    calculate_file_sha256,
    load_batch_manifest,
    replay_batch,
)
from data_ops.core.csv_io import read_source_file
from data_ops.processors.jd_product import (
    JdProductProcessor,
    identify_jd_duplicates,
    register_jd_product_processor,
)
from data_ops.processors.jd_product_contract import JD_PRODUCT_DATASET_CONTRACT
from data_ops.processors.registry import get_processor

REPO_ROOT = Path(__file__).resolve().parents[3]
JD_FIXTURE = REPO_ROOT / "fixtures" / "rpa" / "jd_product_export.csv"
JD_URL_FIXTURE = REPO_ROOT / "fixtures" / "rpa" / "jd_product_urls.csv"


@pytest.fixture(scope="module", autouse=True)
def registered_jd_processor() -> None:
    """Register the concrete processor once for generic orchestration tests."""

    register_jd_product_processor()


def _success_row(**overrides: str) -> dict[str, str]:
    """Build one valid synthetic JD row while allowing focused mutations."""

    row = {
        "dataset_type": "jd_product",
        "batch_id": "jd_test_001",
        "input_index": "1",
        "source_url": "https://item.jd.invalid/100000000001.html",
        "captured_at": "2026-07-18T00:00:00Z",
        "crawl_status": "success",
        "error_code": "",
        "jd_sku_id": "100000000001",
        "title": "Synthetic Product",
        "display_price": "CNY 129.00",
        "shop_name": "Synthetic JD Shop",
        "primary_image_url": "https://img.jd.invalid/100000000001.jpg",
        "capture_region": "",
    }
    row.update(overrides)
    return row


def _frame(*rows: dict[str, str]) -> pd.DataFrame:
    """Return source-shaped rows with stable string values and column order."""

    return pd.DataFrame(
        rows,
        columns=JD_PRODUCT_DATASET_CONTRACT.required_columns,
        dtype="string",
    )


def test_register_jd_product_processor_resolves_concrete_contract() -> None:
    """Registration routes jd_product without adding a JD branch to the core."""

    processor = get_processor("jd_product")

    assert isinstance(processor, JdProductProcessor)
    assert processor.dataset_type == "jd_product"
    assert processor.contract is JD_PRODUCT_DATASET_CONTRACT
    assert processor.contract.optional_columns == ("display_price_amount",)
    assert processor.contract.column_types["display_price_amount"] == "decimal"


def test_process_fixture_writes_traceable_normalized_and_failed_csvs(
    tmp_path: Path,
) -> None:
    """The J3 fixture reconciles one success and three non-success rows."""

    original_bytes = JD_FIXTURE.read_bytes()
    result = process_dataset("jd_product", JD_FIXTURE, tmp_path)

    normalized_path = tmp_path / "jd_product_jd_demo_20260717T000000Z_normalized.csv"
    failed_path = tmp_path / "jd_product_jd_demo_20260717T000000Z_failed.csv"
    normalized = read_source_file(normalized_path, encoding="utf-8")
    failed = read_source_file(failed_path, encoding="utf-8")

    assert result.summary == {
        "input_rows": 4,
        "normalized_rows": 1,
        "failed_rows": 3,
        "duplicate_rows": 0,
    }
    assert tuple(normalized.columns) == JD_PRODUCT_DATASET_CONTRACT.all_columns
    assert tuple(failed.columns) == JD_PRODUCT_DATASET_CONTRACT.all_columns
    assert normalized.loc[0, "source_url"].startswith("https://item.jd.invalid/")
    assert normalized.loc[0, "jd_sku_id"] == "100000000001"
    assert normalized.loc[0, "title"] == "Synthetic Wireless Mouse"
    assert normalized.loc[0, "capture_region"] == ""
    assert normalized.loc[0, "captured_at"] == "2026-07-17T00:00:00Z"
    assert normalized.loc[0, "display_price"] == "CNY 129.00"
    assert normalized.loc[0, "display_price_amount"] == "129.00"
    assert failed["input_index"].tolist() == ["2", "3", "4"]
    assert failed["crawl_status"].tolist() == ["partial", "failed", "failed"]
    assert failed["error_code"].tolist() == [
        "field_missing",
        "navigation_failed",
        "invalid_input",
    ]
    assert set(("input_index", "source_url", "crawl_status", "error_code")).issubset(
        failed.columns
    )
    assert JD_FIXTURE.read_bytes() == original_bytes


def test_jd_product_file_e2e_archives_immutable_source_and_outputs(
    tmp_path: Path,
) -> None:
    """One inbox handoff publishes linked JD outputs, manifest, and source."""

    runtime_root = tmp_path / "runtime"
    inbox = runtime_root / "inbox"
    inbox.mkdir(parents=True)
    source = inbox / "jd_product_handoff.csv"
    shutil.copy2(JD_FIXTURE, source)
    fixture_hash = calculate_file_sha256(JD_FIXTURE)
    input_urls = read_source_file(JD_URL_FIXTURE)
    raw_rows = read_source_file(JD_FIXTURE)

    manifest = process_batch("jd_product", source, runtime_root)

    archive = (
        runtime_root
        / "archive"
        / "jd_product"
        / "jd_demo_20260717T000000Z"
    )
    archived_manifest = load_batch_manifest(archive / "manifest.json")
    normalized_name = (
        "jd_product_jd_demo_20260717T000000Z_normalized.csv"
    )
    failed_name = "jd_product_jd_demo_20260717T000000Z_failed.csv"
    normalized = read_source_file(archive / normalized_name, encoding="utf-8")
    failed = read_source_file(archive / failed_name, encoding="utf-8")

    assert len(input_urls) == len(raw_rows) == manifest.row_counts["input_rows"] == 4
    assert manifest == archived_manifest
    assert manifest.status == "success"
    assert manifest.row_counts == {
        "input_rows": 4,
        "normalized_rows": 1,
        "failed_rows": 3,
        "duplicate_rows": 0,
    }
    assert manifest.output_files == (normalized_name, failed_name)
    assert not source.exists()
    assert calculate_file_sha256(archive / "source.csv") == fixture_hash
    assert calculate_file_sha256(JD_FIXTURE) == fixture_hash
    assert len(normalized) + len(failed) == len(raw_rows)
    assert (runtime_root / "normalized" / normalized_name).exists()
    assert (runtime_root / "normalized" / failed_name).exists()


def test_jd_failed_batch_replays_corrected_copy_without_mutating_archive(
    tmp_path: Path,
) -> None:
    """A corrected local copy replays under the failed batch's contract hash."""

    runtime_root = tmp_path / "runtime"
    inbox = runtime_root / "inbox"
    inbox.mkdir(parents=True)
    broken_source = inbox / "jd_product_broken.csv"
    broken = read_source_file(JD_FIXTURE).drop(columns=["crawl_status"])
    broken.to_csv(broken_source, index=False)

    with pytest.raises(BatchManifestError, match="missing_common_columns"):
        process_batch("jd_product", broken_source, runtime_root)

    failed_batch = (
        runtime_root
        / "failed"
        / "jd_product"
        / "jd_demo_20260717T000000Z"
    )
    manifest_path = failed_batch / "manifest.json"
    archived_source = failed_batch / "source.csv"
    archived_hash = calculate_file_sha256(archived_source)
    corrected_source = tmp_path / "corrected_jd_product.csv"
    shutil.copy2(JD_FIXTURE, corrected_source)
    corrected_hash = calculate_file_sha256(corrected_source)

    result = replay_batch(
        manifest_path,
        output_root=tmp_path / "replayed",
        source_path=corrected_source,
    )

    assert result.summary == {
        "input_rows": 4,
        "normalized_rows": 1,
        "failed_rows": 3,
        "duplicate_rows": 0,
    }
    assert calculate_file_sha256(archived_source) == archived_hash
    assert calculate_file_sha256(corrected_source) == corrected_hash
    assert corrected_source.exists()
    assert (
        tmp_path
        / "replayed"
        / "jd_product_jd_demo_20260717T000000Z_normalized.csv"
    ).exists()
    assert (
        tmp_path
        / "replayed"
        / "jd_product_jd_demo_20260717T000000Z_failed.csv"
    ).exists()


def test_normalize_preserves_price_text_and_parses_amount_without_zero_fallback() -> None:
    """Supported price text becomes decimal output while bad text remains failed."""

    frame = _frame(
        _success_row(
            input_index="1",
            display_price="  ￥1,299.50  ",
            captured_at="2026-07-18T08:00:00+08:00",
            capture_region="  Beijing  ",
        ),
        _success_row(
            input_index="2",
            source_url="https://item.jd.invalid/100000000002.html",
            jd_sku_id="100000000002",
            display_price="CNY 88",
            primary_image_url="https://img.jd.invalid/100000000002.jpg",
        ),
        _success_row(
            input_index="3",
            source_url="https://item.jd.invalid/100000000003.html",
            jd_sku_id="100000000003",
            display_price="会员价待定",
            primary_image_url="https://img.jd.invalid/100000000003.jpg",
        ),
        _success_row(
            input_index="4",
            source_url="https://item.jd.invalid/100000000004.html",
            jd_sku_id="100000000004",
            display_price="CNY " + ("9" * 40),
            primary_image_url="https://img.jd.invalid/100000000004.jpg",
        ),
    )
    processor = JdProductProcessor()

    issues = processor.validate(frame)
    result = processor.split_results(processor.normalize(frame), issues)

    assert result.normalized_rows["display_price"].tolist() == [
        "￥1,299.50",
        "CNY 88",
    ]
    assert result.normalized_rows["display_price_amount"].tolist() == [
        "1299.50",
        "88.00",
    ]
    assert result.normalized_rows["captured_at"].tolist()[0] == "2026-07-18T00:00:00Z"
    assert result.normalized_rows["capture_region"].tolist()[0] == "Beijing"
    assert result.failed_rows["display_price"].tolist() == [
        "会员价待定",
        "CNY " + ("9" * 40),
    ]
    assert result.failed_rows["display_price_amount"].tolist() == ["", ""]
    assert result.failed_rows["crawl_status"].tolist() == ["partial", "partial"]
    assert result.failed_rows["error_code"].tolist() == [
        "price_parse_failed",
        "price_parse_failed",
    ]
    assert "0.00" not in result.failed_rows["display_price_amount"].tolist()


def test_identify_jd_duplicates_marks_later_input_url_and_sku_rows() -> None:
    """Duplicate categories remain explicit and reconcile as failed-row subsets."""

    frame = _frame(
        _success_row(),
        _success_row(
            input_index="1",
            source_url="https://item.jd.invalid/100000000002.html",
            jd_sku_id="100000000002",
            primary_image_url="https://img.jd.invalid/100000000002.jpg",
        ),
        _success_row(
            input_index="3",
            source_url="https://item.jd.invalid/100000000001.html",
            jd_sku_id="100000000003",
            primary_image_url="https://img.jd.invalid/100000000003.jpg",
        ),
        _success_row(
            input_index="4",
            source_url="https://item.jd.invalid/100000000004.html",
            jd_sku_id="100000000001",
            primary_image_url="https://img.jd.invalid/100000000004.jpg",
        ),
    )
    processor = JdProductProcessor()

    duplicate_issues = identify_jd_duplicates(frame)
    issues = processor.validate(frame)
    result = processor.split_results(processor.normalize(frame), issues)

    assert [issue.code for issue in duplicate_issues] == [
        "duplicate_input_index",
        "duplicate_source_url",
        "duplicate_jd_sku_id",
    ]
    assert result.summary == {
        "input_rows": 4,
        "normalized_rows": 1,
        "failed_rows": 3,
        "duplicate_rows": 3,
    }
    assert result.normalized_rows["input_index"].tolist() == ["1"]
    assert result.failed_rows["error_code"].tolist() == [
        "duplicate_input_index",
        "duplicate_source_url",
        "duplicate_jd_sku_id",
    ]
    assert result.failed_rows["crawl_status"].tolist() == ["failed", "failed", "failed"]
    assert len(result.normalized_rows) + len(result.failed_rows) == len(frame)


def test_validate_reports_missing_schema_and_invalid_success_fields() -> None:
    """JD validation reports schema and row errors instead of raising KeyError."""

    processor = JdProductProcessor()
    missing_column = _frame(_success_row()).drop(columns=["shop_name"])
    invalid_success = _frame(
        _success_row(
            input_index="",
            source_url="http://[",
            captured_at="not-a-timestamp",
            jd_sku_id="ABC",
            title=" ",
            primary_image_url="not-an-image-url",
        )
    )

    schema_issues = processor.validate(missing_column)
    row_issues = processor.validate(invalid_success)

    assert [issue.code for issue in schema_issues] == ["missing_jd_columns"]
    assert {
        "invalid_input_index",
        "invalid_source_url",
        "invalid_captured_at",
        "invalid_jd_sku_id",
        "field_missing",
        "invalid_primary_image_url",
    }.issubset({issue.code for issue in row_issues})


def test_missing_jd_column_reconciles_every_row_into_failed_output(
    tmp_path: Path,
) -> None:
    """A site-schema omission writes explicit failures instead of losing rows."""

    source = tmp_path / "missing-shop.csv"
    missing_shop = read_source_file(JD_FIXTURE).drop(columns=["shop_name"])
    missing_shop.to_csv(source, index=False)

    result = process_dataset("jd_product", source, tmp_path / "output")

    assert result.summary == {
        "input_rows": 4,
        "normalized_rows": 0,
        "failed_rows": 4,
        "duplicate_rows": 0,
    }
    assert set(result.failed_rows["error_code"]) == {"missing_jd_columns"}
    assert result.failed_rows["input_index"].tolist() == ["1", "2", "3", "4"]
