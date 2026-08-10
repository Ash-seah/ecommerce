"""Strict public commerce API contracts."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.catalog.schemas import MediaSnapshot
from src.reviews.schemas import StarCounts
from src.sandbox.models import AddressRecord, OrderRecord, WalletLedgerEntry

Minor = Annotated[int, Field(ge=0)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Stars = Annotated[int, Field(ge=1, le=5)]


class CommerceModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class VariantView(CommerceModel):
    id: UUID
    sku: str
    name: str
    list_price_minor: Minor
    price_minor: Minor
    currency: Currency
    stock: Minor
    available: bool
    media: tuple[MediaSnapshot, ...] = ()


class ProductView(CommerceModel):
    id: UUID
    category_id: UUID
    brand: str | None = None
    slug: str
    name: str
    description: str | None
    details: str | None = None
    specifics: tuple[str, ...] = ()
    discount_percent: Minor
    variants: tuple[VariantView, ...]
    media: tuple[MediaSnapshot, ...]
    available: bool
    stock: Minor
    price_min_minor: Minor
    price_max_minor: Minor
    currency: Currency
    average_rating: float | None = None
    rating_count: Minor = 0
    rounded_stars: Stars | None = None
    star_counts: StarCounts = Field(default_factory=StarCounts)
    can_review: bool = False
    my_review_id: UUID | None = None
    units_sold: Minor = 0


class CategoryNode(CommerceModel):
    """Category with nested children for storefront navigation trees."""

    id: UUID
    parent_id: UUID | None
    slug: str
    name: str
    description: str | None
    color: str | None = None
    accent_color: str | None = None
    sort_order: int
    media: tuple[MediaSnapshot, ...] = ()
    children: tuple["CategoryNode", ...] = ()


class CategoryPage(CommerceModel):
    items: tuple[CategoryNode, ...]
    page: int
    page_size: int
    total: Minor
    pages: Minor


class ProductPage(CommerceModel):
    items: tuple[ProductView, ...]
    page: int
    page_size: int
    total: Minor
    pages: Minor


class CartQuantityRequest(BaseModel):
    # Non-strict so JSON string UUIDs coerce; cart identity is variant_id only.
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "variant_id": "00000000-0000-4000-8000-0000000000aa",
                    "quantity": 2,
                }
            ]
        },
    )

    variant_id: UUID
    quantity: int = Field(ge=1)


class CartLineView(CommerceModel):
    variant_id: UUID
    product_name: str
    variant_name: str
    quantity: int
    list_price_minor: Minor
    unit_price_minor: Minor
    line_total_minor: Minor
    currency: Currency
    stock: Minor


class CartView(CommerceModel):
    lines: tuple[CartLineView, ...]
    item_count: Minor
    subtotal_minor: Minor
    currency: Currency


class WishlistRequest(BaseModel):
    # Non-strict so JSON string UUIDs coerce.
    model_config = ConfigDict(extra="forbid")

    product_id: UUID


class WishlistView(CommerceModel):
    items: tuple[ProductView, ...]


class AddressInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "label": "Home",
                    "recipient": "Demo User",
                    "line1": "1 Example Street",
                    "city": "London",
                    "postal_code": "SW1A 1AA",
                    "country_code": "GB",
                }
            ]
        },
    )

    label: str = Field(min_length=1, max_length=60)
    recipient: str = Field(min_length=1, max_length=120)
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    postal_code: str = Field(min_length=1, max_length=32)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")


class AddressList(CommerceModel):
    items: tuple[AddressRecord, ...]


class WalletView(CommerceModel):
    balance_minor: Minor
    currency: Currency


class WalletAdjustmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0, le=1_000_000_000)
    reference: str = Field(min_length=1, max_length=120)


class LedgerPage(CommerceModel):
    items: tuple[WalletLedgerEntry, ...]
    page: int
    page_size: int
    total: Minor
    pages: Minor


class PricingBreakdown(CommerceModel):
    currency: Currency
    subtotal_minor: Minor
    discount_minor: Minor
    shipping_minor: Minor
    tax_minor: Minor
    total_minor: Minor
    delivery_option_id: str
    delivery_option_label: str


class DeliveryOptionView(CommerceModel):
    id: str
    label: str
    description: str
    cost_minor: Minor
    eta_min_days: Minor
    eta_max_days: Minor
    free_shipping_applied: bool = False


class DeliveryOptionList(CommerceModel):
    items: tuple[DeliveryOptionView, ...]
    currency: Currency
    subtotal_minor: Minor
    discount_minor: Minor
    free_shipping_threshold_minor: Minor


class CheckoutRequest(BaseModel):
    # Non-strict so JSON string UUIDs coerce.
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "address_id": "00000000-0000-4000-8000-000000000001",
                    "delivery_option_id": "standard",
                    "coupon_code": "SAVE10",
                }
            ]
        },
    )

    address_id: UUID
    delivery_option_id: str = Field(min_length=1, max_length=40)
    coupon_code: str | None = Field(default=None, min_length=1, max_length=40)


class OrderPage(CommerceModel):
    items: tuple[OrderRecord, ...]
    page: int
    page_size: int
    total: Minor
    pages: Minor


class OrderTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["cancel", "refund"]
