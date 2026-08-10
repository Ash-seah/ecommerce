"""Product review contracts for storefront, sandbox admin, and master."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NonNegativeInt = Annotated[int, Field(ge=0)]
Rating = Annotated[int, Field(ge=1, le=5)]


class ReviewsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductReview(ReviewsModel):
    """One verified-buyer review for a product."""

    id: UUID
    created_at: datetime
    updated_at: datetime
    source: Literal["checkout", "admin", "import"] = "checkout"
    status: Literal["published", "hidden"] = "published"

    product_id: UUID
    product_slug: str = Field(min_length=1, max_length=120)
    product_name: str = Field(min_length=1, max_length=200)

    rating: Rating
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4000)

    order_id: UUID | None = None
    sandbox_session_id: str | None = Field(default=None, max_length=80)
    author_label: str = Field(default="Verified buyer", min_length=1, max_length=80)


class ReviewCreateRequest(ReviewsModel):
    """Shopper create — product comes from the path; purchase is verified server-side."""

    rating: Rating
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4000)


class ReviewCreate(ReviewsModel):
    """Admin/master seed or import."""

    product_id: UUID
    product_slug: str = Field(min_length=1, max_length=120)
    product_name: str = Field(min_length=1, max_length=200)
    rating: Rating
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4000)
    order_id: UUID | None = None
    source: Literal["checkout", "admin", "import"] = "admin"
    status: Literal["published", "hidden"] = "published"
    author_label: str = Field(default="Verified buyer", min_length=1, max_length=80)


class ReviewUpdate(ReviewsModel):
    rating: Rating | None = None
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    status: Literal["published", "hidden"] | None = None
    author_label: str | None = Field(default=None, min_length=1, max_length=80)


class ReviewResponse(ReviewsModel):
    review: ProductReview


class StarCounts(ReviewsModel):
    """Histogram of published star ratings (1★ … 5★)."""

    one: NonNegativeInt = 0
    two: NonNegativeInt = 0
    three: NonNegativeInt = 0
    four: NonNegativeInt = 0
    five: NonNegativeInt = 0


class ReviewList(ReviewsModel):
    items: tuple[ProductReview, ...]
    page: int
    page_size: int
    total: NonNegativeInt
    pages: NonNegativeInt
    average_rating: float | None = None
    rating_count: NonNegativeInt = 0
    rounded_stars: int | None = Field(default=None, ge=1, le=5)
    star_counts: StarCounts = Field(default_factory=StarCounts)


class ProductRatingSummary(ReviewsModel):
    average_rating: float | None = None
    rating_count: NonNegativeInt = 0
    rounded_stars: int | None = Field(default=None, ge=1, le=5)
    star_counts: StarCounts = Field(default_factory=StarCounts)
    can_review: bool = False
    my_review_id: UUID | None = None
