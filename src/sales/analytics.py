"""Pure analytics over sale event sequences."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Hashable, Literal
from uuid import UUID

from src.sales.schemas import (
    BestSellerRow,
    BestSellers,
    SaleEvent,
    SalesBreakdown,
    SalesBreakdownRow,
    SalesGroupBy,
    SalesSeries,
    SalesSummary,
    SeriesPoint,
)


def filter_sales(
    events: Iterable[SaleEvent],
    *,
    status: Literal["recorded", "voided", "all"] = "recorded",
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    variant_id: UUID | None = None,
    coupon_code: str | None = None,
    country_code: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> list[SaleEvent]:
    rows = list(events)
    if status == "recorded":
        rows = [item for item in rows if item.status == "recorded"]
    elif status == "voided":
        rows = [item for item in rows if item.status == "voided"]
    if product_id is not None:
        rows = [item for item in rows if item.product_id == product_id]
    if category_id is not None:
        rows = [item for item in rows if item.category_id == category_id]
    if variant_id is not None:
        rows = [item for item in rows if item.variant_id == variant_id]
    if coupon_code is not None:
        needle = coupon_code.upper()
        rows = [item for item in rows if (item.coupon_code or "") == needle]
    if country_code is not None:
        rows = [item for item in rows if item.country_code == country_code.upper()]
    if occurred_from is not None:
        rows = [item for item in rows if item.occurred_at >= occurred_from]
    if occurred_to is not None:
        rows = [item for item in rows if item.occurred_at <= occurred_to]
    rows.sort(key=lambda item: (item.occurred_at, item.recorded_at, str(item.id)), reverse=True)
    return rows


def summarize(events: Iterable[SaleEvent]) -> SalesSummary:
    recorded = [item for item in events if item.status == "recorded"]
    voided = [item for item in events if item.status == "voided"]
    currencies = {item.currency for item in recorded}
    order_ids = {item.order_id for item in recorded if item.order_id is not None}
    # Synthetic admin sales without order_id still count as one "order" each.
    synthetic = sum(1 for item in recorded if item.order_id is None)
    orders = len(order_ids) + synthetic
    gross = sum(item.line_gross_minor for item in recorded)
    discount = sum(item.allocated_discount_minor for item in recorded)
    shipping = sum(item.allocated_shipping_minor for item in recorded)
    tax = sum(item.allocated_tax_minor for item in recorded)
    net = sum(item.line_net_minor for item in recorded)
    geo = {
        (item.country_code, item.region, item.city, item.postal_code)
        for item in recorded
        if item.country_code is not None
    }
    return SalesSummary(
        currency=next(iter(currencies)) if len(currencies) == 1 else None,
        orders=orders,
        lines=len(recorded),
        units_sold=sum(item.quantity for item in recorded),
        gross_minor=gross,
        discount_minor=discount,
        shipping_minor=shipping,
        tax_minor=tax,
        net_minor=net,
        average_order_minor=(net // orders) if orders else 0,
        voided_lines=len(voided),
        voided_net_minor=sum(item.line_net_minor for item in voided),
        unique_products=len({item.product_id for item in recorded}),
        unique_variants=len({item.variant_id for item in recorded}),
        unique_customers_geo=len(geo),
    )


def bestsellers(
    events: Iterable[SaleEvent],
    *,
    metric: Literal["revenue", "units"] = "revenue",
    limit: int = 10,
) -> BestSellers:
    recorded = [item for item in events if item.status == "recorded"]
    buckets: dict[UUID, dict[str, object]] = {}
    for item in recorded:
        bucket = buckets.setdefault(
            item.product_id,
            {
                "product_id": item.product_id,
                "product_slug": item.product_slug,
                "product_name": item.product_name,
                "category_id": item.category_id,
                "category_name": item.category_name,
                "units": 0,
                "revenue": 0,
                "orders": set(),
            },
        )
        bucket["units"] = int(bucket["units"]) + item.quantity
        bucket["revenue"] = int(bucket["revenue"]) + item.line_net_minor
        orders = bucket["orders"]
        assert isinstance(orders, set)
        orders.add(item.order_id or item.id)
    rows = [
        BestSellerRow(
            product_id=data["product_id"],  # type: ignore[arg-type]
            product_slug=str(data["product_slug"]),
            product_name=str(data["product_name"]),
            category_id=data["category_id"],  # type: ignore[arg-type]
            category_name=(
                None if data["category_name"] is None else str(data["category_name"])
            ),
            units_sold=int(data["units"]),
            revenue_minor=int(data["revenue"]),
            orders=len(data["orders"]),  # type: ignore[arg-type]
            average_unit_price_minor=(
                int(data["revenue"]) // int(data["units"]) if int(data["units"]) else 0
            ),
        )
        for data in buckets.values()
    ]
    key = (lambda row: row.revenue_minor) if metric == "revenue" else (lambda row: row.units_sold)
    rows.sort(key=key, reverse=True)
    return BestSellers(metric=metric, items=tuple(rows[:limit]))


def _bucket_start(stamp: datetime, bucket: Literal["hour", "day"]) -> datetime:
    aware = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)
    aware = aware.astimezone(UTC)
    if bucket == "hour":
        return aware.replace(minute=0, second=0, microsecond=0)
    return aware.replace(hour=0, minute=0, second=0, microsecond=0)


def timeseries(
    events: Iterable[SaleEvent], *, bucket: Literal["hour", "day"] = "day"
) -> SalesSeries:
    recorded = [item for item in events if item.status == "recorded"]
    groups: dict[datetime, dict[str, object]] = defaultdict(
        lambda: {"orders": set(), "units": 0, "gross": 0, "net": 0}
    )
    for item in recorded:
        key = _bucket_start(item.occurred_at, bucket)
        group = groups[key]
        orders = group["orders"]
        assert isinstance(orders, set)
        orders.add(item.order_id or item.id)
        group["units"] = int(group["units"]) + item.quantity
        group["gross"] = int(group["gross"]) + item.line_gross_minor
        group["net"] = int(group["net"]) + item.line_net_minor
    points = [
        SeriesPoint(
            bucket_start=start,
            orders=len(data["orders"]),  # type: ignore[arg-type]
            units_sold=int(data["units"]),
            gross_minor=int(data["gross"]),
            net_minor=int(data["net"]),
        )
        for start, data in sorted(groups.items())
    ]
    return SalesSeries(bucket=bucket, points=tuple(points))


def _group_key(item: SaleEvent, group_by: SalesGroupBy) -> Hashable:
    if group_by == "category":
        return item.category_id
    if group_by == "coupon":
        return item.coupon_code
    return (item.country_code, item.region, item.city)


def _blank_bucket(item: SaleEvent, group_by: SalesGroupBy) -> dict[str, object]:
    base: dict[str, object] = {
        "orders": set(),
        "lines": 0,
        "units": 0,
        "discount": 0,
        "net": 0,
        "category_id": None,
        "category_slug": None,
        "category_name": None,
        "coupon_code": None,
        "country_code": None,
        "region": None,
        "city": None,
    }
    if group_by == "category":
        base["category_id"] = item.category_id
        base["category_slug"] = item.category_slug
        base["category_name"] = item.category_name
    elif group_by == "coupon":
        base["coupon_code"] = item.coupon_code
    else:
        base["country_code"] = item.country_code
        base["region"] = item.region
        base["city"] = item.city
    return base


def group_by(events: Iterable[SaleEvent], *, by: SalesGroupBy) -> SalesBreakdown:
    recorded = [item for item in events if item.status == "recorded"]
    buckets: dict[Hashable, dict[str, object]] = {}
    for item in recorded:
        key = _group_key(item, by)
        bucket = buckets.setdefault(key, _blank_bucket(item, by))
        orders = bucket["orders"]
        assert isinstance(orders, set)
        orders.add(item.order_id or item.id)
        bucket["lines"] = int(bucket["lines"]) + 1
        bucket["units"] = int(bucket["units"]) + item.quantity
        bucket["discount"] = int(bucket["discount"]) + item.allocated_discount_minor
        bucket["net"] = int(bucket["net"]) + item.line_net_minor
    rows = [
        SalesBreakdownRow(
            category_id=data["category_id"],  # type: ignore[arg-type]
            category_slug=(
                None if data["category_slug"] is None else str(data["category_slug"])
            ),
            category_name=(
                None if data["category_name"] is None else str(data["category_name"])
            ),
            coupon_code=(
                None if data["coupon_code"] is None else str(data["coupon_code"])
            ),
            country_code=(
                None if data["country_code"] is None else str(data["country_code"])
            ),
            region=None if data["region"] is None else str(data["region"]),
            city=None if data["city"] is None else str(data["city"]),
            orders=len(data["orders"]),  # type: ignore[arg-type]
            lines=int(data["lines"]),
            units_sold=int(data["units"]),
            discount_minor=int(data["discount"]),
            net_minor=int(data["net"]),
        )
        for data in buckets.values()
    ]
    rows.sort(key=lambda row: row.net_minor, reverse=True)
    return SalesBreakdown(group_by=by, items=tuple(rows))


def feed(
    events: Iterable[SaleEvent],
    *,
    since: datetime | None,
    limit: int = 50,
) -> list[SaleEvent]:
    rows = sorted(
        events,
        key=lambda item: (item.recorded_at, str(item.id)),
        reverse=True,
    )
    if since is not None:
        rows = [item for item in rows if item.recorded_at > since]
    return rows[:limit]


def page_items(items: list, page: int, page_size: int) -> tuple[list, int, int]:
    total = len(items)
    pages = max(1, (total + page_size - 1) // page_size) if total else 0
    start = (page - 1) * page_size
    return items[start : start + page_size], total, pages
