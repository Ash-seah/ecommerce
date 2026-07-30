"""Atomic sandbox state lifecycle and mutation service."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol, Self
from uuid import uuid4

from redis.exceptions import WatchError

from src.catalog.cache import CatalogSnapshotCache
from src.catalog.schemas import CatalogSnapshot
from src.sandbox.merge import merge_catalog
from src.sandbox.models import SandboxState, WalletLedgerEntry, WalletState
from src.sandbox.security import SessionSecrets

StateMutation = Callable[[SandboxState], SandboxState]
MediaCleanupHook = Callable[[str, SandboxState], Awaitable[None]]


class SandboxNotFoundError(LookupError):
    pass


class CatalogUnavailableError(RuntimeError):
    pass


class MutationConflictError(RuntimeError):
    pass


class PipelineProtocol(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None: ...

    async def watch(self, *names: str) -> object: ...

    async def get(self, key: str) -> bytes | str | None: ...

    def multi(self) -> None: ...

    def set(self, key: str, value: str, *, ex: int) -> object: ...

    async def execute(self) -> list[object]: ...

    async def reset(self) -> None: ...


class RedisProtocol(Protocol):
    async def get(self, key: str) -> bytes | str | None: ...

    async def set(self, key: str, value: str, *, ex: int, nx: bool = False) -> object: ...

    async def expire(self, key: str, ttl: int) -> object: ...

    def pipeline(self) -> PipelineProtocol: ...


async def _noop_cleanup(_safe_id: str, _state: SandboxState) -> None:
    return None


class SandboxService:
    def __init__(
        self,
        redis: RedisProtocol,
        catalog_cache: CatalogSnapshotCache,
        secrets: SessionSecrets,
        *,
        key_prefix: str,
        ttl_seconds: int = 7200,
        max_mutation_retries: int = 4,
        initial_wallet_minor: int = 0,
        wallet_currency: str = "USD",
        media_cleanup: MediaCleanupHook = _noop_cleanup,
    ) -> None:
        self._redis = redis
        self._catalog_cache = catalog_cache
        self._secrets = secrets
        self._key_prefix = f"{key_prefix}:sandbox"
        self._ttl = ttl_seconds
        self._max_retries = max_mutation_retries
        self._initial_wallet_minor = initial_wallet_minor
        self._wallet_currency = wallet_currency
        self._media_cleanup = media_cleanup

    def _initial_wallet(self, now: datetime) -> WalletState:
        ledger = (
            [
                WalletLedgerEntry(
                    id=uuid4(),
                    amount_minor=self._initial_wallet_minor,
                    balance_after_minor=self._initial_wallet_minor,
                    kind="initial_credit",
                    reference="demo initial credit",
                    created_at=now,
                )
            ]
            if self._initial_wallet_minor
            else []
        )
        return WalletState(
            currency=self._wallet_currency,
            balance_minor=self._initial_wallet_minor,
            ledger=ledger,
        )

    def safe_id(self, session_id: str) -> str:
        return self._secrets.session_hash(session_id)

    def key_for_safe_id(self, safe_id: str) -> str:
        return f"{self._key_prefix}:{safe_id}"

    async def create(self) -> tuple[str, str, SandboxState]:
        master = await self._catalog_cache.get()
        if master is None:
            raise CatalogUnavailableError("master catalog cache is unavailable")
        while True:
            session_id = self._secrets.new_session_id()
            safe_id = self.safe_id(session_id)
            nonce = self._secrets.new_csrf_nonce()
            now = datetime.now(UTC)
            state = SandboxState(
                pinned_master_revision=master.revision_number,
                created_at=now,
                updated_at=now,
                csrf_nonce_hash=self._secrets.csrf_nonce_hash(nonce),
                wallet=self._initial_wallet(now),
            )
            created = await self._redis.set(
                self.key_for_safe_id(safe_id),
                state.model_dump_json(),
                ex=self._ttl,
                nx=True,
            )
            if created:
                return session_id, nonce, state

    async def inspect(self, session_id: str) -> SandboxState:
        safe_id = self.safe_id(session_id)
        key = self.key_for_safe_id(safe_id)
        payload = await self._redis.get(key)
        if payload is None:
            raise SandboxNotFoundError("sandbox session was not found")
        await self._redis.expire(key, self._ttl)
        return SandboxState.model_validate_json(payload)

    async def refresh(self, session_id: str) -> SandboxState:
        return await self.inspect(session_id)

    async def mutate(self, session_id: str, mutation: StateMutation) -> SandboxState:
        safe_id = self.safe_id(session_id)
        key = self.key_for_safe_id(safe_id)
        for _attempt in range(self._max_retries):
            try:
                async with self._redis.pipeline() as pipeline:
                    await pipeline.watch(key)
                    payload = await pipeline.get(key)
                    if payload is None:
                        raise SandboxNotFoundError("sandbox session was not found")
                    current = SandboxState.model_validate_json(payload)
                    proposed = mutation(current)
                    updated = proposed.model_copy(
                        update={
                            "version": current.version + 1,
                            "created_at": current.created_at,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                    pipeline.multi()
                    pipeline.set(key, updated.model_dump_json(), ex=self._ttl)
                    await pipeline.execute()
                    return updated
            except WatchError:
                continue
        raise MutationConflictError("sandbox update conflicted too many times")

    async def rotate_csrf(self, session_id: str) -> tuple[str, SandboxState]:
        nonce = self._secrets.new_csrf_nonce()
        nonce_hash = self._secrets.csrf_nonce_hash(nonce)
        state = await self.mutate(
            session_id,
            lambda current: current.model_copy(update={"csrf_nonce_hash": nonce_hash}),
        )
        return nonce, state

    async def reset(self, session_id: str) -> tuple[str, SandboxState]:
        safe_id = self.safe_id(session_id)
        previous = await self.inspect(session_id)
        nonce = self._secrets.new_csrf_nonce()
        nonce_hash = self._secrets.csrf_nonce_hash(nonce)

        def clear(current: SandboxState) -> SandboxState:
            return SandboxState(
                pinned_master_revision=current.pinned_master_revision,
                created_at=current.created_at,
                updated_at=current.updated_at,
                csrf_nonce_hash=nonce_hash,
                wallet=self._initial_wallet(current.updated_at),
            )

        state = await self.mutate(session_id, clear)
        await self._media_cleanup(safe_id, previous)
        return nonce, state

    async def merged_catalog(self, session_id: str) -> CatalogSnapshot:
        state = await self.inspect(session_id)
        master = await self.master_catalog(state.pinned_master_revision)
        return merge_catalog(master, state)

    async def master_catalog(self, revision: int) -> CatalogSnapshot:
        master = await self._catalog_cache.get_revision(revision)
        if master is None:
            raise CatalogUnavailableError("pinned master catalog revision is unavailable")
        return master
