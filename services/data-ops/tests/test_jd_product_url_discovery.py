"""Verify JD URL discovery against canonicalization rules and the live site.

The live test intentionally reaches JD instead of using saved HTML or a
network mock. A failure therefore exposes network restrictions, verification
pages, or selector drift that would also break the production discovery run.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from data_ops.discovery.jd_product_urls import (
    JdProductDiscoveryError,
    canonicalize_jd_product_url,
    discover_jd_product_urls,
)

REAL_JD_CATEGORY_URL = "https://www.jd.com/hprm/9987a354086f281133b6.html"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://item.jd.com/100040114484.html?utm_source=test",
            "https://item.jd.com/100040114484.html",
        ),
        (
            "//item.jd.com/100040114484.html",
            "https://item.jd.com/100040114484.html",
        ),
        (
            "https://item.m.jd.com/product/100040114484.html",
            "https://item.jd.com/100040114484.html",
        ),
        (
            "//chat.jd.com/index.action?entry=jd_search&pid=100040114484&score=5",
            "https://item.jd.com/100040114484.html",
        ),
        ("https://example.com/100040114484.html", None),
        ("https://item.jd.com/not-a-sku.html", None),
    ],
)
def test_canonicalize_jd_product_url(source: str, expected: str | None) -> None:
    """Only numeric JD product references become the desktop canonical URL."""

    assert canonicalize_jd_product_url(source) == expected


def test_discover_jd_product_urls_requires_one_source(tmp_path: Path) -> None:
    """Discovery rejects missing or ambiguous keyword and seed URL inputs."""

    with pytest.raises(JdProductDiscoveryError, match="exactly one"):
        discover_jd_product_urls(
            output_path=tmp_path / "urls.csv",
            max_pages=1,
            max_items=5,
        )
    with pytest.raises(JdProductDiscoveryError, match="exactly one"):
        discover_jd_product_urls(
            keyword="phone",
            seed_url=REAL_JD_CATEGORY_URL,
            output_path=tmp_path / "urls.csv",
            max_pages=1,
            max_items=5,
        )


def test_discover_jd_product_urls_from_real_jd(tmp_path: Path) -> None:
    """A real JD category page yields canonical, unique, traceable product URLs."""

    output_path = tmp_path / "jd_product_urls.csv"
    result = discover_jd_product_urls(
        seed_url=REAL_JD_CATEGORY_URL,
        output_path=output_path,
        max_pages=1,
        max_items=5,
        browser_channel="msedge",
        storage_state=os.environ.get("JD_PLAYWRIGHT_STORAGE_STATE"),
        headless=True,
    )

    assert result.pages_visited == 1
    assert 1 <= len(result.product_urls) <= 5
    assert len(result.product_urls) == len(set(result.product_urls))
    assert all(
        url.startswith("https://item.jd.com/") and url.endswith(".html")
        for url in result.product_urls
    )
    with output_path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    assert [row["input_index"] for row in rows] == [str(index) for index in range(1, len(rows) + 1)]
    assert [row["product_url"] for row in rows] == list(result.product_urls)
