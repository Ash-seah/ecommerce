"""Read-only access to the active master catalog."""

from datetime import UTC
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.catalog.models import CatalogRevision, MediaMetadata, Product
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
            url=self._media_url(media.object_key),
        )

    async def get_active_snapshot(self) -> CatalogSnapshot:
        statement = (
            select(CatalogRevision)
            .where(CatalogRevision.is_active.is_(True))
            .options(
                selectinload(CatalogRevision.categories),
                selectinload(CatalogRevision.products).selectinload(Product.variants),
                selectinload(CatalogRevision.products).selectinload(Product.media),
            )
        )
        async with self._sessions() as session:
            revision = await session.scalar(statement)
        if revision is None:
            raise CatalogNotAvailableError("no active catalog revision")

        categories = tuple(
            CategorySnapshot.model_validate(category)
            for category in sorted(
                (item for item in revision.categories if item.is_active),
                key=lambda item: (item.sort_order, item.slug, str(item.id)),
            )
        )
        products = tuple(
            ProductSnapshot(
                id=product.id,
                category_id=product.category_id,
                slug=product.slug,
                name=product.name,
                description=product.description,
                variants=tuple(
                    VariantSnapshot.model_validate(variant)
                    for variant in sorted(
                        (item for item in product.variants if item.is_active),
                        key=lambda item: (item.sku, str(item.id)),
                    )
                ),
                media=tuple(
                    self._media_snapshot(media)
                    for media in sorted(
                        (item for item in product.media if item.is_active),
                        key=lambda item: (item.sort_order, item.object_key, str(item.id)),
                    )
                ),
            )
            for product in sorted(
                (item for item in revision.products if item.is_active),
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
