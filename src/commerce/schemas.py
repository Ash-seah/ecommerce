"""Strict public commerce API contracts."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.catalog.schemas import CategorySnapshot, MediaSnapshot
from src.sandbox.models import AddressRecord, OrderRecord, WalletLedgerEntry

Minor = Annotated[int, Field(ge=0)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class CommerceModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class VariantView(CommerceModel):
    id: UUID
    sku: str
    name: str
    price_minor: Minor
    currency: Currency
    stock: Minor
    available: bool


class ProductView(CommerceModel):
    id: UUID
    category_id: UUID
    slug: str
    name: str
    description: str | None
    variants: tuple[VariantView, ...]
    media: tuple[MediaSnapshot, ...]
    available: bool
    price_min_minor: Minor
    price_max_minor: Minor
    currency: Currency


class CategoryPage(CommerceModel):
    items: tuple[CategorySnapshot, ...]
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


class CartQuantityRequest(CommerceModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        json_schema_extra={"examples": [{"sku": "TEE-CLASSIC-M", "quantity": 2}]},
    )

    sku: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1)


class CartLineView(CommerceModel):
    variant_id: UUID
    sku: str
    product_name: str
    variant_name: str
    quantity: int
    unit_price_minor: Minor
    line_total_minor: Minor
    currency: Currency
    stock: Minor


class CartView(CommerceModel):
    lines: tuple[CartLineView, ...]
    item_count: Minor
    subtotal_minor: Minor
    currency: Currency


class WishlistRequest(CommerceModel):
    product_id: UUID


class WishlistView(CommerceModel):
    items: tuple[ProductView, ...]


class AddressInput(CommerceModel):
    model_config = ConfigDict(
        strict=True,
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


class WalletAdjustmentRequest(CommerceModel):
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


class CheckoutRequest(CommerceModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "address_id": "00000000-0000-4000-8000-000000000001",
                    "coupon_code": "SAVE10",
                }
            ]
        },
    )

    address_id: UUID
    coupon_code: str | None = Field(default=None, min_length=1, max_length=40)


class OrderPage(CommerceModel):
    items: tuple[OrderRecord, ...]
    page: int
    page_size: int
    total: Minor
    pages: Minor


class OrderTransitionRequest(CommerceModel):
    action: Literal["cancel", "refund"]
