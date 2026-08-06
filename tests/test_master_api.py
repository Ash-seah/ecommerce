"""JWT master-catalog operator auth and media helpers."""

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from test_admin_media import FakeMinio, media_service

from src.catalog.schemas import CategorySnapshot
from src.core.config import get_settings
from src.infrastructure.database import OwnerDatabase
from src.infrastructure.minio import MediaError
from src.main import create_app
from src.master.auth import (
    MasterAuthError,
    authenticate_password,
    decode_access_token,
    issue_access_token,
)
from src.master.schemas import PublishResponse


class StubMasterService:
    async def create_category(self, body: object) -> CategorySnapshot:
        del body
        return CategorySnapshot(
            id=uuid4(),
            parent_id=None,
            slug="a1b2c3d4e5f6",
            name="Demo",
            description=None,
            sort_order=0,
        )

    async def publish(self) -> PublishResponse:
        return PublishResponse(
            revision_number=1,
            revision_label="Master catalog",
            product_count=0,
            category_count=1,
        )


def _app_with_stub() -> object:
    settings = get_settings()
    app = create_app(settings)

    class RateProbe:
        async def ping(self) -> bool:
            return True

        async def rate_limit(self, _key: str, _window: int) -> tuple[int, int]:
            return 1, 30

    app.state.redis = RateProbe()
    app.state.master_service = StubMasterService()
    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = _app_with_stub()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as value:
        yield value


def test_plain_admin_password_issues_and_verifies_jwt() -> None:
    settings = get_settings()
    assert settings.admin_username == "admin"
    assert settings.admin_password == "admin123"
    subject = authenticate_password(settings, "admin", "admin123")
    token = issue_access_token(settings, subject)
    payload = decode_access_token(settings, token)
    assert payload["sub"] == "admin"
    assert payload["role"] == "master_admin"
    with pytest.raises(MasterAuthError):
        authenticate_password(settings, "admin", "wrong")
    with pytest.raises(MasterAuthError):
        decode_access_token(settings, "not-a-token")


@pytest.mark.asyncio
async def test_owner_database_uses_migration_role() -> None:
    settings = get_settings()
    database = OwnerDatabase(settings)
    try:
        assert database.engine.url.username == "ecommerce_owner"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_login_and_bearer_protect_master_routes(client: httpx.AsyncClient) -> None:
    denied = await client.post(
        "/v1/master/categories",
        json={"name": "Demo"},
    )
    assert denied.status_code == 401
    assert denied.json()["code"] == "auth_required"

    bad_login = await client.post(
        "/v1/master/auth/login",
        json={"username": "admin", "password": "nope"},
    )
    assert bad_login.status_code == 401
    assert bad_login.json()["code"] == "invalid_credentials"

    login = await client.post(
        "/v1/master/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert login.json()["token_type"] == "bearer"

    created = await client.post(
        "/v1/master/categories",
        json={"name": "Demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 200
    assert created.json()["category"]["name"] == "Demo"
    assert len(created.json()["category"]["slug"]) == 12

    published = await client.post(
        "/v1/master/catalog/publish",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert published.status_code == 200
    assert published.json()["category_count"] == 1


@pytest.mark.asyncio
async def test_upload_master_writes_catalog_prefix_and_rejects_non_catalog_delete() -> None:
    fake = FakeMinio()
    service = media_service(fake)
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 8
    uploaded = await service.upload_master(png, "image/png", "Hero", 0)
    assert uploaded.object_key.startswith("catalog/")
    assert ("master-media", uploaded.object_key) in fake.objects
    assert uploaded.url is not None
    assert "/master-media/" in uploaded.url

    with pytest.raises(MediaError, match="catalog/"):
        await service.delete_master("sandboxes/x/file.png")
    await service.delete_master(uploaded.object_key)
    assert fake.objects == {}


def test_openapi_includes_master_tag() -> None:
    app = _app_with_stub()
    schema = app.openapi()
    assert "master" in {tag["name"] for tag in schema["tags"]}
    assert "/v1/master/auth/login" in schema["paths"]
