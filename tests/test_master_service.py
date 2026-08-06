from datetime import UTC, datetime
from uuid import uuid4

import jwt
import pytest

from src.catalog.models import CatalogRevision, Category, Product
from src.catalog.schemas import CatalogSnapshot
from src.core.config import get_settings
from src.master.auth import MasterAuthError, decode_access_token
from src.master.schemas import CategoryCreate, ProductCreate
from src.master.service import MasterCatalogService, MasterError


class FakeCache:
    async def refresh(self) -> CatalogSnapshot:
        return CatalogSnapshot(
            revision_id=uuid4(),
            revision_number=1,
            revision_label="Master catalog",
            generated_at=datetime.now(UTC),
            categories=(),
            products=(),
        )


class FakeSession:
    def __init__(self, revision: CatalogRevision, *, products: dict | None = None) -> None:
        self.revision = revision
        self.products = products or {}
        self.added: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        text = str(statement)
        if "catalog_revisions" in text or "CatalogRevision" in text:
            return self.revision
        return None

    async def get(self, model: type, key: object) -> object | None:
        if model is Category:
            for category in self.revision.categories:
                if category.id == key:
                    return category
            return None
        if model is Product:
            return self.products.get(key)
        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None


class SessionBegin:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *_args: object) -> None:
        return None


class SessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def begin(self) -> SessionBegin:
        return SessionBegin(self._session)


@pytest.mark.asyncio
async def test_master_service_creates_category_and_refreshes_cache() -> None:
    revision_id = uuid4()
    revision = CatalogRevision(
        id=revision_id,
        revision_number=1,
        label="Master catalog",
        is_active=True,
    )
    revision.categories = []
    session = FakeSession(revision)
    service = MasterCatalogService(SessionFactory(session), object(), FakeCache())  # type: ignore[arg-type]
    created = await service.create_category(
        CategoryCreate(slug="boots", name="Boots", description=None)
    )
    assert created.slug == "boots"
    assert isinstance(session.added[0], Category)


@pytest.mark.asyncio
async def test_master_service_rejects_unknown_category_for_product() -> None:
    revision = CatalogRevision(
        id=uuid4(),
        revision_number=1,
        label="Master catalog",
        is_active=True,
    )
    revision.categories = []
    service = MasterCatalogService(SessionFactory(FakeSession(revision)), object(), FakeCache())  # type: ignore[arg-type]
    with pytest.raises(MasterError) as exc:
        await service.create_product(
            ProductCreate(
                category_id=uuid4(),
                slug="ghost",
                name="Ghost",
                description=None,
            )
        )
    assert exc.value.code == "category_not_found"


def test_decode_rejects_wrong_role_claim() -> None:
    settings = get_settings()
    token = jwt.encode(
        {"sub": "admin", "role": "shopper", "iat": 1, "exp": 4_000_000_000},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(MasterAuthError):
        decode_access_token(settings, token)
