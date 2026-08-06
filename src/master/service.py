"""Owner-role mutations against the active master catalog revision."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.catalog.cache import CatalogSnapshotCache
from src.catalog.models import CatalogRevision, Category, MediaMetadata, Product, ProductVariant
from src.catalog.schemas import (
    CategorySnapshot,
    MediaSnapshot,
    ProductSnapshot,
    VariantSnapshot,
)
from src.infrastructure.minio import MediaService
from src.master.schemas import (
    CategoryCreate,
    CategoryUpdate,
    ProductCreate,
    ProductUpdate,
    PublishResponse,
    VariantCreate,
    VariantUpdate,
)


class MasterError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class MasterCatalogService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        media: MediaService,
        catalog_cache: CatalogSnapshotCache,
    ) -> None:
        self._sessions = sessions
        self._media = media
        self._cache = catalog_cache

    async def _active_revision(self, session: AsyncSession) -> CatalogRevision:
        revision = await session.scalar(
            select(CatalogRevision).where(CatalogRevision.is_active.is_(True))
        )
        if revision is None:
            revision = CatalogRevision(
                id=uuid4(),
                revision_number=1,
                label="Master catalog",
                is_active=True,
            )
            session.add(revision)
            await session.flush()
        return revision

    async def create_category(self, body: CategoryCreate) -> CategorySnapshot:
        async with self._sessions.begin() as session:
            revision = await self._active_revision(session)
            if body.parent_id is not None:
                parent = await session.get(Category, body.parent_id)
                if parent is None or parent.revision_id != revision.id:
                    raise MasterError(404, "parent_not_found", "Parent category was not found")
            exists = await session.scalar(
                select(Category.id).where(
                    Category.revision_id == revision.id, Category.slug == body.slug
                )
            )
            if exists is not None:
                raise MasterError(409, "slug_conflict", "Category slug already exists")
            category = Category(
                id=uuid4(),
                revision_id=revision.id,
                parent_id=body.parent_id,
                slug=body.slug,
                name=body.name,
                description=body.description,
                sort_order=body.sort_order,
                is_active=body.is_active,
            )
            session.add(category)
            await session.flush()
            snapshot = CategorySnapshot.model_validate(category)
        await self._cache.refresh()
        return snapshot

    async def update_category(self, category_id: UUID, body: CategoryUpdate) -> CategorySnapshot:
        async with self._sessions.begin() as session:
            category = await session.get(Category, category_id)
            if category is None:
                raise MasterError(404, "category_not_found", "Category was not found")
            data = body.model_dump(exclude_unset=True)
            if "slug" in data and data["slug"] != category.slug:
                exists = await session.scalar(
                    select(Category.id).where(
                        Category.revision_id == category.revision_id,
                        Category.slug == data["slug"],
                        Category.id != category_id,
                    )
                )
                if exists is not None:
                    raise MasterError(409, "slug_conflict", "Category slug already exists")
            if "parent_id" in data and data["parent_id"] is not None:
                if data["parent_id"] == category_id:
                    raise MasterError(422, "invalid_parent", "Category cannot parent itself")
                parent = await session.get(Category, data["parent_id"])
                if parent is None or parent.revision_id != category.revision_id:
                    raise MasterError(404, "parent_not_found", "Parent category was not found")
            for key, value in data.items():
                setattr(category, key, value)
            await session.flush()
            snapshot = CategorySnapshot.model_validate(category)
        await self._cache.refresh()
        return snapshot

    async def create_product(self, body: ProductCreate) -> ProductSnapshot:
        async with self._sessions.begin() as session:
            revision = await self._active_revision(session)
            category = await session.get(Category, body.category_id)
            if category is None or category.revision_id != revision.id:
                raise MasterError(404, "category_not_found", "Category was not found")
            exists = await session.scalar(
                select(Product.id).where(
                    Product.revision_id == revision.id, Product.slug == body.slug
                )
            )
            if exists is not None:
                raise MasterError(409, "slug_conflict", "Product slug already exists")
            product = Product(
                id=uuid4(),
                revision_id=revision.id,
                category_id=body.category_id,
                slug=body.slug,
                name=body.name,
                description=body.description,
                is_active=body.is_active,
            )
            session.add(product)
            await session.flush()
            snapshot = ProductSnapshot(
                id=product.id,
                category_id=product.category_id,
                slug=product.slug,
                name=product.name,
                description=product.description,
                variants=(),
                media=(),
            )
        await self._cache.refresh()
        return snapshot

    async def update_product(self, product_id: UUID, body: ProductUpdate) -> ProductSnapshot:
        async with self._sessions.begin() as session:
            product = await session.get(Product, product_id)
            if product is None:
                raise MasterError(404, "product_not_found", "Product was not found")
            data = body.model_dump(exclude_unset=True)
            if "category_id" in data:
                category = await session.get(Category, data["category_id"])
                if category is None or category.revision_id != product.revision_id:
                    raise MasterError(404, "category_not_found", "Category was not found")
            if "slug" in data and data["slug"] != product.slug:
                exists = await session.scalar(
                    select(Product.id).where(
                        Product.revision_id == product.revision_id,
                        Product.slug == data["slug"],
                        Product.id != product_id,
                    )
                )
                if exists is not None:
                    raise MasterError(409, "slug_conflict", "Product slug already exists")
            for key, value in data.items():
                setattr(product, key, value)
            await session.flush()
            snapshot = ProductSnapshot(
                id=product.id,
                category_id=product.category_id,
                slug=product.slug,
                name=product.name,
                description=product.description,
                variants=(),
                media=(),
            )
        await self._cache.refresh()
        return snapshot

    async def create_variant(self, body: VariantCreate) -> VariantSnapshot:
        async with self._sessions.begin() as session:
            product = await session.get(Product, body.product_id)
            if product is None:
                raise MasterError(404, "product_not_found", "Product was not found")
            exists = await session.scalar(
                select(ProductVariant.id).where(
                    ProductVariant.product_id == body.product_id,
                    ProductVariant.sku == body.sku,
                )
            )
            if exists is not None:
                raise MasterError(409, "sku_conflict", "SKU already exists on this product")
            variant = ProductVariant(
                id=uuid4(),
                product_id=body.product_id,
                sku=body.sku,
                name=body.name,
                price_minor=body.price_minor,
                currency=body.currency,
                is_active=body.is_active,
            )
            session.add(variant)
            await session.flush()
            snapshot = VariantSnapshot.model_validate(variant)
        await self._cache.refresh()
        return snapshot

    async def update_variant(self, variant_id: UUID, body: VariantUpdate) -> VariantSnapshot:
        async with self._sessions.begin() as session:
            variant = await session.get(ProductVariant, variant_id)
            if variant is None:
                raise MasterError(404, "variant_not_found", "Variant was not found")
            data = body.model_dump(exclude_unset=True)
            if "sku" in data and data["sku"] != variant.sku:
                exists = await session.scalar(
                    select(ProductVariant.id).where(
                        ProductVariant.product_id == variant.product_id,
                        ProductVariant.sku == data["sku"],
                        ProductVariant.id != variant_id,
                    )
                )
                if exists is not None:
                    raise MasterError(409, "sku_conflict", "SKU already exists on this product")
            for key, value in data.items():
                setattr(variant, key, value)
            await session.flush()
            snapshot = VariantSnapshot.model_validate(variant)
        await self._cache.refresh()
        return snapshot

    async def attach_media(
        self,
        product_id: UUID,
        data: bytes,
        content_type: str | None,
        alt_text: str,
        sort_order: int,
    ) -> MediaSnapshot:
        uploaded = await self._media.upload_master(
            data, content_type, alt_text, sort_order, object_prefix="catalog/"
        )
        try:
            async with self._sessions.begin() as session:
                product = await session.get(Product, product_id)
                if product is None:
                    raise MasterError(404, "product_not_found", "Product was not found")
                row = MediaMetadata(
                    id=uploaded.id,
                    product_id=product_id,
                    object_key=uploaded.object_key,
                    content_type=uploaded.content_type,
                    alt_text=uploaded.alt_text,
                    byte_size=uploaded.byte_size,
                    sort_order=uploaded.sort_order,
                    is_active=True,
                )
                session.add(row)
                await session.flush()
        except Exception:
            await self._media.delete_master(uploaded.object_key)
            raise
        await self._cache.refresh()
        return uploaded

    async def delete_media(self, media_id: UUID) -> None:
        object_key: str | None = None
        async with self._sessions.begin() as session:
            media = await session.get(MediaMetadata, media_id)
            if media is None:
                raise MasterError(404, "media_not_found", "Media was not found")
            object_key = media.object_key
            await session.delete(media)
        if object_key is not None:
            await self._media.delete_master(object_key)
        await self._cache.refresh()

    async def publish(self) -> PublishResponse:
        snapshot = await self._cache.refresh()
        return PublishResponse(
            revision_number=snapshot.revision_number,
            revision_label=snapshot.revision_label,
            product_count=len(snapshot.products),
            category_count=len(snapshot.categories),
        )
