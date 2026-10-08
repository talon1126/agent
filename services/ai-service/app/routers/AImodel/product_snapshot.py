"""Turn-scoped batch capture of authoritative mock-api product facts."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent_trace import (
    AgentTraceContext,
    AgentTraceEventType,
    AgentTraceStatus,
)
from .product_models import (
    DeliveryCapability,
    FactSource,
    FactStatus,
    Freshness,
    ProductSnapshot,
    ProductSnapshotEntry,
    ProductSnapshotError,
    ProductSnapshotItem,
    ProductSpecifications,
)


class ProductSnapshotErrorBase(RuntimeError):
    """Base failure for callers that must stop a fact-dependent decision."""


class ProductSnapshotSealed(ProductSnapshotErrorBase):
    """Raised when a turn tries to replace or extend its captured fact set."""


class ProductSnapshotTransportError(ProductSnapshotErrorBase):
    """Raised when the authoritative batch endpoint cannot be consumed safely."""


class SnapshotFreshnessRules(BaseModel):
    """Independent maximum ages for product fact groups."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_seconds: int = Field(default=86400, gt=0)
    price_seconds: int = Field(default=300, gt=0)
    stock_seconds: int = Field(default=60, gt=0)
    rating_seconds: int = Field(default=3600, gt=0)
    delivery_seconds: int = Field(default=300, gt=0)


class _RawObservedAt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog: datetime | None = None
    price: datetime | None = None
    stock: datetime | None = None
    rating: datetime | None = None
    delivery: datetime | None = None


class _RawDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shipping_available: bool
    pickup_available: bool
    delivery_available: bool
    estimated_delivery_at: datetime | None = None


class _RawFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = None
    category: str | None = None
    brand: str | None = None
    current_price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    stock: int | None = Field(default=None, ge=0)
    specifications: dict[str, str] | None = None
    rating: Decimal | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    delivery: _RawDelivery | None = None
    observed_at: _RawObservedAt


class _RawItemError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class _RawItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    status: Literal["ok", "error"]
    facts: _RawFacts | None = None
    error: _RawItemError | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.status == "ok":
            if self.facts is None or self.error is not None:
                raise ValueError("successful raw item requires only facts")
        elif self.facts is not None or self.error is None:
            raise ValueError("failed raw item requires only an error")
        return self


