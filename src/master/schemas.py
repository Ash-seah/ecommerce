"""Request/response contracts for master-catalog administration."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.catalog.schemas import (
    CategorySnapshot,
    MediaSnapshot,
    ProductSnapshot,
    VariantSnapshot,
)


class MasterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(MasterModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(MasterModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CategoryCreate(MasterModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    parent_id: UUID | None = None
    sort_order: int = Field(default=0, ge=0, le=1_000_000)
    is_active: bool = True


class CategoryUpdate(MasterModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    parent_id: UUID | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1_000_000)
    is_active: bool | None = None


class ProductCreate(MasterModel):
    category_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    discount_percent: Annotated[int, Field(ge=0, le=100)] = 0
    is_active: bool = True


class ProductUpdate(MasterModel):
    category_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    discount_percent: Annotated[int | None, Field(default=None, ge=0, le=100)] = None
    is_active: bool | None = None


class VariantCreate(MasterModel):
    product_id: UUID
    name: str = Field(min_length=1, max_length=160)
    price_minor: Annotated[int, Field(ge=0, le=1_000_000_000)]
    currency: str = Field(default="IRR", pattern=r"^[A-Z]{3}$")
    is_active: bool = True


class VariantUpdate(MasterModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    price_minor: Annotated[int | None, Field(default=None, ge=0, le=1_000_000_000)]
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    is_active: bool | None = None


class PublishResponse(MasterModel):
    revision_number: int
    revision_label: str
    product_count: int
    category_count: int


class CategoryResponse(MasterModel):
    category: CategorySnapshot


class ProductResponse(MasterModel):
    product: ProductSnapshot


class VariantResponse(MasterModel):
    variant: VariantSnapshot


class MediaResponse(MasterModel):
    media: MediaSnapshot
