from datetime import UTC, datetime
from uuid import uuid4

import jwt
import pytest

from src.catalog.models import CatalogRevision, Category, Product, ProductVariant
from src.catalog.schemas import CatalogSnapshot, CategorySnapshot
from src.core.config import get_settings
from src.master.auth import MasterAuthError, decode_access_token
from src.master.schemas import CategoryCreate, CategoryUpdate, ProductCreate, VariantUpdate
from src.master.service import MasterCatalogService, MasterError


class FakeCache:
    def __init__(self, snapshot: CatalogSnapshot | None = None) -> None:
        self.snapshot = snapshot
        self.refresh_count = 0

    async def get(self) -> CatalogSnapshot | None:
        return self.snapshot

    async def refresh(self) -> CatalogSnapshot:
        self.refresh_count += 1
        if self.snapshot is None:
            self.snapshot = CatalogSnapshot(
                revision_id=uuid4(),
                revision_number=1,
                revision_label="Master catalog",
                generated_at=datetime.now(UTC),
                categories=(),
                products=(),
            )
        return self.snapshot


class FakeSession:
    def __init__(
        self,
        revision: CatalogRevision,
        *,
        products: dict | None = None,
        variants: dict | None = None,
        scalar_results: list[object | None] | None = None,
    ) -> None:
        self.revision = revision
        self.products = products or {}
        self.variants = variants or {}
        self.added: list[object] = []
        self.deleted: list[object] = []
        self._scalar_results = list(scalar_results or [])

    async def scalar(self, statement: object) -> object | None:
        if self._scalar_results:
            return self._scalar_results.pop(0)
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
        if model is ProductVariant:
            return self.variants.get(key)
        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    async def delete(self, item: object) -> None:
        self.deleted.append(item)

    async def scalars(self, statement: object) -> object:
        del statement

        class Result:
            def all(self_inner) -> list:
                return []

        return Result()

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
        CategoryCreate(name="Boots", description=None)
    )
    assert len(created.slug) == 12
    assert created.slug.isalnum()
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
                name="Ghost",
                description=None,
            )
        )
    assert exc.value.code == "category_not_found"


@pytest.mark.asyncio
async def test_master_service_updates_and_deletes_category() -> None:
    category_id = uuid4()
    category = Category(
        id=category_id,
        revision_id=uuid4(),
        parent_id=None,
        slug="boots12abcd",
        name="Boots",
        description=None,
        sort_order=0,
        is_active=True,
    )
    revision = CatalogRevision(
        id=category.revision_id,
        revision_number=1,
        label="Master catalog",
        is_active=True,
    )
    revision.categories = [category]
    session = FakeSession(revision, scalar_results=[None, None])
    cache = FakeCache(
        CatalogSnapshot(
            revision_id=revision.id,
            revision_number=1,
            revision_label="Master catalog",
            generated_at=datetime.now(UTC),
            categories=(
                CategorySnapshot(
                    id=category_id,
                    parent_id=None,
                    slug=category.slug,
                    name="Boots",
                    description=None,
                    sort_order=0,
                ),
            ),
            products=(),
        )
    )
    service = MasterCatalogService(SessionFactory(session), object(), cache)  # type: ignore[arg-type]

    updated = await service.update_category(category_id, CategoryUpdate(name="Shoes"))
    assert updated.name == "Shoes"
    assert category.name == "Shoes"

    await service.delete_category(category_id)
    assert session.deleted == [category]
    assert cache.refresh_count >= 2


@pytest.mark.asyncio
async def test_master_service_rejects_delete_category_with_products() -> None:
    category_id = uuid4()
    category = Category(
        id=category_id,
        revision_id=uuid4(),
        parent_id=None,
        slug="boots12abcd",
        name="Boots",
        description=None,
        sort_order=0,
        is_active=True,
    )
    revision = CatalogRevision(
        id=category.revision_id,
        revision_number=1,
        label="Master catalog",
        is_active=True,
    )
    revision.categories = [category]
    session = FakeSession(revision, scalar_results=[None, uuid4()])
    service = MasterCatalogService(SessionFactory(session), object(), FakeCache())  # type: ignore[arg-type]
    with pytest.raises(MasterError) as exc:
        await service.delete_category(category_id)
    assert exc.value.code == "category_has_products"


@pytest.mark.asyncio
async def test_master_service_updates_and_deletes_variant() -> None:
    variant_id = uuid4()
    variant = ProductVariant(
        id=variant_id,
        product_id=uuid4(),
        sku="sku12abcdxyz",
        name="Default",
        price_minor=100,
        currency="USD",
        is_active=True,
    )
    revision = CatalogRevision(
        id=uuid4(),
        revision_number=1,
        label="Master catalog",
        is_active=True,
    )
    revision.categories = []
    session = FakeSession(revision, variants={variant_id: variant})

    class FakeMedia:
        async def delete_master(self, object_key: str) -> None:
            del object_key

    service = MasterCatalogService(SessionFactory(session), FakeMedia(), FakeCache())  # type: ignore[arg-type]
    updated = await service.update_variant(variant_id, VariantUpdate(price_minor=250))
    assert updated.price_minor == 250
    await service.delete_variant(variant_id)
    assert session.deleted == [variant]


def test_decode_rejects_wrong_role_claim() -> None:
    settings = get_settings()
    token = jwt.encode(
        {"sub": "admin", "role": "shopper", "iat": 1, "exp": 4_000_000_000},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(MasterAuthError):
        decode_access_token(settings, token)
