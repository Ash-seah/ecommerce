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
    model_config = ConfigDict(strict=True, extra="forbid")


class CategoryInput(AdminModel):
    parent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    sort_order: int = 0


class ProductInput(AdminModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "category_id": "00000000-0000-4000-8000-000000000001",
                    "name": "Sandbox Shirt",
                    "description": "Visible only in this anonymous sandbox.",
                }
            ]
        },
    )

    category_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


class VariantInput(AdminModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "sku": "SANDBOX-SHIRT-M",
                    "name": "Sandbox Shirt / M",
                    "price_minor": 2499,
                    "currency": "USD",
                }
            ]
        },
    )

    sku: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
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
        strict=True,
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
