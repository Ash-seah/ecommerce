"""Pure analytics over view event sequences."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from src.views.schemas import (
    TopPath,
    TopPaths,
    TopViewedCategories,
    TopViewedCategory,
    TopViewedProduct,
    TopViewedProducts,
    ViewEvent,
    ViewKind,
    ViewsByKind,
    ViewsByKindRow,
    ViewsSeries,
    ViewsSeriesPoint,
    ViewsSummary,
)


def filter_views(
    events: Iterable[ViewEvent],
    *,
    status: Literal["recorded", "voided", "all"] = "recorded",
    kind: ViewKind | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    path: str | None = None,
    country_code: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> list[ViewEvent]:
    rows = list(events)
    if status == "recorded":
        rows = [item for item in rows if item.status == "recorded"]
    elif status == "voided":
        rows = [item for item in rows if item.status == "voided"]
    if kind is not None:
        rows = [item for item in rows if item.kind == kind]
    if product_id is not None:
        rows = [item for item in rows if item.product_id == product_id]
    if category_id is not None:
        rows = [item for item in rows if item.category_id == category_id]
    if path is not None:
        rows = [item for item in rows if item.path == path]
    if country_code is not None:
        rows = [item for item in rows if item.country_code == country_code.upper()]
    if occurred_from is not None:
        rows = [item for item in rows if item.occurred_at >= occurred_from]
    if occurred_to is not None:
        rows = [item for item in rows if item.occurred_at <= occurred_to]
    rows.sort(key=lambda item: (item.occurred_at, item.recorded_at, str(item.id)), reverse=True)
    return rows


def summarize(events: Iterable[ViewEvent]) -> ViewsSummary:
    recorded = [item for item in events if item.status == "recorded"]
    voided = [item for item in events if item.status == "voided"]

    def count(kind: ViewKind) -> int:
        return sum(1 for item in recorded if item.kind == kind)

    return ViewsSummary(
        visits=count("visit"),
        product_views=count("product_view"),
        category_views=count("category_view"),
        listing_views=count("listing_view"),
        searches=count("search"),
        total_events=len(recorded),
        voided_events=len(voided),
        unique_products=len({item.product_id for item in recorded if item.product_id}),
        unique_categories=len({item.category_id for item in recorded if item.category_id}),
        unique_paths=len({item.path for item in recorded if item.path}),
        unique_sessions=len(
            {item.sandbox_session_id for item in recorded if item.sandbox_session_id}
        ),
    )


def _bucket_start(stamp: datetime, bucket: Literal["hour", "day"]) -> datetime:
    aware = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)
    aware = aware.astimezone(UTC)
    if bucket == "hour":
        return aware.replace(minute=0, second=0, microsecond=0)
    return aware.replace(hour=0, minute=0, second=0, microsecond=0)


def timeseries(
    events: Iterable[ViewEvent], *, bucket: Literal["hour", "day"] = "day"
) -> ViewsSeries:
    recorded = [item for item in events if item.status == "recorded"]
    groups: dict[datetime, dict[str, int]] = defaultdict(
        lambda: {
            "visit": 0,
            "product_view": 0,
            "category_view": 0,
            "listing_view": 0,
            "search": 0,
            "total": 0,
        }
    )
    for item in recorded:
        key = _bucket_start(item.occurred_at, bucket)
        groups[key][item.kind] += 1
        groups[key]["total"] += 1
    points = [
        ViewsSeriesPoint(
            bucket_start=start,
            visits=data["visit"],
            product_views=data["product_view"],
            category_views=data["category_view"],
            listing_views=data["listing_view"],
            searches=data["search"],
            total=data["total"],
        )
        for start, data in sorted(groups.items())
    ]
    return ViewsSeries(bucket=bucket, points=tuple(points))


def top_products(events: Iterable[ViewEvent], *, limit: int = 10) -> TopViewedProducts:
    recorded = [
        item for item in events if item.status == "recorded" and item.product_id is not None
    ]
    buckets: dict[UUID, dict[str, object]] = {}
    for item in recorded:
        assert item.product_id is not None
        bucket = buckets.setdefault(
            item.product_id,
            {
                "product_id": item.product_id,
                "product_slug": item.product_slug,
                "product_name": item.product_name,
                "views": 0,
            },
        )
        bucket["views"] = int(bucket["views"]) + 1
    rows = [
        TopViewedProduct(
            product_id=data["product_id"],  # type: ignore[arg-type]
            product_slug=None if data["product_slug"] is None else str(data["product_slug"]),
            product_name=None if data["product_name"] is None else str(data["product_name"]),
            views=int(data["views"]),
        )
        for data in buckets.values()
    ]
    rows.sort(key=lambda row: row.views, reverse=True)
    return TopViewedProducts(items=tuple(rows[:limit]))


def top_categories(events: Iterable[ViewEvent], *, limit: int = 10) -> TopViewedCategories:
    recorded = [
        item for item in events if item.status == "recorded" and item.category_id is not None
    ]
    buckets: dict[UUID, dict[str, object]] = {}
    for item in recorded:
        assert item.category_id is not None
        bucket = buckets.setdefault(
            item.category_id,
            {
                "category_id": item.category_id,
                "category_slug": item.category_slug,
                "category_name": item.category_name,
                "views": 0,
            },
        )
        bucket["views"] = int(bucket["views"]) + 1
    rows = [
        TopViewedCategory(
            category_id=data["category_id"],  # type: ignore[arg-type]
            category_slug=None if data["category_slug"] is None else str(data["category_slug"]),
            category_name=None if data["category_name"] is None else str(data["category_name"]),
            views=int(data["views"]),
        )
        for data in buckets.values()
    ]
    rows.sort(key=lambda row: row.views, reverse=True)
    return TopViewedCategories(items=tuple(rows[:limit]))


def top_paths(events: Iterable[ViewEvent], *, limit: int = 10) -> TopPaths:
    recorded = [item for item in events if item.status == "recorded" and item.path]
    counts: dict[str, int] = defaultdict(int)
    for item in recorded:
        assert item.path is not None
        counts[item.path] += 1
    rows = [TopPath(path=path, hits=hits) for path, hits in counts.items()]
    rows.sort(key=lambda row: row.hits, reverse=True)
    return TopPaths(items=tuple(rows[:limit]))


def by_kind(events: Iterable[ViewEvent]) -> ViewsByKind:
    recorded = [item for item in events if item.status == "recorded"]
    counts: dict[ViewKind, int] = defaultdict(int)
    for item in recorded:
        counts[item.kind] += 1
    rows = [ViewsByKindRow(kind=kind, count=count) for kind, count in counts.items()]
    rows.sort(key=lambda row: row.count, reverse=True)
    return ViewsByKind(items=tuple(rows))


def feed(
    events: Iterable[ViewEvent],
    *,
    since: datetime | None,
    limit: int = 50,
) -> list[ViewEvent]:
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
