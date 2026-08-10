"""Read-only access to the active master catalog."""

from datetime import UTC
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.catalog.models import (
    CatalogRevision,
    Category,
    MediaMetadata,
    Product,
    ProductVariant,
)
from src.catalog.schemas import (
    CatalogSnapshot,
    CategorySnapshot,
    MediaSnapshot,
    ProductSnapshot,
    VariantSnapshot,
)


class CatalogNotAvailableError(LookupError):
    """Raised when no active catalog revision exists."""


class MasterCatalogRepository:
    """Build snapshots without exposing write operations."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        media_public_base_url: str | None = None,
        master_bucket: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._media_base_url = (
            media_public_base_url.rstrip("/") if media_public_base_url else None
        )
        self._master_bucket = master_bucket

    def _media_url(self, object_key: str) -> str | None:
        if self._media_base_url is None or self._master_bucket is None:
            return None
        bucket = quote(self._master_bucket, safe="")
        key = quote(object_key, safe="/")
        return f"{self._media_base_url}/{bucket}/{key}"

    def _media_snapshot(self, media: MediaMetadata) -> MediaSnapshot:
        return MediaSnapshot(
            id=media.id,
            object_key=media.object_key,
            content_type=media.content_type,
            alt_text=media.alt_text,
            byte_size=media.byte_size,
            sort_order=media.sort_order,
            is_main=media.is_main,
            url=self._media_url(media.object_key),
        )

    def _media_list(self, items: list[MediaMetadata]) -> tuple[MediaSnapshot, ...]:
        return tuple(
            self._media_snapshot(media)
            for media in sorted(
                (item for item in items if item.is_active),
                key=lambda item: (
                    not item.is_main,
                    item.sort_order,
                    item.object_key,
                    str(item.id),
                ),
            )
        )

    async def get_active_snapshot(self) -> CatalogSnapshot:
        statement = (
            select(CatalogRevision)
            .where(CatalogRevision.is_active.is_(True))
            .options(
                selectinload(CatalogRevision.categories).selectinload(Category.media),
                selectinload(CatalogRevision.products)
                .selectinload(Product.variants)
                .selectinload(ProductVariant.media),
                selectinload(CatalogRevision.products).selectinload(Product.media),
            )
        )
        async with self._sessions() as session:
            revision = await session.scalar(statement)
        if revision is None:
            raise CatalogNotAvailableError("no active catalog revision")

        categories = tuple(
            CategorySnapshot(
                id=category.id,
                parent_id=category.parent_id,
                slug=category.slug,
                name=category.name,
                description=category.description,
                color=category.color,
                accent_color=category.accent_color,
                sort_order=category.sort_order,
                is_active=category.is_active,
                media=self._media_list(list(category.media)),
            )
            for category in sorted(
                revision.categories,
                key=lambda item: (item.sort_order, item.slug, str(item.id)),
            )
        )
        products = tuple(
            ProductSnapshot(
                id=product.id,
                category_id=product.category_id,
                brand=product.brand,
                slug=product.slug,
                name=product.name,
                description=product.description,
                details=product.details,
                specifics=tuple(product.specifics or ()),
                discount_percent=product.discount_percent,
                is_active=product.is_active,
                variants=tuple(
                    VariantSnapshot(
                        id=variant.id,
                        sku=variant.sku,
                        name=variant.name,
                        price_minor=variant.price_minor,
                        currency=variant.currency,
                        is_active=variant.is_active,
                        media=self._media_list(list(variant.media)),
                    )
                    for variant in sorted(
                        product.variants,
                        key=lambda item: (item.sku, str(item.id)),
                    )
                ),
                media=self._media_list(
                    [item for item in product.media if item.variant_id is None]
                ),
            )
            for product in sorted(
                revision.products,
                key=lambda item: (item.slug, str(item.id)),
            )
        )
        return CatalogSnapshot(
            revision_id=revision.id,
            revision_number=revision.revision_number,
            revision_label=revision.label,
            generated_at=revision.updated_at.astimezone(UTC),
            categories=categories,
            products=products,
        )
