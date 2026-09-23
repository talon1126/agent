"""Immutable, source-aware product facts captured for one Agent turn."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Generic, Literal, Self, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


SnapshotText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
SnapshotId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


class _FreshnessObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    captured_at: datetime
    max_age_seconds: int = Field(gt=0)

    @field_validator("observed_at", "captured_at")
    @classmethod
    def validate_aware_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "freshness observation time")


class FactStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"


class FreshnessState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class Freshness(BaseModel):
    """Age classification for one independently expiring product fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: FreshnessState
    observed_at: datetime | None = None
    expires_at: datetime | None = None
    max_age_seconds: int | None = Field(default=None, gt=0)
    reason: SnapshotText | None = None

    @field_validator("observed_at", "expires_at")
    @classmethod
    def validate_aware_time(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value, "freshness time") if value is not None else None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.state is FreshnessState.UNKNOWN:
            if any(
                value is not None
                for value in (
                    self.observed_at,
                    self.expires_at,
                    self.max_age_seconds,
                )
            ):
                raise ValueError("unknown freshness cannot carry timestamps or max age")
            if self.reason is None:
                raise ValueError("unknown freshness requires a reason")
            return self
        if (
            self.observed_at is None
            or self.expires_at is None
            or self.max_age_seconds is None
        ):
            raise ValueError(
                "known freshness requires observed_at, expires_at and max age"
            )
        if self.expires_at <= self.observed_at:
            raise ValueError("freshness expiry must follow observation time")
        return self

    @classmethod
    def from_observation(
        cls,
        *,
        observed_at: datetime,
        captured_at: datetime,
        max_age_seconds: int,
    ) -> Freshness:
        observation = _FreshnessObservation(
            observed_at=observed_at,
            captured_at=captured_at,
            max_age_seconds=max_age_seconds,
        )
        from datetime import timedelta

        expires_at = observation.observed_at + timedelta(
            seconds=observation.max_age_seconds
        )
        return cls(
            state=(
                FreshnessState.FRESH
                if observation.captured_at <= expires_at
                else FreshnessState.STALE
            ),
            observed_at=observation.observed_at,
            expires_at=expires_at,
            max_age_seconds=observation.max_age_seconds,
        )

    @classmethod
    def unknown(cls, reason: str = "observation_missing") -> Freshness:
        return cls(state=FreshnessState.UNKNOWN, reason=reason)


class FactSource(BaseModel):
    """Bounded lineage shared by facts from one authoritative batch read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: SnapshotText
    endpoint: SnapshotText
    source_version: SnapshotId
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "captured_at")


FactValue = TypeVar("FactValue")


class ProductFact(BaseModel, Generic[FactValue]):
    """One typed value that can never disguise missing data as a default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: FactStatus
    value: FactValue | None = None
    source: FactSource
    freshness: Freshness

    @model_validator(mode="after")
    def validate_value_presence(self) -> Self:
        if self.status is FactStatus.KNOWN and self.value is None:
            raise ValueError("known fact requires a value")
        if self.status is FactStatus.UNKNOWN and self.value is not None:
            raise ValueError("unknown fact cannot carry a value")
        if (
            self.status is FactStatus.UNKNOWN
            and self.freshness.state is not FreshnessState.UNKNOWN
        ):
            raise ValueError("unknown fact must have unknown freshness")
        return self


class ProductSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: SnapshotText
    value: SnapshotText


class ProductSpecifications(BaseModel):
    """Deeply immutable, deterministically ordered product specifications."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    values: tuple[ProductSpecification, ...] = Field(
        default_factory=tuple, max_length=64
    )

    @model_validator(mode="after")
    def validate_unique_sorted_keys(self) -> Self:
        keys = [item.key for item in self.values]
        if len(keys) != len(set(keys)):
            raise ValueError("specification keys must be unique")
        if keys != sorted(keys, key=str.casefold):
            raise ValueError("specifications must be sorted by key")
        return self

    @classmethod
    def from_mapping(cls, values: dict[str, str]) -> ProductSpecifications:
        return cls(
            values=tuple(
                ProductSpecification(key=key, value=value)
                for key, value in sorted(
                    values.items(), key=lambda item: item[0].casefold()
                )
            )
        )


class DeliveryCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shipping_available: bool
    pickup_available: bool
    delivery_available: bool
    estimated_delivery_at: datetime | None = None

    @field_validator("estimated_delivery_at")
    @classmethod
    def validate_estimated_delivery_at(cls, value: datetime | None) -> datetime | None:
        return (
            _require_aware(value, "estimated_delivery_at")
            if value is not None
            else None
        )


class ProductSnapshotItem(BaseModel):
    """All facts for one item under one snapshot version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: SnapshotId
    name: ProductFact[str]
    category: ProductFact[str]
    brand: ProductFact[str]
    current_price: ProductFact[Decimal]
    currency: ProductFact[str]
    stock: ProductFact[int]
    specifications: ProductFact[ProductSpecifications]
    rating: ProductFact[Decimal]
    review_count: ProductFact[int]
    delivery: ProductFact[DeliveryCapability]


class ProductSnapshotError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: SnapshotText
    message: SnapshotText


class ProductSnapshotEntry(BaseModel):
    """One ordered batch result, including per-item failure without batch loss."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: SnapshotId
    status: Literal["ok", "error"]
    item: ProductSnapshotItem | None = None
    error: ProductSnapshotError | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        if self.status == "ok":
            if self.item is None or self.error is not None:
                raise ValueError("successful snapshot entry requires only an item")
            if self.item.item_id != self.item_id:
                raise ValueError("snapshot entry item_id must match its item")
        elif self.item is not None or self.error is None:
            raise ValueError("failed snapshot entry requires only an error")
        return self


class ProductSnapshot(BaseModel):
    """One sealed, immutable product-fact view for an Agent turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    snapshot_id: SnapshotId
    turn_id: SnapshotId
    source_version: SnapshotId
    captured_at: datetime
    requested_item_ids: tuple[SnapshotId, ...] = Field(min_length=1, max_length=100)
    entries: tuple[ProductSnapshotEntry, ...] = Field(min_length=1, max_length=100)

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "captured_at")

    @model_validator(mode="after")
    def validate_entries(self) -> Self:
        requested = list(self.requested_item_ids)
        if len(requested) != len(set(requested)):
            raise ValueError("requested item IDs must be unique")
        if [entry.item_id for entry in self.entries] != requested:
            raise ValueError("snapshot entries must match requested item order")
        return self

    @property
    def items_by_id(self) -> dict[str, ProductSnapshotItem]:
        return {
            entry.item_id: entry.item
            for entry in self.entries
            if entry.item is not None
        }
