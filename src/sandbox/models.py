"""Strict Redis-backed sandbox state contracts."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.catalog.schemas import (
    CategorySnapshot,
    MediaSnapshot,
    ProductSnapshot,
    VariantSnapshot,
)

NonNegativeInt = Annotated[int, Field(ge=0)]


class SandboxModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class CategoryOverlay(SandboxModel):
    parent_id: UUID | None = None
    slug: str | None = None
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None


class VariantOverlay(SandboxModel):
    sku: str | None = None
    name: str | None = None
    price_minor: NonNegativeInt | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class ProductOverlay(SandboxModel):
    category_id: UUID | None = None
    slug: str | None = None
    name: str | None = None
    description: str | None = None
    media: tuple[MediaSnapshot, ...] | None = None


class CustomVariant(SandboxModel):
    product_id: UUID
    variant: VariantSnapshot


class CouponRecord(SandboxModel):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Z0-9_-]+$")
    kind: Literal["percent", "fixed"]
    value: Annotated[int, Field(gt=0)]
    minimum_subtotal_minor: NonNegativeInt = 0
    maximum_discount_minor: NonNegativeInt | None = None
    active: bool = True


class CartLine(SandboxModel):
    variant_id: UUID
    quantity: Annotated[int, Field(gt=0)]


class CartState(SandboxModel):
    lines: list[CartLine] = Field(default_factory=list)


class WishlistState(SandboxModel):
    product_ids: set[UUID] = Field(default_factory=set)


class AddressRecord(SandboxModel):
    id: UUID
    label: str = Field(min_length=1, max_length=60)
    recipient: str = Field(min_length=1, max_length=120)
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    postal_code: str = Field(min_length=1, max_length=32)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")


class AddressState(SandboxModel):
    addresses: dict[UUID, AddressRecord] = Field(default_factory=dict)


class OrderLineSnapshot(SandboxModel):
    variant_id: UUID
    sku: str = Field(min_length=1, max_length=80)
    product_name: str = Field(min_length=1, max_length=200)
    variant_name: str = Field(min_length=1, max_length=160)
    quantity: Annotated[int, Field(gt=0)]
    unit_price_minor: NonNegativeInt
    line_total_minor: NonNegativeInt


class OrderRecord(SandboxModel):
    id: UUID
    status: Literal["placed", "cancelled", "refunded"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    lines: tuple[OrderLineSnapshot, ...]
    subtotal_minor: NonNegativeInt
    discount_minor: NonNegativeInt
    shipping_minor: NonNegativeInt
    tax_minor: NonNegativeInt
    total_minor: NonNegativeInt
    address: AddressRecord
    coupon_code: str | None = Field(default=None, min_length=1, max_length=40)
    created_at: datetime
    updated_at: datetime


class OrderState(SandboxModel):
    orders: dict[UUID, OrderRecord] = Field(default_factory=dict)
    idempotency_keys: dict[str, UUID] = Field(default_factory=dict)


class WalletLedgerEntry(SandboxModel):
    id: UUID
    amount_minor: int
    balance_after_minor: NonNegativeInt
    kind: Literal[
        "initial_credit",
        "admin_credit",
        "admin_debit",
        "checkout_debit",
        "cancellation_credit",
        "refund_credit",
    ]
    reference: str = Field(min_length=1, max_length=120)
    created_at: datetime


class InventoryLedgerEntry(SandboxModel):
    id: UUID
    variant_id: UUID
    sku: str = Field(min_length=1, max_length=80)
    quantity_delta: int
    stock_after: NonNegativeInt
    kind: Literal["checkout_decrement", "cancellation_restock", "refund_restock"]
    reference: str = Field(min_length=1, max_length=120)
    created_at: datetime


class WalletState(SandboxModel):
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    balance_minor: NonNegativeInt = 0
    ledger: list[WalletLedgerEntry] = Field(default_factory=list)


class SandboxState(SandboxModel):
    """Complete isolated state persisted as one optimistic-locking document."""

    schema_version: Annotated[int, Field(ge=1)] = 1
    version: NonNegativeInt = 0
    pinned_master_revision: Annotated[int, Field(gt=0)]
    created_at: datetime
    updated_at: datetime
    csrf_nonce_hash: str = Field(min_length=64, max_length=64)
    category_overlays: dict[UUID, CategoryOverlay] = Field(default_factory=dict)
    product_overlays: dict[UUID, ProductOverlay] = Field(default_factory=dict)
    variant_overlays: dict[UUID, VariantOverlay] = Field(default_factory=dict)
    custom_categories: dict[UUID, CategorySnapshot] = Field(default_factory=dict)
    custom_products: dict[UUID, ProductSnapshot] = Field(default_factory=dict)
    custom_variants: dict[UUID, CustomVariant] = Field(default_factory=dict)
    category_tombstones: set[UUID] = Field(default_factory=set)
    product_tombstones: set[UUID] = Field(default_factory=set)
    variant_tombstones: set[UUID] = Field(default_factory=set)
    stock_overrides: dict[UUID, NonNegativeInt] = Field(default_factory=dict)
    coupons: dict[str, CouponRecord] = Field(default_factory=dict)
    owned_media: dict[UUID, MediaSnapshot] = Field(default_factory=dict)
    cart: CartState = Field(default_factory=CartState)
    wishlist: WishlistState = Field(default_factory=WishlistState)
    addresses: AddressState = Field(default_factory=AddressState)
    orders: OrderState = Field(default_factory=OrderState)
    wallet: WalletState = Field(default_factory=WalletState)
    inventory_ledger: list[InventoryLedgerEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_document_invariants(self) -> Self:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("sandbox timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if any(key != value.id for key, value in self.custom_categories.items()):
            raise ValueError("custom category map keys must match entity IDs")
        if any(key != value.id for key, value in self.custom_products.items()):
            raise ValueError("custom product map keys must match entity IDs")
        if any(key != value.variant.id for key, value in self.custom_variants.items()):
            raise ValueError("custom variant map keys must match entity IDs")
        if any(key != value.code for key, value in self.coupons.items()):
            raise ValueError("coupon map keys must match normalized codes")
        if any(key != value.id for key, value in self.owned_media.items()):
            raise ValueError("owned media map keys must match entity IDs")
        return self
