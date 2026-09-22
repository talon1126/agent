"""Protect the J10 Playwright JD detail collector contract.

The live smoke test intentionally reaches JD and must fail visibly when the
network, selectors, or page authorization state no longer supports collection.
Synthetic inputs cover only deterministic invalid and verification branches.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from data_ops.collectors.base import CaptureRequest, CollectorMode
from data_ops.collectors.jd_product_playwright import (
    JdPageState,
    PlaywrightJdProductCollector,
    classify_jd_page_state,
)
from data_ops.discovery.jd_product_urls import discover_jd_product_urls
from data_ops.processors.jd_product_contract import JD_PRODUCT_WEB_EXPORT_CONTRACT

REAL_JD_CATEGORY_URL = "https://www.jd.com/hprm/9987a354086f281133b6.html"


def _read_rows(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """Return a raw CSV header and rows without changing field values."""

    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        return tuple(reader.fieldnames or ()), list(reader)


def test_collector_modes_are_stable_cli_values() -> None:
    """J10 exposes exactly the Playwright and Yingdao collector choices."""

    assert [mode.value for mode in CollectorMode] == ["playwright", "yingdao"]
    assert PlaywrightJdProductCollector().browser_channel == "chrome"


def test_classify_jd_page_state_stops_manual_verification() -> None:
    """Explicit login and verification pages cannot become product rows."""

    assert (
        classify_jd_page_state(
            "https://passport.jd.com/new/login.aspx",
            "京东登录 扫码登录",
        )
        == JdPageState.MANUAL_VERIFICATION_REQUIRED
    )
    assert (
        classify_jd_page_state(
            "https://safe.jd.com/verify",
            "安全验证",
        )
        == JdPageState.MANUAL_VERIFICATION_REQUIRED
    )
    assert (
        classify_jd_page_state(
            "https://pc-frequent-pro.pf.jd.com/?from=pc_item&reason=403",
            "暂时无法展示该商品的信息",
        )
        == JdPageState.ACCESS_RESTRICTED
    )


def test_playwright_collector_writes_failed_row_for_invalid_url(tmp_path: Path) -> None:
    """An invalid input is reconciled without opening a browser or dropping a row."""

    input_csv = tmp_path / "input.csv"
    input_csv.write_text(
        "input_index,product_url\n1,https://example.com/not-jd\n",
        encoding="utf-8-sig",
    )
    raw_csv = tmp_path / "raw.csv"
    result = PlaywrightJdProductCollector().collect(
        CaptureRequest(
            batch_id="j10_invalid",
            input_csv=input_csv,
            raw_output_csv=raw_csv,
        )
    )

    header, rows = _read_rows(raw_csv)
    assert result.status == "success"
    assert result.captured_count == 1
    assert header == JD_PRODUCT_WEB_EXPORT_CONTRACT.output_columns
    assert len(rows) == 1
    assert rows[0]["crawl_status"] == "failed"
    assert rows[0]["error_code"] == "invalid_input"


def test_playwright_collector_collects_real_jd_product(tmp_path: Path) -> None:
    """A bounded live JD batch yields at least one complete 13-column row."""

    user_data_dir = os.environ.get("JD_PLAYWRIGHT_USER_DATA_DIR")
    storage_state = None if user_data_dir else os.environ.get("JD_PLAYWRIGHT_STORAGE_STATE")
    headless = os.environ.get("JD_PLAYWRIGHT_HEADLESS", "true").lower() != "false"
    input_csv = tmp_path / "urls.csv"
    discovery = discover_jd_product_urls(
        seed_url=REAL_JD_CATEGORY_URL,
        output_path=input_csv,
        max_pages=1,
        max_items=1,
        browser_channel="chrome",
        storage_state=storage_state,
        headless=headless,
    )
    raw_csv = tmp_path / "raw.csv"
    result = PlaywrightJdProductCollector(
        browser_channel="chrome",
        user_data_dir=user_data_dir,
        storage_state=storage_state,
        headless=headless,
    ).collect(
        CaptureRequest(
            batch_id="j10_live",
            input_csv=discovery.output_path,
            raw_output_csv=raw_csv,
        )
    )

    header, rows = _read_rows(raw_csv)
    assert result.status == "success", result.error_code
    assert result.captured_count == 1
    assert header == JD_PRODUCT_WEB_EXPORT_CONTRACT.output_columns
    assert len(rows) == 1
    assert rows[0]["crawl_status"] == "success", rows[0]["error_code"]
    assert rows[0]["jd_sku_id"]
    assert rows[0]["title"]
    assert rows[0]["display_price"]
    assert rows[0]["shop_name"]
    assert rows[0]["primary_image_url"]
    assert rows[0]["capture_region"] == ""
