"""Refresh the Redis catalog snapshot from Postgres.

When app dependencies are installed (Compose `cache-refresh` / API image), refreshes
in-process. On a bare host without pydantic, falls back to
`POST /v1/master/catalog/publish` using stdlib + `.env` admin credentials.

  python3 -m scripts.refresh_catalog_cache
  MASTER_API_BASE_URL=https://ecommerce.terabitventure.com/api \\
    python3 -m scripts.refresh_catalog_cache
"""

from __future__ import annotations

import asyncio
import sys


async def refresh_inprocess() -> None:
    from src.catalog.cache import CatalogSnapshotCache
    from src.catalog.repository import MasterCatalogRepository
    from src.core.config import get_settings
    from src.infrastructure.database import ReaderDatabase
    from src.infrastructure.redis import RedisClient

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


def refresh_via_master_api() -> None:
    from scripts.master_api_client import MasterApiError, login_from_env

    try:
        client, base_url = login_from_env()
        published = client.publish()
    except MasterApiError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    except OSError as exc:
        print(f"connection failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        f"published via {base_url} revision {published.get('revision_number')} "
        f"categories={published.get('category_count')} "
        f"products={published.get('product_count')}"
    )


def main() -> None:
    try:
        import pydantic  # noqa: F401
    except ModuleNotFoundError:
        refresh_via_master_api()
        return
    asyncio.run(refresh_inprocess())


if __name__ == "__main__":
    main()
