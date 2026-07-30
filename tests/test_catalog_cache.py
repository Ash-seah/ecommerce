from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.catalog.cache import CatalogSnapshotCache
from src.catalog.schemas import CatalogSnapshot


class FakeBackend:
    def __init__(self) -> None:
        self.values: dict[str, bytes | str] = {}

    async def get(self, key: str) -> bytes | str | None:
        return self.values.get(key)

    async def set(self, key: str, value: bytes | str) -> object:
        self.values[key] = value
        return True


class FakeRepository:
    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def get_active_snapshot(self) -> CatalogSnapshot:
        self.calls += 1
        return self.snapshot


@pytest.mark.asyncio
async def test_cache_refresh_publishes_revision_and_round_trips() -> None:
    snapshot = CatalogSnapshot(
        revision_id=uuid4(),
        revision_number=7,
        revision_label="v7",
        generated_at=datetime.now(UTC),
        categories=(),
        products=(),
    )
    backend = FakeBackend()
    repository = FakeRepository(snapshot)
    cache = CatalogSnapshotCache(backend, repository, key_prefix="test")

    refreshed = await cache.refresh()
    cached = await cache.get()

    assert refreshed == snapshot
    assert cached == snapshot
    assert repository.calls == 1
    assert backend.values["test:catalog:current"] == "7"
    assert "test:catalog:snapshot:7" in backend.values


@pytest.mark.asyncio
async def test_cache_miss_does_not_query_database() -> None:
    snapshot = CatalogSnapshot(
        revision_id=uuid4(),
        revision_number=1,
        revision_label="v1",
        generated_at=datetime.now(UTC),
        categories=(),
        products=(),
    )
    repository = FakeRepository(snapshot)
    cache = CatalogSnapshotCache(FakeBackend(), repository, key_prefix="test")

    assert await cache.get() is None
    assert repository.calls == 0
