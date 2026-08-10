"""Sales analytics contracts shared by sandbox admin and master."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]


class SalesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SaleEvent(SalesModel):
    """One sold line — the atomic analytics fact.

    Orders are the commercial document; sale events are the analytics ledger.
    One checkout with three lines yields three events (plus void rows on cancel/refund).
    """

    id: UUID
    occurred_at: datetime
    recorded_at: datetime
    source: Literal["checkout", "admin", "import"] = "checkout"
    status: Literal["recorded", "voided"] = "recorded"

    order_id: UUID | None = None
    line_index: NonNegativeInt = 0

    product_id: UUID
    product_slug: str = Field(min_length=1, max_length=120)
    product_name: str = Field(min_length=1, max_length=200)
    category_id: UUID
    category_slug: str | None = Field(default=None, max_length=100)
    category_name: str | None = Field(default=None, max_length=160)

    variant_id: UUID
    variant_sku: str = Field(min_length=1, max_length=80)
    variant_name: str = Field(min_length=1, max_length=160)

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    quantity: PositiveInt
    list_unit_price_minor: NonNegativeInt
    unit_price_minor: NonNegativeInt
    line_gross_minor: NonNegativeInt
    allocated_discount_minor: NonNegativeInt = 0
    allocated_shipping_minor: NonNegativeInt = 0
    allocated_tax_minor: NonNegativeInt = 0
    line_net_minor: NonNegativeInt

    product_discount_percent: Annotated[int, Field(ge=0, le=100)] = 0
    coupon_code: str | None = Field(default=None, min_length=1, max_length=40)

    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=32)

    sandbox_session_id: str | None = Field(default=None, max_length=80)
    voided_at: datetime | None = None
    void_reason: str | None = Field(default=None, max_length=240)
    notes: str | None = Field(default=None, max_length=500)


class SaleCreate(SalesModel):
    """Admin/master seed or synthetic sale."""

    occurred_at: datetime | None = None
    source: Literal["admin", "import"] = "admin"
    order_id: UUID | None = None
    line_index: NonNegativeInt = 0
    product_id: UUID
    product_slug: str = Field(min_length=1, max_length=120)
    product_name: str = Field(min_length=1, max_length=200)
    category_id: UUID
    category_slug: str | None = Field(default=None, max_length=100)
    category_name: str | None = Field(default=None, max_length=160)
    variant_id: UUID
    variant_sku: str = Field(min_length=1, max_length=80)
    variant_name: str = Field(min_length=1, max_length=160)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    quantity: PositiveInt = 1
    list_unit_price_minor: NonNegativeInt
    unit_price_minor: NonNegativeInt
    allocated_discount_minor: NonNegativeInt = 0
    allocated_shipping_minor: NonNegativeInt = 0
    allocated_tax_minor: NonNegativeInt = 0
    product_discount_percent: Annotated[int, Field(ge=0, le=100)] = 0
    coupon_code: str | None = Field(default=None, min_length=1, max_length=40)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=500)


class SaleUpdate(SalesModel):
    occurred_at: datetime | None = None
    status: Literal["recorded", "voided"] | None = None
    quantity: PositiveInt | None = None
    list_unit_price_minor: NonNegativeInt | None = None
    unit_price_minor: NonNegativeInt | None = None
    allocated_discount_minor: NonNegativeInt | None = None
    allocated_shipping_minor: NonNegativeInt | None = None
    allocated_tax_minor: NonNegativeInt | None = None
    product_discount_percent: Annotated[int | None, Field(default=None, ge=0, le=100)] = None
    coupon_code: str | None = Field(default=None, min_length=1, max_length=40)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=32)
    void_reason: str | None = Field(default=None, max_length=240)
    notes: str | None = Field(default=None, max_length=500)


class SaleVoidRequest(SalesModel):
    reason: str | None = Field(default=None, max_length=240)


class SaleList(SalesModel):
    items: tuple[SaleEvent, ...]
    page: int
    page_size: int
    total: NonNegativeInt
    pages: NonNegativeInt


class SaleResponse(SalesModel):
    sale: SaleEvent


class SalesSummary(SalesModel):
    currency: str | None
    orders: NonNegativeInt
    lines: NonNegativeInt
    units_sold: NonNegativeInt
    gross_minor: NonNegativeInt
    discount_minor: NonNegativeInt
    shipping_minor: NonNegativeInt
    tax_minor: NonNegativeInt
    net_minor: NonNegativeInt
    average_order_minor: NonNegativeInt
    voided_lines: NonNegativeInt
    voided_net_minor: NonNegativeInt
    unique_products: NonNegativeInt
    unique_variants: NonNegativeInt
    unique_customers_geo: NonNegativeInt


class BestSellerRow(SalesModel):
    product_id: UUID
    product_slug: str
    product_name: str
    category_id: UUID
    category_name: str | None
    units_sold: NonNegativeInt
    revenue_minor: NonNegativeInt
    orders: NonNegativeInt
    average_unit_price_minor: NonNegativeInt


class BestSellers(SalesModel):
    metric: Literal["revenue", "units"]
    items: tuple[BestSellerRow, ...]


class SeriesPoint(SalesModel):
    bucket_start: datetime
    orders: NonNegativeInt
    units_sold: NonNegativeInt
    gross_minor: NonNegativeInt
    net_minor: NonNegativeInt


class SalesSeries(SalesModel):
    bucket: Literal["hour", "day"]
    points: tuple[SeriesPoint, ...]


SalesGroupBy = Literal["category", "coupon", "geo"]


class SalesBreakdownRow(SalesModel):
    """One bucket from a group-by breakdown. Dimension fields depend on `group_by`."""

    category_id: UUID | None = None
    category_slug: str | None = None
    category_name: str | None = None
    coupon_code: str | None = None
    country_code: str | None = None
    region: str | None = None
    city: str | None = None
    orders: NonNegativeInt
    lines: NonNegativeInt
    units_sold: NonNegativeInt
    discount_minor: NonNegativeInt
    net_minor: NonNegativeInt


class SalesBreakdown(SalesModel):
    group_by: SalesGroupBy
    items: tuple[SalesBreakdownRow, ...]


class SalesFeed(SalesModel):
    """Polling watch surface — newest recorded (and void) events since a cursor."""

    items: tuple[SaleEvent, ...]
    next_since: datetime | None
