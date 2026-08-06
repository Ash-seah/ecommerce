"""Idempotently seed the master catalog using owner credentials."""

import asyncio
import uuid

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.catalog.models import CatalogRevision, Category, MediaMetadata, Product, ProductVariant
from src.core.config import get_settings

NAMESPACE = uuid.UUID("4cc2143e-703d-43df-9d62-b659e03e061c")


def stable_id(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, name)


async def seed() -> None:
    settings = get_settings()
    engine = create_async_engine(str(settings.migration_database_url), pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    revision_id = stable_id("revision:sample-v1")
    category_id = stable_id("category:apparel")
    product_id = stable_id("product:classic-tee")

    async with sessions.begin() as session:
        await session.execute(
            insert(CatalogRevision)
            .values(
                id=revision_id,
                revision_number=1,
                label="Sample catalog",
                is_active=False,
            )
            .on_conflict_do_update(
                index_elements=[CatalogRevision.id],
                set_={"label": "Sample catalog", "updated_at": func.now()},
            )
        )
        await session.execute(
            insert(Category)
            .values(
                id=category_id,
                revision_id=revision_id,
                slug=category_id.hex[:12],
                name="Apparel",
                description="Everyday apparel",
                sort_order=10,
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=[Category.id],
                set_={
                    "name": "Apparel",
                    "description": "Everyday apparel",
                    "sort_order": 10,
                    "is_active": True,
                    "updated_at": func.now(),
                },
            )
        )
        await session.execute(
            insert(Product)
            .values(
                id=product_id,
                revision_id=revision_id,
                category_id=category_id,
                slug=product_id.hex[:12],
                name="Classic Tee",
                description="A deterministic sample product",
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=[Product.id],
                set_={
                    "name": "Classic Tee",
                    "description": "A deterministic sample product",
                    "is_active": True,
                    "updated_at": func.now(),
                },
            )
        )
        for size, price in (("S", 1999), ("M", 1999), ("L", 2099)):
            variant_id = stable_id(f"variant:classic-tee:{size.lower()}")
            await session.execute(
                insert(ProductVariant)
                .values(
                    id=variant_id,
                    product_id=product_id,
                    sku=f"TEE-CLASSIC-{size}",
                    name=f"Classic Tee / {size}",
                    price_minor=price,
                    currency="IRR",
                    is_active=True,
                )
                .on_conflict_do_update(
                    index_elements=[ProductVariant.id],
                    set_={
                        "price_minor": price,
                        "currency": "IRR",
                        "is_active": True,
                        "updated_at": func.now(),
                    },
                )
            )
        await session.execute(
            insert(MediaMetadata)
            .values(
                id=stable_id("media:classic-tee:front"),
                product_id=product_id,
                object_key="catalog/classic-tee/front.webp",
                content_type="image/webp",
                alt_text="Classic Tee front view",
                byte_size=48231,
                sort_order=0,
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=[MediaMetadata.id],
                set_={
                    "alt_text": "Classic Tee front view",
                    "byte_size": 48231,
                    "is_active": True,
                    "updated_at": func.now(),
                },
            )
        )
        await session.execute(update(CatalogRevision).values(is_active=False))
        await session.execute(
            update(CatalogRevision)
            .where(CatalogRevision.id == revision_id)
            .values(is_active=True, updated_at=func.now())
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
