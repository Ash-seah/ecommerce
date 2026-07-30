"""Revision-addressed global catalog snapshot cache."""

from typing import Protocol

from src.catalog.schemas import CatalogSnapshot


class CacheBackend(Protocol):
    async def get(self, key: str) -> bytes | str | None: ...

    async def set(self, key: str, value: bytes | str) -> object: ...


class ActiveCatalogReader(Protocol):
    async def get_active_snapshot(self) -> CatalogSnapshot: ...


class CatalogSnapshotCache:
    def __init__(
        self,
        backend: CacheBackend,
        repository: ActiveCatalogReader,
        *,
        key_prefix: str,
    ) -> None:
        self._backend = backend
        self._repository = repository
        self._current_key = f"{key_prefix}:catalog:current"
        self._snapshot_prefix = f"{key_prefix}:catalog:snapshot"

    def snapshot_key(self, revision_number: int) -> str:
        return f"{self._snapshot_prefix}:{revision_number}"

    async def get(self) -> CatalogSnapshot | None:
        revision = await self._backend.get(self._current_key)
        if revision is None:
            return None
        if isinstance(revision, bytes):
            revision = revision.decode("ascii")
        if not revision.isdecimal():
            return None
        return await self.get_revision(int(revision))

    async def get_revision(self, revision_number: int) -> CatalogSnapshot | None:
        """Read an immutable revision without falling through to PostgreSQL."""
        payload = await self._backend.get(self.snapshot_key(revision_number))
        if payload is None:
            return None
        return CatalogSnapshot.model_validate_json(payload)

    async def refresh(self) -> CatalogSnapshot:
        snapshot = await self._repository.get_active_snapshot()
        payload = snapshot.model_dump_json()
        await self._backend.set(self.snapshot_key(snapshot.revision_number), payload)
        await self._backend.set(self._current_key, str(snapshot.revision_number))
        return snapshot
