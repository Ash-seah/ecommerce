"""Strict contracts for session-local catalog administration."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.catalog.schemas import (
    CatalogSnapshot,
    CategorySnapshot,
    MediaSnapshot,
    ProductSnapshot,
    VariantSnapshot,
)
from src.sandbox.models import CouponRecord


class AdminModel(BaseModel):
    # Non-strict so JSON request bodies can send UUID fields as strings.
    model_config = ConfigDict(extra="forbid")


class CategoryInput(AdminModel):
    parent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    sort_order: int = 0


class ProductInput(AdminModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "category_id": "00000000-0000-4000-8000-000000000001",
                    "brand": "Acme",
                    "name": "Sandbox Shirt",
                    "description": "Visible only in this anonymous sandbox.",
                    "details": "Cut for everyday wear with reinforced seams.",
                    "specifics": ["Cotton", "Machine washable"],
                }
            ]
        },
    )

    category_id: UUID
    brand: str | None = Field(default=None, min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    details: str | None = Field(default=None, max_length=20_000)
    specifics: tuple[Annotated[str, Field(min_length=1, max_length=80)], ...] = Field(
        default=(), max_length=50
    )
    discount_percent: Annotated[int, Field(ge=0, le=100)] = 0


class VariantInput(AdminModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "Sandbox Shirt / M",
                    "price_minor": 2499,
                    "currency": "USD",
                }
            ]
        },
    )

    name: str = Field(min_length=1, max_length=160)
    price_minor: Annotated[int, Field(ge=0, le=1_000_000_000)]
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class PriceAdjustment(AdminModel):
    price_minor: Annotated[int, Field(ge=0, le=1_000_000_000)]
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class InventoryAdjustment(AdminModel):
    operation: Literal["set", "increment"]
    quantity: Annotated[int, Field(ge=-1_000_000, le=1_000_000)]


class ActiveAdjustment(AdminModel):
    active: bool


class CouponInput(AdminModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "code": "DEMO20",
                    "kind": "percent",
                    "value": 20,
                    "minimum_subtotal_minor": 1000,
                    "maximum_discount_minor": 5000,
                    "active": True,
                }
            ]
        },
    )

    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["percent", "fixed"]
    value: Annotated[int, Field(gt=0, le=1_000_000_000)]
    minimum_subtotal_minor: Annotated[int, Field(ge=0, le=1_000_000_000)] = 0
    maximum_discount_minor: Annotated[int | None, Field(ge=0, le=1_000_000_000)] = None
    active: bool = True


class MediaUploadResponse(AdminModel):
    media: MediaSnapshot


class AdminCatalogResponse(AdminModel):
    catalog: CatalogSnapshot
    version: int


class CategoryResponse(AdminModel):
    category: CategorySnapshot
    version: int



class ProductResponse(AdminModel):
    product: ProductSnapshot
    version: int


class VariantResponse(AdminModel):
    variant: VariantSnapshot
    version: int


class CouponResponse(AdminModel):
    coupon: CouponRecord
    version: int


class CouponList(AdminModel):
    items: tuple[CouponRecord, ...]
