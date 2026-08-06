"""Traffic / visit / view analytics contracts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NonNegativeInt = Annotated[int, Field(ge=0)]

ViewKind = Literal[
    "visit",
    "product_view",
    "category_view",
    "listing_view",
    "search",
]


class ViewsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ViewEvent(ViewsModel):
    """One visit or view — the atomic traffic analytics fact."""

    id: UUID
    occurred_at: datetime
    recorded_at: datetime
    source: Literal["client", "auto", "admin", "import"] = "client"
    status: Literal["recorded", "voided"] = "recorded"
    kind: ViewKind

    path: str | None = Field(default=None, max_length=500)
    referrer: str | None = Field(default=None, max_length=500)
    query: str | None = Field(default=None, max_length=240)

    product_id: UUID | None = None
    product_slug: str | None = Field(default=None, max_length=120)
    product_name: str | None = Field(default=None, max_length=200)
    category_id: UUID | None = None
    category_slug: str | None = Field(default=None, max_length=100)
    category_name: str | None = Field(default=None, max_length=160)

    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)

    user_agent: str | None = Field(default=None, max_length=400)
    sandbox_session_id: str | None = Field(default=None, max_length=80)
    voided_at: datetime | None = None
    void_reason: str | None = Field(default=None, max_length=240)
    notes: str | None = Field(default=None, max_length=500)


class ViewCreate(ViewsModel):
    occurred_at: datetime | None = None
    source: Literal["client", "admin", "import"] = "admin"
    kind: ViewKind
    path: str | None = Field(default=None, max_length=500)
    referrer: str | None = Field(default=None, max_length=500)
    query: str | None = Field(default=None, max_length=240)
    product_id: UUID | None = None
    product_slug: str | None = Field(default=None, max_length=120)
    product_name: str | None = Field(default=None, max_length=200)
    category_id: UUID | None = None
    category_slug: str | None = Field(default=None, max_length=100)
    category_name: str | None = Field(default=None, max_length=160)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    user_agent: str | None = Field(default=None, max_length=400)
    notes: str | None = Field(default=None, max_length=500)


class ViewRecordRequest(ViewsModel):
    """Shopper-facing beacon to log a visit/view from the storefront."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "kind": "visit",
                    "path": "/",
                    "referrer": "https://google.com",
                },
                {
                    "kind": "product_view",
                    "product_id": "00000000-0000-4000-8000-0000000000aa",
                    "path": "/products/shoe",
                },
            ]
        },
    )

    kind: ViewKind
    path: str | None = Field(default=None, max_length=500)
    referrer: str | None = Field(default=None, max_length=500)
    query: str | None = Field(default=None, max_length=240)
    product_id: UUID | None = None
    category_id: UUID | None = None
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)


class ViewUpdate(ViewsModel):
    occurred_at: datetime | None = None
    status: Literal["recorded", "voided"] | None = None
    kind: ViewKind | None = None
    path: str | None = Field(default=None, max_length=500)
    referrer: str | None = Field(default=None, max_length=500)
    query: str | None = Field(default=None, max_length=240)
    product_id: UUID | None = None
    product_slug: str | None = Field(default=None, max_length=120)
    product_name: str | None = Field(default=None, max_length=200)
    category_id: UUID | None = None
    category_slug: str | None = Field(default=None, max_length=100)
    category_name: str | None = Field(default=None, max_length=160)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    user_agent: str | None = Field(default=None, max_length=400)
    void_reason: str | None = Field(default=None, max_length=240)
    notes: str | None = Field(default=None, max_length=500)


class ViewVoidRequest(ViewsModel):
    reason: str | None = Field(default=None, max_length=240)


class ViewList(ViewsModel):
    items: tuple[ViewEvent, ...]
    page: int
    page_size: int
    total: NonNegativeInt
    pages: NonNegativeInt


class ViewResponse(ViewsModel):
    view: ViewEvent


class ViewsSummary(ViewsModel):
    visits: NonNegativeInt
    product_views: NonNegativeInt
    category_views: NonNegativeInt
    listing_views: NonNegativeInt
    searches: NonNegativeInt
    total_events: NonNegativeInt
    voided_events: NonNegativeInt
    unique_products: NonNegativeInt
    unique_categories: NonNegativeInt
    unique_paths: NonNegativeInt
    unique_sessions: NonNegativeInt


class ViewsSeriesPoint(ViewsModel):
    bucket_start: datetime
    visits: NonNegativeInt
    product_views: NonNegativeInt
    category_views: NonNegativeInt
    listing_views: NonNegativeInt
    searches: NonNegativeInt
    total: NonNegativeInt


class ViewsSeries(ViewsModel):
    bucket: Literal["hour", "day"]
    points: tuple[ViewsSeriesPoint, ...]


class TopViewedProduct(ViewsModel):
    product_id: UUID
    product_slug: str | None
    product_name: str | None
    views: NonNegativeInt


class TopViewedProducts(ViewsModel):
    items: tuple[TopViewedProduct, ...]


class TopViewedCategory(ViewsModel):
    category_id: UUID
    category_slug: str | None
    category_name: str | None
    views: NonNegativeInt


class TopViewedCategories(ViewsModel):
    items: tuple[TopViewedCategory, ...]


class TopPath(ViewsModel):
    path: str
    hits: NonNegativeInt


class TopPaths(ViewsModel):
    items: tuple[TopPath, ...]


class ViewsByKindRow(ViewsModel):
    kind: ViewKind
    count: NonNegativeInt


class ViewsByKind(ViewsModel):
    items: tuple[ViewsByKindRow, ...]


class ViewsFeed(ViewsModel):
    items: tuple[ViewEvent, ...]
    next_since: datetime | None
