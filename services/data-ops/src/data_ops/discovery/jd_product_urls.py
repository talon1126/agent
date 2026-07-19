"""Discover canonical JD product URLs from live search or category pages.

This concrete site module owns browser navigation, lazy-load scrolling,
pagination, SKU extraction, and the two-column Yingdao handoff CSV. It never
collects product details, bypasses verification, or writes business storage.
"""

from __future__ import annotations

import csv
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

_JD_PRODUCT_PATH_PATTERN = re.compile(r"^/(?:product/)?(?P<sku>[0-9]+)\.html/?$")
_JD_PRODUCT_HOSTS = frozenset({"item.jd.com", "item.m.jd.com", "mitem.jd.com"})
_JD_SEARCH_REFERENCE_HOSTS = frozenset({"chat.jd.com"})
_VERIFICATION_HOSTS = frozenset({"safe.jd.com", "verify.jd.com"})
_VERIFICATION_PATH_MARKERS = ("/privatedomain/risk_handler/",)
_VERIFICATION_MARKERS = (
    "访问验证",
    "安全验证",
    "请输入验证码",
    "页面访问受限",
)
_NEXT_PAGE_SELECTORS = (
    "a.pn-next",
    "a[aria-label*='下一页']",
    "a:has-text('下一页')",
)


class JdProductDiscoveryError(RuntimeError):
    """Report one controlled JD discovery failure with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        """Store a machine-readable code without exposing page contents."""

        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Describe one completed URL discovery handoff."""

    output_path: Path
    product_urls: tuple[str, ...]
    pages_visited: int


def canonicalize_jd_product_url(value: str) -> str | None:
    """Return the desktop canonical URL for one supported numeric JD SKU.

    Args:
        value: Product link or a JD search-card link carrying a numeric pid.

    Returns:
        ``https://item.jd.com/{sku}.html`` for supported links, otherwise None.
    """

    candidate = value.strip()
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host in _JD_PRODUCT_HOSTS:
        match = _JD_PRODUCT_PATH_PATTERN.fullmatch(parsed.path)
        if match is not None:
            return f"https://item.jd.com/{match.group('sku')}.html"
    if host in _JD_SEARCH_REFERENCE_HOSTS and parsed.path == "/index.action":
        product_ids = parse_qs(parsed.query).get("pid", [])
        if len(product_ids) == 1 and product_ids[0].isdigit():
            return f"https://item.jd.com/{product_ids[0]}.html"
    return None


