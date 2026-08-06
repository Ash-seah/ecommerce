"""Explicitly refresh the global catalog snapshot cache."""

import asyncio

from src.catalog.cache import CatalogSnapshotCache
from src.catalog.repository import MasterCatalogRepository
from src.core.config import get_settings
from src.infrastructure.database import ReaderDatabase
from src.infrastructure.redis import RedisClient


async def refresh() -> None:
    settings = get_settings()
    database = ReaderDatabase(settings)
    redis = RedisClient(settings)
    repository = MasterCatalogRepository(
        database.session_factory,
        media_public_base_url=(
            str(settings.media_public_base_url) if settings.media_public_base_url else None
        ),
        master_bucket=settings.minio_master_bucket,
    )
    cache = CatalogSnapshotCache(redis, repository, key_prefix=settings.redis_key_prefix)
    try:
        snapshot = await cache.refresh()
        print(f"cached catalog revision {snapshot.revision_number}")
    finally:
        await redis.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(refresh())
