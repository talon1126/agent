"""Shared pagination helpers for Feishu read-model endpoints.

This module belongs to the mock-api router layer. Feishu table sync endpoints
all expose small read models such as Items, Order Items, Procurement Requests,
and Flash Sales. They should share the same limit/offset contract so
feishu-adapter can reuse one pagination loop instead of encoding table-specific
rules in every scheduled sync path.
"""

from typing import Any


def normalize_limit_offset(
    *,
    limit: int | None,
    offset: int | None,
    default_limit: int = 100,
    max_limit: int = 500,
) -> tuple[int, int]:
    """Normalize caller-provided pagination values into safe integers.

    Args:
        limit: Requested page size from feishu-adapter or n8n.
        offset: Zero-based row offset from the source read model.
        default_limit: Page size used when the caller omits `limit`.
        max_limit: Hard cap that prevents accidental unbounded exports.

    Returns:
        A `(limit, offset)` tuple with `limit` clamped to `1..max_limit` and
        `offset` clamped to zero or above.
    """

    safe_limit = max(min(int(limit or default_limit), max_limit), 1)
    safe_offset = max(int(offset or 0), 0)
    return safe_limit, safe_offset


def page_items(
    rows: list[dict[str, Any]],
    *,
    limit: int | None,
    offset: int | None,
    default_limit: int = 100,
    max_limit: int = 500,
) -> tuple[list[dict[str, Any]], bool, int | None]:
    """Slice rows and return the standard mock-api pagination envelope fields.

    Args:
        rows: Fully filtered read-model rows.
        limit: Requested page size.
        offset: Zero-based row offset.
        default_limit: Page size used when the caller omits `limit`.
        max_limit: Hard cap for one response page.

    Returns:
        A tuple containing the current page, whether another page exists, and
        the next offset. `next_offset` is `None` when the current page is the
        final page.
    """

    safe_limit, safe_offset = normalize_limit_offset(
        limit=limit,
        offset=offset,
        default_limit=default_limit,
        max_limit=max_limit,
    )
    next_index = safe_offset + safe_limit
    page = rows[safe_offset:next_index]
    has_more = next_index < len(rows)
    return page, has_more, next_index if has_more else None
