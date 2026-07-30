from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Self, cast
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import AnyHttpUrl
from redis.exceptions import WatchError

from src.catalog.cache import CatalogSnapshotCache
from src.catalog.schemas import (
    CatalogSnapshot,
    CategorySnapshot,
    ProductSnapshot,
    VariantSnapshot,
)
from src.core.config import get_settings
from src.sandbox.merge import merge_catalog
from src.sandbox.models import CategoryOverlay, ProductOverlay, SandboxState, VariantOverlay
from src.sandbox.router import SandboxAPIError, router
from src.sandbox.security import SessionSecrets
from src.sandbox.service import RedisProtocol, SandboxService


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.key = ""
        self.pending: tuple[str, str, int] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def watch(self, *names: str) -> object:
        self.key = names[0]
        return True

    async def get(self, key: str) -> bytes | str | None:
        return self.redis.values.get(key)

    def multi(self) -> None:
        return None

    def set(self, key: str, value: str, *, ex: int) -> object:
        self.pending = (key, value, ex)
        return self

    async def execute(self) -> list[object]:
        if self.redis.conflicts:
            self.redis.conflicts -= 1
            raise WatchError
        assert self.pending is not None
        key, value, ttl = self.pending
        self.redis.values[key] = value
        self.redis.ttls[key] = ttl
        return [True]

    async def reset(self) -> None:
        return None


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes | str] = {}
        self.ttls: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []
        self.conflicts = 0

    async def get(self, key: str) -> bytes | str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int, nx: bool = False) -> object:
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def expire(self, key: str, ttl: int) -> object:
        if key not in self.values:
            return False
        self.expire_calls.append((key, ttl))
        self.ttls[key] = ttl
        return True

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


class NullRepository:
    async def get_active_snapshot(self) -> CatalogSnapshot:
        raise AssertionError("sandbox routes must not reach PostgreSQL")


def snapshot() -> CatalogSnapshot:
    category_id = uuid4()
    product_id = uuid4()
    return CatalogSnapshot(
        revision_id=uuid4(),
        revision_number=8,
        revision_label="v8",
        generated_at=datetime.now(UTC),
        categories=(
            CategorySnapshot(
                id=category_id,
                parent_id=None,
                slug="base",
                name="Base",
                description=None,
                sort_order=0,
            ),
        ),
        products=(
            ProductSnapshot(
                id=product_id,
                category_id=category_id,
                slug="base-product",
                name="Base product",
                description=None,
                variants=(
                    VariantSnapshot(
                        id=uuid4(),
                        sku="BASE",
                        name="Base variant",
                        price_minor=100,
                        currency="USD",
                    ),
                ),
                media=(),
            ),
        ),
    )


async def service_fixture(
    cleanup: Callable[[str, SandboxState], Awaitable[None]] | None = None,
) -> tuple[SandboxService, FakeRedis, SessionSecrets, CatalogSnapshot]:
    redis = FakeRedis()
    master = snapshot()
    cache = CatalogSnapshotCache(redis, NullRepository(), key_prefix="ecommerce")
    await redis.set(cache.snapshot_key(8), master.model_dump_json(), ex=7200)
    redis.values["ecommerce:catalog:current"] = "8"
    secrets = SessionSecrets(b"s" * 32, b"c" * 32)
    kwargs = {} if cleanup is None else {"media_cleanup": cleanup}
    service = SandboxService(
        cast(RedisProtocol, redis), cache, secrets, key_prefix="ecommerce", **kwargs
    )
    return service, redis, secrets, master


@pytest.mark.asyncio
async def test_sessions_are_isolated_slide_ttl_and_hide_raw_ids() -> None:
    service, redis, _secrets, _master = await service_fixture()
    first_id, _nonce, first = await service.create()
    second_id, _nonce, second = await service.create()
    assert first_id != second_id
    assert first.pinned_master_revision == second.pinned_master_revision
    assert first.wallet == second.wallet

    changed = await service.mutate(
        first_id,
        lambda state: state.model_copy(
            update={"wallet": state.wallet.model_copy(update={"balance_minor": 500})}
        ),
    )
    assert changed.wallet.balance_minor == 500
    assert (await service.inspect(second_id)).wallet.balance_minor == 0
    first_key = service.key_for_safe_id(service.safe_id(first_id))
    assert redis.ttls[first_key] == 7200
    assert redis.expire_calls[-1][1] == 7200
    serialized = "\n".join([*redis.values.keys(), *(str(v) for v in redis.values.values())])
    assert first_id not in serialized
    assert second_id not in serialized


