"""Collect visible JD product details with a bounded Playwright session.

This collector consumes the canonical discovery CSV and publishes the same
13-column raw contract as Yingdao. JD selectors and page-state rules remain in
this site module; the pipeline only sees the common collector result. Login,
verification, and access restrictions stop the batch without bypass attempts.
"""

from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import (
    Error as PlaywrightError,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from data_ops.collectors.base import CaptureRequest, CaptureResult
from data_ops.discovery.jd_product_urls import canonicalize_jd_product_url
from data_ops.processors.jd_product_contract import JD_PRODUCT_WEB_EXPORT_CONTRACT

_LOGIN_HOSTS = frozenset({"passport.jd.com", "plogin.m.jd.com"})
_VERIFICATION_HOSTS = frozenset({"safe.jd.com", "verify.jd.com"})
_ACCESS_HOSTS = frozenset({"pc-frequent-pro.pf.jd.com"})
_ACCESS_PATH_MARKERS = ("/privatedomain/risk_handler/", "/risk_handler/")
_MANUAL_MARKERS = ("请输入验证码", "请完成安全验证", "扫码验证", "访问验证")
_ACCESS_MARKERS = ("页面访问受限", "访问过于频繁", "操作过于频繁", "请求存在风险")


class JdPageState(StrEnum):
    """Classify page states that map to the shared crawl error contract."""

    NORMAL = "normal"
    FIELD_MISSING = "field_missing"
    NAVIGATION_FAILED = "navigation_failed"
    PAGE_TIMEOUT = "page_timeout"
    MANUAL_VERIFICATION_REQUIRED = "manual_verification_required"
    ACCESS_RESTRICTED = "access_restricted"


@dataclass(frozen=True, slots=True)
class JdSelectorSet:
    """Keep ordered JD field fallbacks out of generic orchestration code."""

    title: tuple[str, ...] = (
        "#name h1",
        ".sku-name",
        "[class*='sku-name']",
        "h1",
    )
    display_price: tuple[str, ...] = (
        ".summary-price .p-price .price",
        ".p-price .price",
        "[class*='priceInfo'] [class*='price']",
        "[class*='price-info'] [class*='price']",
    )
    shop_name: tuple[str, ...] = (
        ".J-hove-wrap .name a",
        ".popbox-inner .name a",
        "[class*='shop-name']",
        "[class*='shopName']",
        "a[href*='mall.jd.com']",
    )
    primary_image: tuple[str, ...] = (
        "#spec-img",
        ".preview-img img",
        ".jqzoom img",
        "[class*='preview'] img",
    )


@dataclass(slots=True)
class LoadedBrowserContext:
    """Own a browser context and its optional non-persistent browser."""

    context: BrowserContext
    browser: Browser | None = None

    def close(self) -> None:
        """Close all Playwright resources created for one collector run."""

        self.context.close()
        if self.browser is not None:
            self.browser.close()


def classify_jd_page_state(url: str, body_text: str = "") -> JdPageState:
    """Classify explicit JD login, verification, and restriction pages.

    Generic header text such as ``请登录`` is deliberately ignored because it
    also appears on public product pages. Only dedicated hosts, risk paths, or
    strong page messages stop collection.
    """

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in _LOGIN_HOSTS or host in _VERIFICATION_HOSTS or host.endswith(".safe.jd.com"):
        return JdPageState.MANUAL_VERIFICATION_REQUIRED
    if host in _ACCESS_HOSTS:
        return JdPageState.ACCESS_RESTRICTED
    if any(marker in parsed.path for marker in _ACCESS_PATH_MARKERS):
        return JdPageState.ACCESS_RESTRICTED
    if any(marker in body_text for marker in _ACCESS_MARKERS):
        return JdPageState.ACCESS_RESTRICTED
    if any(marker in body_text for marker in _MANUAL_MARKERS):
        return JdPageState.MANUAL_VERIFICATION_REQUIRED
    return JdPageState.NORMAL


def load_jd_browser_context(
    playwright: Playwright,
    *,
    browser_channel: str = "chrome",
    browser_executable: str | Path | None = None,
    user_data_dir: str | Path | None = None,
    storage_state: str | Path | None = None,
    headless: bool = True,
) -> LoadedBrowserContext:
    """Create one authorized JD context from a dedicated local session.

    Args:
        playwright: Active Playwright runtime.
        browser_channel: Installed Chromium-compatible browser channel.
        browser_executable: Optional explicit browser executable.
        user_data_dir: Optional dedicated persistent profile directory.
        storage_state: Optional Playwright state JSON used without a profile.
        headless: Whether to hide the browser window.

    Returns:
        An owned context wrapper that closes its browser resources.

    Raises:
        ValueError: If both supported authorization-state mechanisms are set.
        FileNotFoundError: If the configured storage-state file is missing.

    Notes:
        The caller must keep session paths outside the repository or under an
        ignored runtime directory. Session contents never enter batch files.
    """

    if user_data_dir and storage_state:
        raise ValueError("use either JD user data directory or storage state, not both")
    launch_options: dict[str, object] = {"headless": headless}
    if browser_executable is not None:
        launch_options["executable_path"] = str(Path(browser_executable).resolve())
    elif browser_channel:
        launch_options["channel"] = browser_channel
    context_options: dict[str, object] = {"locale": "zh-CN"}
    if user_data_dir:
        context = playwright.chromium.launch_persistent_context(
            str(Path(user_data_dir).resolve()),
            **launch_options,
            **context_options,
        )
        return LoadedBrowserContext(context=context)
    if storage_state:
        state_path = Path(storage_state).resolve()
        if not state_path.is_file():
            raise FileNotFoundError("configured JD Playwright storage state does not exist")
        context_options["storage_state"] = str(state_path)
    browser = playwright.chromium.launch(**launch_options)
    return LoadedBrowserContext(context=browser.new_context(**context_options), browser=browser)


def _read_input_rows(path: Path) -> list[dict[str, str]]:
    """Load the two-column discovery handoff and preserve each source row."""

    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != JD_PRODUCT_WEB_EXPORT_CONTRACT.input_columns:
            raise ValueError("JD collector input CSV must contain input_index,product_url")
        return [dict(row) for row in reader]


def _captured_at() -> str:
    """Return a stable UTC timestamp for one observed row."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _empty_row(request: CaptureRequest, input_index: str, source_url: str) -> dict[str, str]:
    """Build one complete raw row before assigning its page outcome."""

    return {
        "dataset_type": "jd_product",
        "batch_id": request.batch_id,
        "input_index": input_index,
        "source_url": source_url,
        "captured_at": _captured_at(),
        "crawl_status": "failed",
        "error_code": "adapter_error",
        "jd_sku_id": "",
        "title": "",
        "display_price": "",
        "shop_name": "",
        "primary_image_url": "",
        "capture_region": "",
    }


def _first_text(page: Page, selectors: tuple[str, ...]) -> str:
    """Return the first visible non-empty text from ordered CSS fallbacks."""

    for selector in selectors:
        candidates = page.locator(selector)
        for index in range(min(candidates.count(), 5)):
            candidate = candidates.nth(index)
            try:
                if candidate.is_visible():
                    value = " ".join(candidate.inner_text(timeout=1_000).split())
                    if value:
                        return value
            except PlaywrightError:
                continue
    return ""


def _first_image_url(page: Page, selectors: tuple[str, ...]) -> str:
    """Return the first visible absolute image URL from ordered CSS fallbacks."""

    for selector in selectors:
        candidates = page.locator(selector)
        for index in range(min(candidates.count(), 5)):
            candidate = candidates.nth(index)
            try:
                if not candidate.is_visible():
                    continue
                for attribute in ("src", "data-origin", "data-lazy-img", "data-src"):
                    value = (candidate.get_attribute(attribute) or "").strip()
                    if value and not value.startswith("data:"):
                        return urljoin(page.url, value)
            except PlaywrightError:
                continue
    return ""


def _write_raw_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Atomically publish all reconciled rows in the exact processor order."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(
                target,
                fieldnames=JD_PRODUCT_WEB_EXPORT_CONTRACT.output_columns,
            )
            writer.writeheader()
            writer.writerows(rows)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


@dataclass(slots=True)
class PlaywrightJdProductCollector:
    """Visit canonical JD product URLs serially and deliver one row per input."""

    browser_channel: str = "chrome"
    browser_executable: str | Path | None = None
    user_data_dir: str | Path | None = None
    storage_state: str | Path | None = None
    headless: bool = True
    navigation_timeout_ms: int = 45_000
    field_wait_ms: int = 6_000
    selectors: JdSelectorSet = JdSelectorSet()

    def _collect_page(
        self,
        page: Page,
        request: CaptureRequest,
        source: dict[str, str],
    ) -> tuple[dict[str, str], JdPageState]:
        """Collect one input row and return its page state for batch control."""

        input_index = str(source.get("input_index", "")).strip()
        source_url = str(source.get("product_url", "")).strip()
        row = _empty_row(request, input_index, source_url)
        canonical_url = canonicalize_jd_product_url(source_url)
        if canonical_url is None or not input_index:
            row["error_code"] = "invalid_input"
            return row, JdPageState.NORMAL
        row["source_url"] = canonical_url
        row["jd_sku_id"] = canonical_url.rsplit("/", 1)[-1].removesuffix(".html")
        try:
            page.goto(canonical_url, wait_until="domcontentloaded")
            body_text = page.locator("body").inner_text(timeout=5_000)
        except PlaywrightTimeoutError:
            row["error_code"] = JdPageState.PAGE_TIMEOUT.value
            return row, JdPageState.PAGE_TIMEOUT
        except PlaywrightError:
            row["error_code"] = JdPageState.NAVIGATION_FAILED.value
            return row, JdPageState.NAVIGATION_FAILED
        state = classify_jd_page_state(page.url, body_text)
        if state != JdPageState.NORMAL:
            row["error_code"] = state.value
            return row, state

        deadline = datetime.now(UTC).timestamp() + self.field_wait_ms / 1_000
        fields = {"title": "", "display_price": "", "shop_name": "", "primary_image_url": ""}
        while datetime.now(UTC).timestamp() < deadline:
            state = classify_jd_page_state(page.url)
            if state in {
                JdPageState.MANUAL_VERIFICATION_REQUIRED,
                JdPageState.ACCESS_RESTRICTED,
            }:
                row["error_code"] = state.value
                return row, state
            fields["title"] = fields["title"] or _first_text(page, self.selectors.title)
            fields["display_price"] = fields["display_price"] or _first_text(
                page, self.selectors.display_price
            )
            fields["shop_name"] = fields["shop_name"] or _first_text(
                page, self.selectors.shop_name
            )
            fields["primary_image_url"] = fields["primary_image_url"] or _first_image_url(
                page, self.selectors.primary_image
            )
            if all(fields.values()):
                break
            page.wait_for_timeout(300)
        row.update(fields)
        if all(fields.values()):
            row["crawl_status"] = "success"
            row["error_code"] = ""
            return row, JdPageState.NORMAL
        row["crawl_status"] = "partial"
        row["error_code"] = JdPageState.FIELD_MISSING.value
        return row, JdPageState.FIELD_MISSING

    def collect(self, request: CaptureRequest) -> CaptureResult:
        """Collect one bounded batch and atomically publish its raw CSV.

        Login, verification, and access restriction states stop navigation for
        remaining inputs. Each unvisited input still receives one failed row so
        the checkpoint reconciles with the discovery CSV.
        """

        sources = _read_input_rows(request.input_csv)
        rows: list[dict[str, str]] = []
        terminal_error = ""
        valid_sources = [
            source
            for source in sources
            if canonicalize_jd_product_url(str(source.get("product_url", ""))) is not None
            and str(source.get("input_index", "")).strip()
        ]
        if valid_sources:
            try:
                with sync_playwright() as playwright:
                    loaded = load_jd_browser_context(
                        playwright,
                        browser_channel=self.browser_channel,
                        browser_executable=self.browser_executable,
                        user_data_dir=self.user_data_dir,
                        storage_state=self.storage_state,
                        headless=self.headless,
                    )
                    try:
                        page = (
                            loaded.context.pages[0]
                            if loaded.context.pages
                            else loaded.context.new_page()
                        )
                        page.set_default_navigation_timeout(self.navigation_timeout_ms)
                        for position, source in enumerate(sources):
                            row, state = self._collect_page(page, request, source)
                            rows.append(row)
                            if state in {
                                JdPageState.MANUAL_VERIFICATION_REQUIRED,
                                JdPageState.ACCESS_RESTRICTED,
                            }:
                                terminal_error = state.value
                                for remaining in sources[position + 1 :]:
                                    pending = _empty_row(
                                        request,
                                        str(remaining.get("input_index", "")).strip(),
                                        str(remaining.get("product_url", "")).strip(),
                                    )
                                    pending["error_code"] = state.value
                                    rows.append(pending)
                                break
                    finally:
                        loaded.close()
            except (OSError, PlaywrightError, ValueError) as exc:
                terminal_error = "navigation_failed"
                rows = []
                for source in sources:
                    row = _empty_row(
                        request,
                        str(source.get("input_index", "")).strip(),
                        str(source.get("product_url", "")).strip(),
                    )
                    row["error_code"] = terminal_error
                    rows.append(row)
                if isinstance(exc, ValueError):
                    raise
        else:
            for source in sources:
                row = _empty_row(
                    request,
                    str(source.get("input_index", "")).strip(),
                    str(source.get("product_url", "")).strip(),
                )
                row["error_code"] = "invalid_input"
                rows.append(row)
        _write_raw_csv(request.raw_output_csv, rows)
        failed_count = sum(row["crawl_status"] != "success" for row in rows)
        return CaptureResult(
            run_id=f"playwright-{request.batch_id}",
            status="failed" if terminal_error else "success",
            error_code=terminal_error,
            captured_count=len(rows),
            failed_count=failed_count,
        )


__all__ = [
    "JdPageState",
    "JdSelectorSet",
    "PlaywrightJdProductCollector",
    "classify_jd_page_state",
    "load_jd_browser_context",
]
