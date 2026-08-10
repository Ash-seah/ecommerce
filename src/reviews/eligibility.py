"""Purchase verification and star-rating aggregation for product reviews."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from src.catalog.schemas import CatalogSnapshot
from src.reviews.schemas import ProductReview, StarCounts
from src.sandbox.models import SandboxState


@dataclass(frozen=True, slots=True)
class StarSummary:
    average_rating: float | None
    rating_count: int
    rounded_stars: int | None
    star_counts: StarCounts


def purchased_order_id(
    state: SandboxState, catalog: CatalogSnapshot, product_id: UUID
) -> UUID | None:
    """Return a placed-order id that bought this product, if any.

    Prefers recorded sale events (they carry product_id). Falls back to matching
    order lines via catalog variant membership.
    """

    for sale in state.sales.values():
        if (
            sale.status == "recorded"
            and sale.product_id == product_id
            and sale.order_id is not None
        ):
            order = state.orders.orders.get(sale.order_id)
            if order is not None and order.status == "placed":
                return sale.order_id

    variant_ids = {
        variant.id
        for product in catalog.products
        if product.id == product_id
        for variant in product.variants
    }
    if not variant_ids:
        return None
    for order in state.orders.orders.values():
        if order.status != "placed":
            continue
        if any(line.variant_id in variant_ids for line in order.lines):
            return order.id
    return None


def existing_session_review(
    state: SandboxState, product_id: UUID
) -> ProductReview | None:
    for review in state.reviews.values():
        if (
            review.product_id == product_id
            and review.sandbox_session_id is not None
            and review.status == "published"
        ):
            return review
    # Also treat hidden as "already reviewed" for create-gate uniqueness.
    for review in state.reviews.values():
        if review.product_id == product_id:
            return review
    return None


def empty_star_counts() -> StarCounts:
    return StarCounts()


def star_summary(reviews: list[ProductReview]) -> StarSummary:
    """Aggregate published reviews into average, rounded display stars, and histogram."""

    published = [item for item in reviews if item.status == "published"]
    counts = [0, 0, 0, 0, 0]
    for item in published:
        counts[item.rating - 1] += 1
    star_counts = StarCounts(
        one=counts[0],
        two=counts[1],
        three=counts[2],
        four=counts[3],
        five=counts[4],
    )
    if not published:
        return StarSummary(
            average_rating=None,
            rating_count=0,
            rounded_stars=None,
            star_counts=star_counts,
        )
    total = sum(item.rating for item in published)
    count = len(published)
    average = round(total / count, 2)
    # Half-up display stars for UI (4.5 → 5, 4.4 → 4).
    rounded = int(average + 0.5)
    rounded = min(5, max(1, rounded))
    return StarSummary(
        average_rating=average,
        rating_count=count,
        rounded_stars=rounded,
        star_counts=star_counts,
    )


def rating_summary(reviews: list[ProductReview]) -> tuple[float | None, int]:
    """Backward-compatible average/count helper."""

    summary = star_summary(reviews)
    return summary.average_rating, summary.rating_count