@pytest.mark.asyncio
async def test_atomic_mutation_retries_conflict_and_increments_version() -> None:
    service, redis, _secrets, _master = await service_fixture()
    session_id, _nonce, _state = await service.create()
    redis.conflicts = 1
    updated = await service.mutate(session_id, lambda state: state)
    assert updated.version == 1
    assert (await service.inspect(session_id)).version == 1


@pytest.mark.asyncio
async def test_reset_clears_only_target_and_calls_media_cleanup() -> None:
    cleanup_calls: list[str] = []

    async def cleanup(safe_id: str, _state: SandboxState) -> None:
        cleanup_calls.append(safe_id)

    service, _redis, _secrets, _master = await service_fixture(cleanup)
    first_id, _nonce, _state = await service.create()
    second_id, _nonce, _state = await service.create()
    await service.mutate(
        first_id,
        lambda state: state.model_copy(
            update={"wallet": state.wallet.model_copy(update={"balance_minor": 10})}
        ),
    )
    await service.mutate(
        second_id,
        lambda state: state.model_copy(
            update={"wallet": state.wallet.model_copy(update={"balance_minor": 20})}
        ),
    )
    _nonce, reset = await service.reset(first_id)
    assert reset.wallet.balance_minor == 0
    assert (await service.inspect(second_id)).wallet.balance_minor == 20
    assert cleanup_calls == [service.safe_id(first_id)]


def test_copy_on_write_merge_preserves_master_and_applies_overlays() -> None:
    master = snapshot()
    now = datetime.now(UTC)
    category = master.categories[0]
    product = master.products[0]
    variant = product.variants[0]
    state = SandboxState(
        pinned_master_revision=8,
        created_at=now,
        updated_at=now,
        csrf_nonce_hash="a" * 64,
        category_overlays={category.id: CategoryOverlay(name="Changed")},
        product_overlays={product.id: ProductOverlay(name="Changed product")},
        variant_overlays={variant.id: VariantOverlay(price_minor=250)},
    )
    merged = merge_catalog(master, state)
    assert merged.categories[0].name == "Changed"
    assert merged.products[0].name == "Changed product"
    assert merged.products[0].variants[0].price_minor == 250
    assert master.categories[0].name == "Base"
    assert master.products[0].variants[0].price_minor == 100


@pytest.mark.asyncio
async def test_cookie_flags_origin_bound_csrf_and_rotation() -> None:
    service, _redis, secrets, _master = await service_fixture()
    settings = get_settings().model_copy(
        update={
            "cors_origins": [AnyHttpUrl("https://client.test")],
            "session_cookie_secure": True,
        }
    )
    app = FastAPI()
    app.state.settings = settings
    app.state.session_secrets = secrets
    app.state.sandbox_service = service
    app.include_router(router)

    @app.exception_handler(SandboxAPIError)
    async def handler(_request: Request, exc: SandboxAPIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://client.test") as client:
        created = await client.get(
            "/v1/sandbox/session/create",
            headers={"Origin": "https://client.test"},
        )
        assert created.status_code == 200
        cookie = created.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "secure" in cookie
        assert "samesite=lax" in cookie
        token = created.json()["csrf_token"]

        missing = await client.post(
            "/v1/sandbox/session/refresh",
            headers={"Origin": "https://client.test"},
        )
        assert missing.status_code == 403
        wrong_origin = await client.post(
            "/v1/sandbox/session/refresh",
            headers={"Origin": "https://evil.test", "X-CSRF-Token": token},
        )
        assert wrong_origin.status_code == 403
        valid = await client.post(
            "/v1/sandbox/session/refresh",
            headers={"Origin": "https://client.test", "X-CSRF-Token": token},
        )
        assert valid.status_code == 200
        assert "max-age=7200" in valid.headers["set-cookie"].lower()
        rotated = await client.post(
            "/v1/sandbox/session/csrf",
            headers={"Origin": "https://client.test", "X-CSRF-Token": token},
        )
        assert rotated.status_code == 200
        assert rotated.json()["csrf_token"] != token
        rejected_old = await client.post(
            "/v1/sandbox/session/refresh",
            headers={"Origin": "https://client.test", "X-CSRF-Token": token},
        )
        assert rejected_old.status_code == 403