def _validate_request(
    *,
    keyword: str | None,
    seed_url: str | None,
    max_pages: int,
    max_items: int,
) -> str:
    """Validate caller bounds and return the first live page URL."""

    has_keyword = bool(keyword and keyword.strip())
    has_seed_url = bool(seed_url and seed_url.strip())
    if has_keyword == has_seed_url:
        raise JdProductDiscoveryError(
            "invalid_discovery_source",
            "exactly one of keyword or seed_url is required",
        )
    if type(max_pages) is not int or max_pages <= 0:
        raise JdProductDiscoveryError("invalid_max_pages", "max_pages must be positive")
    if type(max_items) is not int or max_items <= 0:
        raise JdProductDiscoveryError("invalid_max_items", "max_items must be positive")
    if has_keyword:
        return "https://search.jd.com/Search?keyword=" + quote_plus(keyword.strip()) + "&enc=utf-8"
    parsed = urlparse(seed_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise JdProductDiscoveryError(
            "invalid_seed_url",
            "seed_url must be an absolute HTTP URL",
        )
    host = parsed.hostname.lower()
    if host != "jd.com" and not host.endswith(".jd.com"):
        raise JdProductDiscoveryError("invalid_seed_url", "seed_url must use a JD host")
    return seed_url.strip()


def _raise_for_verification(page: Page) -> None:
    """Stop when JD redirects to or renders an explicit verification page."""

    parsed = urlparse(page.url)
    host = (parsed.hostname or "").lower()
    if (
        host in _VERIFICATION_HOSTS
        or host.endswith(".safe.jd.com")
        or any(marker in parsed.path for marker in _VERIFICATION_PATH_MARKERS)
    ):
        raise JdProductDiscoveryError(
            "manual_verification_required",
            "JD redirected discovery to a verification page",
        )
    try:
        body_text = page.locator("body").inner_text(timeout=5_000)
    except PlaywrightError:
        return
    if any(marker in body_text for marker in _VERIFICATION_MARKERS):
        raise JdProductDiscoveryError(
            "manual_verification_required",
            "JD rendered an access-verification message",
        )


def _collect_product_urls(page: Page, known: dict[str, None], max_items: int) -> None:
    """Scroll the current page and append newly visible canonical product links."""

    previous_height = -1
    stable_rounds = 0
    for _ in range(12):
        hrefs = page.locator("a[href]").evaluate_all(
            "elements => elements.map(element => element.getAttribute('href') || '')"
        )
        for href in hrefs:
            canonical = canonicalize_jd_product_url(urljoin(page.url, str(href)))
            if canonical is not None:
                known.setdefault(canonical, None)
                if len(known) >= max_items:
                    return
        height = int(page.evaluate("document.documentElement.scrollHeight"))
        if height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
        if stable_rounds >= 2 and known:
            return
        previous_height = height
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(700)


def _collect_after_stable_navigation(
    page: Page,
    known: dict[str, None],
    max_items: int,
) -> None:
    """Retry once when a live JD page replaces its initial document."""

    for attempt in range(2):
        try:
            _collect_product_urls(page, known, max_items)
            return
        except PlaywrightError as exc:
            context_replaced = "Execution context was destroyed" in str(exc)
            if not context_replaced or attempt == 1:
                raise
            page.wait_for_load_state("domcontentloaded")
            _raise_for_verification(page)


def _load_and_collect_page(
    page: Page,
    page_url: str,
    known: dict[str, None],
    max_items: int,
) -> None:
    """Reload one live page once when its first document exposes no products."""

    initial_count = len(known)
    for _ in range(2):
        page.goto(page_url, wait_until="domcontentloaded")
        _raise_for_verification(page)
        _collect_after_stable_navigation(page, known, max_items)
        if len(known) > initial_count:
            return


def _next_page_url(page: Page) -> str | None:
    """Return the first enabled next-page link exposed by the live page."""

    for selector in _NEXT_PAGE_SELECTORS:
        candidates = page.locator(selector)
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            if not candidate.is_visible():
                continue
            class_name = (candidate.get_attribute("class") or "").lower()
            aria_disabled = (candidate.get_attribute("aria-disabled") or "").lower()
            if "disabled" in class_name or aria_disabled == "true":
                continue
            href = candidate.get_attribute("href")
            if href:
                return urljoin(page.url, href)
    return None


def _write_input_csv(output_path: Path, product_urls: tuple[str, ...]) -> None:
    """Atomically publish the two-column Yingdao input contract."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=("input_index", "product_url"))
            writer.writeheader()
            for index, product_url in enumerate(product_urls, start=1):
                writer.writerow({"input_index": index, "product_url": product_url})
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def discover_jd_product_urls(
    *,
    output_path: str | Path,
    max_pages: int,
    max_items: int,
    keyword: str | None = None,
    seed_url: str | None = None,
    browser_channel: str = "msedge",
    browser_executable: str | Path | None = None,
    storage_state: str | Path | None = None,
    headless: bool = True,
    navigation_timeout_ms: int = 45_000,
) -> DiscoveryResult:
    """Discover live JD product links and write the Yingdao input CSV.

    Args:
        output_path: Destination for ``input_index,product_url`` rows.
        max_pages: Maximum real JD pages to visit.
        max_items: Maximum unique SKU URLs to publish.
        keyword: Optional keyword used to build a JD search URL.
        seed_url: Optional explicit JD search or category URL.
        browser_channel: Installed Playwright browser channel.
        browser_executable: Optional explicit Chromium-compatible executable.
        storage_state: Optional local Playwright state for an authorized JD login.
        headless: Whether to hide the discovery browser window.
        navigation_timeout_ms: Per-page navigation timeout.

    Returns:
        Published path, canonical product URLs, and pages visited.

    Raises:
        JdProductDiscoveryError: For invalid inputs, network failures,
            verification pages, or a page that yields no product URLs.

    Side Effects:
        Opens real JD pages and atomically writes one CSV file.
    """

    first_url = _validate_request(
        keyword=keyword,
        seed_url=seed_url,
        max_pages=max_pages,
        max_items=max_items,
    )
    destination = Path(output_path).resolve()
    storage_state_path = Path(storage_state).resolve() if storage_state else None
    if storage_state_path is not None and not storage_state_path.is_file():
        raise JdProductDiscoveryError(
            "storage_state_missing",
            "configured Playwright storage state does not exist",
        )
    discovered: dict[str, None] = {}
    pages_visited = 0
    try:
        with sync_playwright() as playwright:
            launch_options: dict[str, object] = {"headless": headless}
            if browser_executable is not None:
                launch_options["executable_path"] = str(Path(browser_executable))
            elif browser_channel:
                launch_options["channel"] = browser_channel
            browser = playwright.chromium.launch(**launch_options)
            try:
                context_options: dict[str, object] = {"locale": "zh-CN"}
                if storage_state_path is not None:
                    context_options["storage_state"] = str(storage_state_path)
                context = browser.new_context(**context_options)
                page = context.new_page()
                page.set_default_navigation_timeout(navigation_timeout_ms)
                next_url: str | None = first_url
                visited_urls: set[str] = set()
                while next_url and pages_visited < max_pages and len(discovered) < max_items:
                    if next_url in visited_urls:
                        break
                    visited_urls.add(next_url)
                    _load_and_collect_page(page, next_url, discovered, max_items)
                    pages_visited += 1
                    next_url = _next_page_url(page)
            finally:
                browser.close()
    except JdProductDiscoveryError:
        raise
    except PlaywrightError as exc:
        raise JdProductDiscoveryError(
            "discovery_navigation_failed",
            f"Playwright could not complete live JD discovery ({type(exc).__name__})",
        ) from exc

    product_urls = tuple(discovered)[:max_items]
    if not product_urls:
        raise JdProductDiscoveryError(
            "no_product_urls",
            "the live JD page exposed no canonical product links",
        )
    _write_input_csv(destination, product_urls)
    return DiscoveryResult(
        output_path=destination,
        product_urls=product_urls,
        pages_visited=pages_visited,
    )


__all__ = [
    "DiscoveryResult",
    "JdProductDiscoveryError",
    "canonicalize_jd_product_url",
    "discover_jd_product_urls",
]
