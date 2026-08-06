"""Strict, immutable catalog snapshot contracts."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NonEmptyText = Annotated[str, Field(min_length=1)]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class SnapshotModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, from_attributes=True)


class MediaSnapshot(SnapshotModel):
    id: UUID
    object_key: NonEmptyText
    content_type: NonEmptyText
    alt_text: NonEmptyText
    byte_size: Annotated[int, Field(ge=0)]
    sort_order: Annotated[int, Field(ge=0)]
    is_main: bool = False
    url: str | None = None


class VariantSnapshot(SnapshotModel):
    id: UUID
    sku: NonEmptyText
    name: NonEmptyText
    price_minor: Annotated[int, Field(ge=0)]
    currency: CurrencyCode
    is_active: bool = True
    media: tuple[MediaSnapshot, ...] = ()


class ProductSnapshot(SnapshotModel):
    id: UUID
    category_id: UUID
    slug: NonEmptyText
    name: NonEmptyText
    description: str | None
    discount_percent: Annotated[int, Field(ge=0, le=100)] = 0
    is_active: bool = True
    variants: tuple[VariantSnapshot, ...]
    media: tuple[MediaSnapshot, ...]


class CategorySnapshot(SnapshotModel):
    id: UUID
    parent_id: UUID | None
    slug: NonEmptyText
    name: NonEmptyText
    description: str | None
    sort_order: int
    is_active: bool = True


class CatalogSnapshot(SnapshotModel):
    revision_id: UUID
    revision_number: Annotated[int, Field(gt=0)]
    revision_label: NonEmptyText
    generated_at: datetime
    categories: tuple[CategorySnapshot, ...]
    products: tuple[ProductSnapshot, ...]


def media_sort_key(item: MediaSnapshot) -> tuple[bool, int, str]:
    return (not item.is_main, item.sort_order, str(item.id))


def with_media_appended(
    existing: tuple[MediaSnapshot, ...], media: MediaSnapshot
) -> tuple[MediaSnapshot, ...]:
    """Append media, clearing other main flags when the new item is main."""

    base = existing
    if media.is_main:
        base = tuple(
            item.model_copy(update={"is_main": False}) if item.is_main else item for item in existing
        )
    return tuple(sorted((*base, media), key=media_sort_key))


def with_main_media(
    items: tuple[MediaSnapshot, ...], media_id: UUID
) -> tuple[MediaSnapshot, ...] | None:
    """Mark one item as main; returns None if media_id is not in the list."""

    if not any(item.id == media_id for item in items):
        return None
    return tuple(
        sorted(
            (item.model_copy(update={"is_main": item.id == media_id}) for item in items),
            key=media_sort_key,
        )
    )