class _RawBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: Literal[True]
    source_version: str
    captured_at: datetime
    items: tuple[_RawItem, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_unique_items(self) -> Self:
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("batch response item IDs must be unique")
        return self


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_snapshot_id() -> str:
    return f"snapshot-{uuid4().hex}"


def _iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _normalize_item_ids(item_ids: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_item_id in item_ids:
        item_id = str(raw_item_id).strip()
        if not item_id:
            raise ValueError("item IDs cannot be blank")
        if len(item_id) > 128:
            raise ValueError("item ID exceeds 128 characters")
        if item_id not in seen:
            normalized.append(item_id)
            seen.add(item_id)
    if not normalized:
        raise ValueError("at least one item ID is required")
    if len(normalized) > 100:
        raise ValueError("at most 100 item IDs may be captured")
    return tuple(normalized)


def _fact_payload(
    value: Any,
    *,
    observed_at: datetime | None,
    max_age_seconds: int,
    captured_at: datetime,
    source: FactSource,
) -> dict[str, Any]:
    if value is None:
        return {
            "status": FactStatus.UNKNOWN,
            "value": None,
            "source": source,
            "freshness": Freshness.unknown("fact_missing"),
        }
    freshness = (
        Freshness.unknown("observation_missing")
        if observed_at is None
        else Freshness.from_observation(
            observed_at=observed_at,
            captured_at=captured_at,
            max_age_seconds=max_age_seconds,
        )
    )
    return {
        "status": FactStatus.KNOWN,
        "value": value,
        "source": source,
        "freshness": freshness,
    }


def _snapshot_item(
    raw: _RawItem,
    *,
    source: FactSource,
    captured_at: datetime,
    rules: SnapshotFreshnessRules,
) -> ProductSnapshotItem:
    facts = raw.facts
    if facts is None:
        raise ValueError("successful item is missing facts")
    specifications = (
        ProductSpecifications.from_mapping(facts.specifications)
        if facts.specifications is not None
        else None
    )
    delivery = (
        DeliveryCapability.model_validate(facts.delivery.model_dump())
        if facts.delivery is not None
        else None
    )
    common = {
        "captured_at": captured_at,
        "source": source,
    }
    return ProductSnapshotItem(
        item_id=raw.item_id,
        name=_fact_payload(
            facts.name,
            observed_at=facts.observed_at.catalog,
            max_age_seconds=rules.catalog_seconds,
            **common,
        ),
        category=_fact_payload(
            facts.category,
            observed_at=facts.observed_at.catalog,
            max_age_seconds=rules.catalog_seconds,
            **common,
        ),
        brand=_fact_payload(
            facts.brand,
            observed_at=facts.observed_at.catalog,
            max_age_seconds=rules.catalog_seconds,
            **common,
        ),
        current_price=_fact_payload(
            facts.current_price,
            observed_at=facts.observed_at.price,
            max_age_seconds=rules.price_seconds,
            **common,
        ),
        currency=_fact_payload(
            facts.currency,
            observed_at=facts.observed_at.price,
            max_age_seconds=rules.price_seconds,
            **common,
        ),
        stock=_fact_payload(
            facts.stock,
            observed_at=facts.observed_at.stock,
            max_age_seconds=rules.stock_seconds,
            **common,
        ),
        specifications=_fact_payload(
            specifications,
            observed_at=facts.observed_at.catalog,
            max_age_seconds=rules.catalog_seconds,
            **common,
        ),
        rating=_fact_payload(
            facts.rating,
            observed_at=facts.observed_at.rating,
            max_age_seconds=rules.rating_seconds,
            **common,
        ),
        review_count=_fact_payload(
            facts.review_count,
            observed_at=facts.observed_at.rating,
            max_age_seconds=rules.rating_seconds,
            **common,
        ),
        delivery=_fact_payload(
            delivery,
            observed_at=facts.observed_at.delivery,
            max_age_seconds=rules.delivery_seconds,
            **common,
        ),
    )


class ProductSnapshotClient:
    """Capture and seal one authoritative product batch per Agent turn."""

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.Client | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] = _new_snapshot_id,
        freshness_rules: SnapshotFreshnessRules | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client
        self.clock = clock
        self.id_factory = id_factory
        self.freshness_rules = freshness_rules or SnapshotFreshnessRules()
        self.timeout_seconds = timeout_seconds
        self._lock = threading.RLock()
        self._by_turn: dict[str, ProductSnapshot] = {}
        self._by_snapshot_id: dict[str, ProductSnapshot] = {}

    def capture_for_turn(
        self,
        *,
        turn_id: str,
        item_ids: Iterable[str],
        trace_context: AgentTraceContext | None = None,
    ) -> ProductSnapshot:
        normalized_turn_id = str(turn_id).strip()
        if not normalized_turn_id:
            raise ValueError("turn_id cannot be blank")
        requested = _normalize_item_ids(item_ids)
        with self._lock:
            existing = self._by_turn.get(normalized_turn_id)
            if existing is not None:
                if set(existing.requested_item_ids) != set(requested):
                    raise ProductSnapshotSealed(
                        "product snapshot is sealed for this turn"
                    )
                return existing
            snapshot = self._capture(
                turn_id=normalized_turn_id,
                item_ids=requested,
                trace_context=trace_context,
            )
            self._by_turn[normalized_turn_id] = snapshot
            self._by_snapshot_id[snapshot.snapshot_id] = snapshot
            return snapshot

    def get_snapshot(self, snapshot_id: str) -> ProductSnapshot:
        with self._lock:
            snapshot = self._by_snapshot_id.get(str(snapshot_id).strip())
        if snapshot is None:
            raise KeyError("product snapshot does not exist")
        return snapshot

    def _capture(
        self,
        *,
        turn_id: str,
        item_ids: tuple[str, ...],
        trace_context: AgentTraceContext | None,
    ) -> ProductSnapshot:
        event = (
            trace_context.begin_event(
                AgentTraceEventType.CONTEXT,
                stage="product_snapshot",
                summary={"requested_count": len(item_ids)},
            )
            if trace_context is not None
            else None
        )
        try:
            raw = self._fetch(item_ids)
            received_at = self.clock()
            if raw.captured_at > received_at:
                raise ProductSnapshotTransportError(
                    "snapshot captured_at cannot be in the future"
                )
            raw_by_id = {item.item_id: item for item in raw.items}
            unexpected = set(raw_by_id) - set(item_ids)
            if unexpected:
                raise ProductSnapshotTransportError(
                    "batch response contains unrequested item IDs"
                )
            source = FactSource(
                provider="mock-api",
                endpoint="/products/snapshots",
                source_version=raw.source_version,
                captured_at=raw.captured_at,
            )
            entries: list[ProductSnapshotEntry] = []
            for item_id in item_ids:
                item = raw_by_id.get(item_id)
                if item is None:
                    entries.append(
                        ProductSnapshotEntry(
                            item_id=item_id,
                            status="error",
                            error=ProductSnapshotError(
                                code="missing_result",
                                message="Batch endpoint omitted this item.",
                            ),
                        )
                    )
                elif item.status == "error":
                    assert item.error is not None
                    entries.append(
                        ProductSnapshotEntry(
                            item_id=item_id,
                            status="error",
                            error=ProductSnapshotError.model_validate(
                                item.error.model_dump()
                            ),
                        )
                    )
                else:
                    entries.append(
                        ProductSnapshotEntry(
                            item_id=item_id,
                            status="ok",
                            item=_snapshot_item(
                                item,
                                source=source,
                                captured_at=raw.captured_at,
                                rules=self.freshness_rules,
                            ),
                        )
                    )
            snapshot = ProductSnapshot(
                snapshot_id=self.id_factory(),
                turn_id=turn_id,
                source_version=raw.source_version,
                captured_at=raw.captured_at,
                requested_item_ids=item_ids,
                entries=tuple(entries),
            )
        except Exception as error:
            if event is not None:
                event.finish(AgentTraceStatus.ERROR, error=type(error).__name__)
            if isinstance(error, ProductSnapshotErrorBase):
                raise
            raise ProductSnapshotTransportError(
                f"invalid product snapshot response: {type(error).__name__}"
            ) from error
        if event is not None:
            event.related_ids["snapshot_id"] = snapshot.snapshot_id
            event.finish(
                AgentTraceStatus.SUCCESS,
                summary={
                    "snapshot_id": snapshot.snapshot_id,
                    "source_version": snapshot.source_version,
                    "captured_at": _iso_z(snapshot.captured_at),
                    "item_count": len(snapshot.items_by_id),
                    "error_count": sum(
                        entry.status == "error" for entry in snapshot.entries
                    ),
                },
            )
        return snapshot

    def _fetch(self, item_ids: tuple[str, ...]) -> _RawBatchResponse:
        owns_client = self.http_client is None
        client = self.http_client or httpx.Client(timeout=self.timeout_seconds)
        try:
            response = client.post(
                f"{self.base_url}/products/snapshots",
                json={"item_ids": list(item_ids)},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return _RawBatchResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as error:
            raise ProductSnapshotTransportError(
                f"product snapshot request failed: {type(error).__name__}"
            ) from error
        finally:
            if owns_client:
                client.close()
