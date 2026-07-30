"""Read-only access to the active master catalog."""

from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.catalog.models import CatalogRevision, Product
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

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

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
                    MediaSnapshot.model_validate(media)
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
