"""Read-only batch product facts for one Agent decision snapshot."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Self

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from sqlalchemy import func, select

from app.routers.warehouse.state import get_warehouse_repository
from app.warehouse_store import inventory_location_balances, item_reviews, items


router = APIRouter()
ItemId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class ProductSnapshotBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_ids: list[ItemId] = Field(min_length=1, max_length=100)


class ProductSnapshotObservedAt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog: datetime
    price: datetime
    stock: datetime | None = None
    rating: datetime | None = None
    delivery: datetime


class ProductSnapshotDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipping_available: bool
    pickup_available: bool
    delivery_available: bool
    estimated_delivery_at: datetime | None = None


class ProductSnapshotFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    category: str
    brand: str
    current_price: Decimal = Field(ge=0)
    currency: str
    stock: int | None = Field(default=None, ge=0)
    specifications: dict[str, str]
    rating: Decimal | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    delivery: ProductSnapshotDelivery
    observed_at: ProductSnapshotObservedAt


class ProductSnapshotItemError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ProductSnapshotBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: ItemId
    status: Literal["ok", "error"]
    facts: ProductSnapshotFacts | None = None
    error: ProductSnapshotItemError | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.status == "ok":
            if self.facts is None or self.error is not None:
                raise ValueError("successful item requires only facts")
        elif self.facts is not None or self.error is None:
            raise ValueError("failed item requires only an error")
        return self


class ProductSnapshotBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    source_version: str
    captured_at: datetime
    items: list[ProductSnapshotBatchItem] = Field(max_length=100)


def _source_version(captured_at: datetime) -> str:
    return "mock-api-product-snapshot-v1:" + captured_at.strftime("%Y%m%dT%H%M%SZ")


def load_product_snapshot_rows(
    repository: object,
    item_ids: list[str],
) -> list[dict[str, object]]:
    """Read catalog, stock, and rating facts in one set-based SQL statement."""

    stock_summary = (
        select(
            inventory_location_balances.c.item_id.label("stock_item_id"),
            func.sum(inventory_location_balances.c.quantity_on_hand).label("stock"),
            func.max(inventory_location_balances.c.updated_at).label(
                "stock_observed_at"
            ),
        )
        .where(inventory_location_balances.c.item_id.in_(item_ids))
        .where(inventory_location_balances.c.storage_status == "available")
        .group_by(inventory_location_balances.c.item_id)
        .subquery()
    )
    rating_summary = (
        select(
            item_reviews.c.item_id.label("rating_item_id"),
            func.avg(item_reviews.c.rating).label("rating"),
            func.count(item_reviews.c.id).label("review_count"),
            func.max(item_reviews.c.updated_at).label("rating_observed_at"),
        )
        .where(item_reviews.c.item_id.in_(item_ids))
        .group_by(item_reviews.c.item_id)
        .subquery()
    )
    statement = (
        select(
            items.c.item_id,
            items.c.item_name,
            items.c.brand,
            items.c.spec,
            items.c.category_id,
            items.c.price,
            stock_summary.c.stock,
            stock_summary.c.stock_observed_at,
            rating_summary.c.rating,
            rating_summary.c.review_count,
            rating_summary.c.rating_observed_at,
        )
        .outerjoin(stock_summary, stock_summary.c.stock_item_id == items.c.item_id)
        .outerjoin(
            rating_summary,
            rating_summary.c.rating_item_id == items.c.item_id,
        )
        .where(items.c.item_id.in_(item_ids))
    )
    engine = getattr(repository, "engine", None)
    if engine is None:
        raise RuntimeError("product snapshot repository has no SQL engine")
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [
        {
            **dict(row),
            "price": float(row["price"]),
            "stock": int(row["stock"]) if row["stock"] is not None else None,
            "rating": (
                round(float(row["rating"]), 1) if row["rating"] is not None else None
            ),
            "review_count": (
                int(row["review_count"]) if row["review_count"] is not None else None
            ),
        }
        for row in rows
    ]


@router.post(
    "/products/snapshots",
    response_model=ProductSnapshotBatchResponse,
)
def get_product_snapshots(
    request: ProductSnapshotBatchRequest,
) -> ProductSnapshotBatchResponse | JSONResponse:
    """Return one stable, partially successful result for every requested item."""

    repository = get_warehouse_repository()
    if repository is None:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": "product_snapshot_backend_unavailable",
                "message": "Postgres backend is required for product snapshots.",
            },
        )
    item_ids = list(dict.fromkeys(request.item_ids))
    captured_at = datetime.now(UTC)
    rows_by_id = {
        str(row["item_id"]): row
        for row in load_product_snapshot_rows(repository, item_ids)
    }
    response_items: list[ProductSnapshotBatchItem] = []
    for item_id in item_ids:
        row = rows_by_id.get(item_id)
        if row is None:
            response_items.append(
                ProductSnapshotBatchItem(
                    item_id=item_id,
                    status="error",
                    error=ProductSnapshotItemError(
                        code="item_not_found",
                        message="Item not found.",
                    ),
                )
            )
            continue
        response_items.append(
            ProductSnapshotBatchItem(
                item_id=item_id,
                status="ok",
                facts=ProductSnapshotFacts(
                    name=str(row["item_name"]),
                    category=str(row["category_id"]),
                    brand=str(row["brand"]),
                    current_price=Decimal(str(row["price"])),
                    currency="USD",
                    stock=row.get("stock"),
                    specifications={"summary": str(row["spec"])},
                    rating=(
                        Decimal(str(row["rating"]))
                        if row.get("rating") is not None
                        else None
                    ),
                    review_count=row.get("review_count"),
                    delivery=ProductSnapshotDelivery(
                        shipping_available=True,
                        pickup_available=True,
                        delivery_available=True,
                        estimated_delivery_at=captured_at + timedelta(days=1),
                    ),
                    observed_at=ProductSnapshotObservedAt(
                        catalog=captured_at,
                        price=captured_at,
                        # The snapshot query observes the authoritative stock
                        # balance now. The latest movement timestamp describes
                        # when stock changed, not when its value was observed.
                        stock=captured_at if row.get("stock") is not None else None,
                        rating=row.get("rating_observed_at"),
                        delivery=captured_at,
                    ),
                ),
            )
        )
    return ProductSnapshotBatchResponse(
        source_version=_source_version(captured_at),
        captured_at=captured_at,
        items=response_items,
    )
