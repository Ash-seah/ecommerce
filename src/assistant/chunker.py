"""Document assembly for catalog, reviews, and analytics facts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from src.catalog.schemas import CatalogSnapshot
from src.reviews.schemas import ProductReview
from src.sales.analytics import bestsellers, summarize
from src.sales.schemas import SaleEvent
from src.views.analytics import summarize as summarize_views
from src.views.schemas import ViewEvent


@dataclass(frozen=True, slots=True)
class RagDocument:
    source: str
    ref_id: str
    title: str
    content: str
    product_id: UUID | None = None

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


def documents_from_catalog(catalog: CatalogSnapshot) -> list[RagDocument]:
    categories = {item.id: item for item in catalog.categories}
    docs: list[RagDocument] = []
    for category in catalog.categories:
        if not category.is_active:
            continue
        text = "\n".join(
            part
            for part in (
                f"Category: {category.name}",
                f"Slug: {category.slug}",
                category.description or "",
            )
            if part
        )
        docs.append(
            RagDocument(
                source="category",
                ref_id=str(category.id),
                title=category.name,
                content=text[:8000],
            )
        )
    for product in catalog.products:
        if not product.is_active:
            continue
        category = categories.get(product.category_id)
        variant_lines = [
            f"- {variant.sku} {variant.name}: {variant.price_minor} {variant.currency}"
            for variant in product.variants
            if variant.is_active
        ]
        text = "\n".join(
            part
            for part in (
                f"Product: {product.name}",
                f"Slug: {product.slug}",
                f"Brand: {product.brand}" if product.brand else "",
                f"Category: {category.name}" if category is not None else "",
                f"Discount percent: {product.discount_percent}",
                product.description or "",
                product.details or "",
                ("Specifics: " + ", ".join(product.specifics)) if product.specifics else "",
                "Variants:",
                *variant_lines,
            )
            if part
        )
        docs.append(
            RagDocument(
                source="product",
                ref_id=str(product.id),
                title=product.name,
                content=text[:8000],
                product_id=product.id,
            )
        )
    return docs


def documents_from_reviews(reviews: list[ProductReview]) -> list[RagDocument]:
    docs: list[RagDocument] = []
    for review in reviews:
        if review.status != "published":
            continue
        title = review.title or f"{review.rating}-star review"
        text = (
            f"Review for {review.product_name} ({review.product_slug})\n"
            f"Rating: {review.rating}/5\n"
            f"{title}\n"
            f"{review.body}"
        )
        docs.append(
            RagDocument(
                source="review",
                ref_id=str(review.id),
                title=f"{review.product_name}: {title}"[:240],
                content=text[:8000],
                product_id=review.product_id,
            )
        )
    return docs


def documents_from_analytics(
    sales: list[SaleEvent], views: list[ViewEvent]
) -> list[RagDocument]:
    sales_summary = summarize(sales)
    views_summary = summarize_views(views)
    top = bestsellers(sales, metric="units", limit=12)
    top_lines = [
        f"- {row.product_name} ({row.product_slug}): {row.units_sold} units, "
        f"{row.revenue_minor} minor revenue"
        for row in top.items
    ]
    sales_text = "\n".join(
        [
            "Master sales analytics snapshot (recorded events).",
            f"Orders: {sales_summary.orders}",
            f"Lines: {sales_summary.lines}",
            f"Units sold: {sales_summary.units_sold}",
            f"Gross minor: {sales_summary.gross_minor}",
            f"Net minor: {sales_summary.net_minor}",
            f"Unique products: {sales_summary.unique_products}",
            "Bestsellers by units:",
            *(top_lines or ["- none"]),
        ]
    )
    views_text = "\n".join(
        [
            "Master traffic analytics snapshot (recorded events).",
            f"Visits: {views_summary.visits}",
            f"Product views: {views_summary.product_views}",
            f"Category views: {views_summary.category_views}",
            f"Listing views: {views_summary.listing_views}",
            f"Searches: {views_summary.searches}",
            f"Total events: {views_summary.total_events}",
            f"Unique products viewed: {views_summary.unique_products}",
        ]
    )
    return [
        RagDocument(
            source="analytics",
            ref_id="sales",
            title="Sales analytics",
            content=sales_text[:8000],
        ),
        RagDocument(
            source="analytics",
            ref_id="views",
            title="Traffic analytics",
            content=views_text[:8000],
        ),
    ]
