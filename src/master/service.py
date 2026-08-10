"""Owner-role mutations against the active master catalog revision."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.catalog.cache import CatalogSnapshotCache
from src.catalog.ids import short_uuid
from src.catalog.models import CatalogRevision, Category, MediaMetadata, Product, ProductVariant
from src.catalog.schemas import (
    CatalogSnapshot,
    CategorySnapshot,
    MediaSnapshot,
    ProductSnapshot,
    VariantSnapshot,
)
from src.infrastructure.minio import MediaService
from src.master.schemas import (
    CategoryCreate,
    CategoryUpdate,
    MediaUpdate,
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


def _variant_snapshot(variant: ProductVariant) -> VariantSnapshot:
    return VariantSnapshot(
        id=variant.id,
        sku=variant.sku,
        name=variant.name,
        price_minor=variant.price_minor,
        currency=variant.currency,
        is_active=variant.is_active,
        media=(),
    )


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

    async def _unique_slug(
        self,
        session: AsyncSession,
        model: type[Category] | type[Product],
        revision_id: UUID,
    ) -> str:
        for _ in range(8):
            candidate = short_uuid()
            exists = await session.scalar(
                select(model.id).where(model.revision_id == revision_id, model.slug == candidate)
            )
            if exists is None:
                return candidate
        raise MasterError(500, "slug_allocate_failed", "Could not allocate a unique short id")

    async def _unique_variant_code(self, session: AsyncSession, product_id: UUID) -> str:
        for _ in range(8):
            candidate = short_uuid()
            exists = await session.scalar(
                select(ProductVariant.id).where(
                    ProductVariant.product_id == product_id,
                    ProductVariant.sku == candidate,
                )
            )
            if exists is None:
                return candidate
        raise MasterError(500, "sku_allocate_failed", "Could not allocate a unique short id")

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

    async def get_catalog(self) -> CatalogSnapshot:
        snapshot = await self._cache.get()
        if snapshot is None:
            return await self._cache.refresh()
        return snapshot

    async def get_category(self, category_id: UUID) -> CategorySnapshot:
        catalog = await self.get_catalog()
        for category in catalog.categories:
            if category.id == category_id:
                return category
        raise MasterError(404, "category_not_found", "Category was not found")


    async def get_product(self, product_id: UUID) -> ProductSnapshot:
        catalog = await self.get_catalog()
        for product in catalog.products:
            if product.id == product_id:
                return product
        raise MasterError(404, "product_not_found", "Product was not found")

    async def get_variant(self, variant_id: UUID) -> VariantSnapshot:
        catalog = await self.get_catalog()
        for product in catalog.products:
            for variant in product.variants:
                if variant.id == variant_id:
                    return variant
        raise MasterError(404, "variant_not_found", "Variant was not found")

    async def get_media(self, media_id: UUID) -> MediaSnapshot:
        catalog = await self.get_catalog()
        for category in catalog.categories:
            for media in category.media:
                if media.id == media_id:
                    return media
        for product in catalog.products:
            for media in product.media:
                if media.id == media_id:
                    return media
            for variant in product.variants:
                for media in variant.media:
                    if media.id == media_id:
                        return media
        raise MasterError(404, "media_not_found", "Media was not found")

    @staticmethod
    def _category_snapshot(category: Category, media: tuple[MediaSnapshot, ...] = ()) -> CategorySnapshot:
        return CategorySnapshot(
            id=category.id,
            parent_id=category.parent_id,
            slug=category.slug,
            name=category.name,
            description=category.description,
            color=category.color,
            accent_color=category.accent_color,
            sort_order=category.sort_order,
            is_active=category.is_active,
            media=media,
        )

    async def create_category(self, body: CategoryCreate) -> CategorySnapshot:
        async with self._sessions.begin() as session:
            revision = await self._active_revision(session)
            if body.parent_id is not None:
                parent = await session.get(Category, body.parent_id)
                if parent is None or parent.revision_id != revision.id:
                    raise MasterError(404, "parent_not_found", "Parent category was not found")
            category = Category(
                id=uuid4(),
                revision_id=revision.id,
                parent_id=body.parent_id,
                slug=await self._unique_slug(session, Category, revision.id),
                name=body.name,
                description=body.description,
                color=body.color,
                accent_color=body.accent_color,
                sort_order=body.sort_order,
                is_active=body.is_active,
            )
            session.add(category)
            await session.flush()
            snapshot = self._category_snapshot(category)
        await self._cache.refresh()
        return snapshot

    async def update_category(self, category_id: UUID, body: CategoryUpdate) -> CategorySnapshot:
        async with self._sessions.begin() as session:
            category = await session.get(Category, category_id)
            if category is None:
                raise MasterError(404, "category_not_found", "Category was not found")
            data = body.model_dump(exclude_unset=True)
            if "parent_id" in data and data["parent_id"] is not None:
                if data["parent_id"] == category_id:
                    raise MasterError(422, "invalid_parent", "Category cannot parent itself")
                parent = await session.get(Category, data["parent_id"])
                if parent is None or parent.revision_id != category.revision_id:
                    raise MasterError(404, "parent_not_found", "Parent category was not found")
            for key, value in data.items():
                setattr(category, key, value)
            await session.flush()
            snapshot = self._category_snapshot(category)
        await self._cache.refresh()
        return snapshot

    async def delete_category(self, category_id: UUID) -> None:
        object_keys: list[str] = []
        async with self._sessions.begin() as session:
            category = await session.get(Category, category_id)
            if category is None:
                raise MasterError(404, "category_not_found", "Category was not found")
            child = await session.scalar(
                select(Category.id).where(Category.parent_id == category_id).limit(1)
            )
            if child is not None:
                raise MasterError(
                    409, "category_has_children", "Delete or reparent child categories first"
                )
            product = await session.scalar(
                select(Product.id).where(Product.category_id == category_id).limit(1)
            )
            if product is not None:
                raise MasterError(
                    409, "category_has_products", "Move or delete products in this category first"
                )
            media_rows = (
                await session.scalars(
                    select(MediaMetadata).where(MediaMetadata.category_id == category_id)
                )
            ).all()
            object_keys = [row.object_key for row in media_rows]
            await session.delete(category)
        for object_key in object_keys:
            await self._media.delete_master(object_key)
        await self._cache.refresh()


    async def create_product(self, body: ProductCreate) -> ProductSnapshot:
        async with self._sessions.begin() as session:
            revision = await self._active_revision(session)
            category = await session.get(Category, body.category_id)
            if category is None or category.revision_id != revision.id:
                raise MasterError(404, "category_not_found", "Category was not found")
            product = Product(
                id=uuid4(),
                revision_id=revision.id,
                category_id=body.category_id,
                brand=body.brand,
                slug=await self._unique_slug(session, Product, revision.id),
                name=body.name,
                description=body.description,
                details=body.details,
                specifics=list(body.specifics),
                discount_percent=body.discount_percent,
                is_active=body.is_active,
            )
            session.add(product)
            await session.flush()
            snapshot = self._product_snapshot(product)
        await self._cache.refresh()
        return snapshot

    @staticmethod
    def _product_snapshot(product: Product) -> ProductSnapshot:
        return ProductSnapshot(
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
            variants=(),
            media=(),
        )

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
            if "specifics" in data and data["specifics"] is not None:
                data["specifics"] = list(data["specifics"])
            for key, value in data.items():
                setattr(product, key, value)
            await session.flush()
            snapshot = self._product_snapshot(product)
        await self._cache.refresh()
        return snapshot

    async def delete_product(self, product_id: UUID) -> None:
        object_keys: list[str] = []
        async with self._sessions.begin() as session:
            product = await session.get(Product, product_id)
            if product is None:
                raise MasterError(404, "product_not_found", "Product was not found")
            media_rows = (
                await session.scalars(
                    select(MediaMetadata).where(MediaMetadata.product_id == product_id)
                )
            ).all()
            object_keys = [row.object_key for row in media_rows]
            await session.delete(product)
        for object_key in object_keys:
            await self._media.delete_master(object_key)
        await self._cache.refresh()

    async def create_variant(self, body: VariantCreate) -> VariantSnapshot:
        async with self._sessions.begin() as session:
            product = await session.get(Product, body.product_id)
            if product is None:
                raise MasterError(404, "product_not_found", "Product was not found")
            variant = ProductVariant(
                id=uuid4(),
                product_id=body.product_id,
                sku=await self._unique_variant_code(session, body.product_id),
                name=body.name,
                price_minor=body.price_minor,
                currency=body.currency,
                is_active=body.is_active,
            )
            session.add(variant)
            await session.flush()
            snapshot = _variant_snapshot(variant)
        await self._cache.refresh()
        return snapshot

    async def update_variant(self, variant_id: UUID, body: VariantUpdate) -> VariantSnapshot:
        async with self._sessions.begin() as session:
            variant = await session.get(ProductVariant, variant_id)
            if variant is None:
                raise MasterError(404, "variant_not_found", "Variant was not found")
            data = body.model_dump(exclude_unset=True)
            for key, value in data.items():
                setattr(variant, key, value)
            await session.flush()
            snapshot = _variant_snapshot(variant)
        await self._cache.refresh()
        return snapshot

    async def delete_variant(self, variant_id: UUID) -> None:
        object_keys: list[str] = []
        async with self._sessions.begin() as session:
            variant = await session.get(ProductVariant, variant_id)
            if variant is None:
                raise MasterError(404, "variant_not_found", "Variant was not found")
            media_rows = (
                await session.scalars(
                    select(MediaMetadata).where(MediaMetadata.variant_id == variant_id)
                )
            ).all()
            object_keys = [row.object_key for row in media_rows]
            await session.delete(variant)
        for object_key in object_keys:
            await self._media.delete_master(object_key)
        await self._cache.refresh()

    async def attach_media(
        self,
        product_id: UUID,
        data: bytes,
        content_type: str | None,
        alt_text: str,
        sort_order: int,
        *,
        is_main: bool = False,
    ) -> MediaSnapshot:
        uploaded = await self._media.upload_master(
            data,
            content_type,
            alt_text,
            sort_order,
            object_prefix="catalog/",
            is_main=is_main,
        )
        try:
            async with self._sessions.begin() as session:
                product = await session.get(Product, product_id)
                if product is None:
                    raise MasterError(404, "product_not_found", "Product was not found")
                if is_main:
                    await self._clear_product_main(session, product_id, variant_id=None)
                row = MediaMetadata(
                    id=uploaded.id,
                    product_id=product_id,
                    category_id=None,
                    variant_id=None,
                    object_key=uploaded.object_key,
                    content_type=uploaded.content_type,
                    alt_text=uploaded.alt_text,
                    byte_size=uploaded.byte_size,
                    sort_order=uploaded.sort_order,
                    is_main=is_main,
                    is_active=True,
                )
                session.add(row)
                await session.flush()
        except Exception:
            await self._media.delete_master(uploaded.object_key)
            raise
        await self._cache.refresh()
        return uploaded

    async def attach_category_media(
        self,
        category_id: UUID,
        data: bytes,
        content_type: str | None,
        alt_text: str,
        sort_order: int,
        *,
        is_main: bool = False,
    ) -> MediaSnapshot:
        uploaded = await self._media.upload_master(
            data,
            content_type,
            alt_text,
            sort_order,
            object_prefix="catalog/",
            is_main=is_main,
        )
        try:
            async with self._sessions.begin() as session:
                category = await session.get(Category, category_id)
                if category is None:
                    raise MasterError(404, "category_not_found", "Category was not found")
                if is_main:
                    await self._clear_category_main(session, category_id)
                row = MediaMetadata(
                    id=uploaded.id,
                    product_id=None,
                    category_id=category_id,
                    variant_id=None,
                    object_key=uploaded.object_key,
                    content_type=uploaded.content_type,
                    alt_text=uploaded.alt_text,
                    byte_size=uploaded.byte_size,
                    sort_order=uploaded.sort_order,
                    is_main=is_main,
                    is_active=True,
                )
                session.add(row)
                await session.flush()
        except Exception:
            await self._media.delete_master(uploaded.object_key)
            raise
        await self._cache.refresh()
        return uploaded

    async def attach_variant_media(
        self,
        variant_id: UUID,
        data: bytes,
        content_type: str | None,
        alt_text: str,
        sort_order: int,
        *,
        is_main: bool = False,
    ) -> MediaSnapshot:
        uploaded = await self._media.upload_master(
            data,
            content_type,
            alt_text,
            sort_order,
            object_prefix="catalog/",
            is_main=is_main,
        )
        try:
            async with self._sessions.begin() as session:
                variant = await session.get(ProductVariant, variant_id)
                if variant is None:
                    raise MasterError(404, "variant_not_found", "Variant was not found")
                if is_main:
                    await self._clear_product_main(
                        session, variant.product_id, variant_id=variant_id
                    )
                row = MediaMetadata(
                    id=uploaded.id,
                    product_id=variant.product_id,
                    category_id=None,
                    variant_id=variant_id,
                    object_key=uploaded.object_key,
                    content_type=uploaded.content_type,
                    alt_text=uploaded.alt_text,
                    byte_size=uploaded.byte_size,
                    sort_order=uploaded.sort_order,
                    is_main=is_main,
                    is_active=True,
                )
                session.add(row)
                await session.flush()
        except Exception:
            await self._media.delete_master(uploaded.object_key)
            raise
        await self._cache.refresh()
        return uploaded

    async def set_media_main(self, media_id: UUID) -> MediaSnapshot:
        async with self._sessions.begin() as session:
            media = await session.get(MediaMetadata, media_id)
            if media is None or not media.is_active:
                raise MasterError(404, "media_not_found", "Media was not found")
            if media.category_id is not None:
                await self._clear_category_main(session, media.category_id)
            else:
                assert media.product_id is not None
                await self._clear_product_main(
                    session, media.product_id, variant_id=media.variant_id
                )
            media.is_main = True
            await session.flush()
            snapshot = MediaSnapshot(
                id=media.id,
                object_key=media.object_key,
                content_type=media.content_type,
                alt_text=media.alt_text,
                byte_size=media.byte_size,
                sort_order=media.sort_order,
                is_main=True,
                url=None,
            )
        await self._cache.refresh()
        # Re-read public URL via cache refresh path; rebuild with master URL helper.
        uploaded_url = await self._media.url(snapshot.object_key, master=True)
        return snapshot.model_copy(update={"url": uploaded_url})

    @staticmethod
    async def _clear_product_main(
        session: AsyncSession, product_id: UUID, *, variant_id: UUID | None
    ) -> None:
        statement = select(MediaMetadata).where(
            MediaMetadata.product_id == product_id,
            MediaMetadata.is_active.is_(True),
        )
        if variant_id is None:
            statement = statement.where(MediaMetadata.variant_id.is_(None))
        else:
            statement = statement.where(MediaMetadata.variant_id == variant_id)
        rows = (await session.scalars(statement)).all()
        for row in rows:
            if row.is_main:
                row.is_main = False

    @staticmethod
    async def _clear_category_main(session: AsyncSession, category_id: UUID) -> None:
        rows = (
            await session.scalars(
                select(MediaMetadata).where(
                    MediaMetadata.category_id == category_id,
                    MediaMetadata.is_active.is_(True),
                )
            )
        ).all()
        for row in rows:
            if row.is_main:
                row.is_main = False

    async def update_media(self, media_id: UUID, body: MediaUpdate) -> MediaSnapshot:
        async with self._sessions.begin() as session:
            media = await session.get(MediaMetadata, media_id)
            if media is None:
                raise MasterError(404, "media_not_found", "Media was not found")
            data = body.model_dump(exclude_unset=True)
            make_main = data.pop("is_main", None)
            for key, value in data.items():
                setattr(media, key, value)
            if make_main is True:
                if media.category_id is not None:
                    await self._clear_category_main(session, media.category_id)
                else:
                    assert media.product_id is not None
                    await self._clear_product_main(
                        session, media.product_id, variant_id=media.variant_id
                    )
                media.is_main = True
            elif make_main is False:
                media.is_main = False
            await session.flush()
            snapshot = MediaSnapshot(
                id=media.id,
                object_key=media.object_key,
                content_type=media.content_type,
                alt_text=media.alt_text,
                byte_size=media.byte_size,
                sort_order=media.sort_order,
                is_main=media.is_main,
                url=None,
            )
        await self._cache.refresh()
        uploaded_url = await self._media.url(snapshot.object_key, master=True)
        return snapshot.model_copy(update={"url": uploaded_url})

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
