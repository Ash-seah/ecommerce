"""Build and mutate product reviews."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.reviews.schemas import ProductReview, ReviewCreate


def review_from_create(
    body: ReviewCreate,
    *,
    review_id: UUID | None = None,
    sandbox_session_id: str | None = None,
    order_id: UUID | None = None,
    source_override: str | None = None,
) -> ProductReview:
    now = datetime.now(UTC)
    return ProductReview(
        id=review_id or uuid4(),
        created_at=now,
        updated_at=now,
        source=source_override or body.source,  # type: ignore[arg-type]
        status=body.status,
        product_id=body.product_id,
        product_slug=body.product_slug,
        product_name=body.product_name,
        rating=body.rating,
        title=body.title,
        body=body.body,
        order_id=order_id if order_id is not None else body.order_id,
        sandbox_session_id=sandbox_session_id,
        author_label=body.author_label,
    )


def apply_review_update(review: ProductReview, updates: dict[str, object]) -> ProductReview:
    data = dict(updates)
    data["updated_at"] = datetime.now(UTC)
    return review.model_copy(update=data)
