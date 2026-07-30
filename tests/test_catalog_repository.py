from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.catalog.models import CatalogRevision, Category, MediaMetadata, Product, ProductVariant
from src.catalog.repository import CatalogNotAvailableError, MasterCatalogRepository
from src.core.config import get_settings
from src.infrastructure.database import ReaderDatabase


class FakeSession:
    def __init__(self, revision: CatalogRevision | None) -> None:
        self.revision = revision

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def scalar(self, _statement: object) -> CatalogRevision | None:
        return self.revision


def _revision() -> CatalogRevision:
    revision_id = uuid4()
    category_id = uuid4()
    product_id = uuid4()
    revision = CatalogRevision(
        id=revision_id,
        revision_number=2,
        label="v2",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    revision.categories = [
        Category(
            id=category_id,
            revision_id=revision_id,
            slug="category",
            name="Category",
            sort_order=0,
            is_active=True,
        )
    ]
    product = Product(
        id=product_id,
        revision_id=revision_id,
        category_id=category_id,
        slug="product",
        name="Product",
        is_active=True,
    )
    product.variants = [
        ProductVariant(
            id=uuid4(),
            product_id=product_id,
            sku="SKU",
            name="Variant",
            price_minor=1250,
            currency="USD",
            is_active=True,
        )
    ]
    product.media = [
        MediaMetadata(
            id=uuid4(),
            product_id=product_id,
            object_key="product.webp",
            content_type="image/webp",
            alt_text="Product",
            byte_size=10,
            sort_order=0,
            is_active=True,
        )
    ]
    revision.products = [product]
    return revision


@pytest.mark.asyncio
async def test_repository_builds_active_snapshot() -> None:
    revision = _revision()
    repository = MasterCatalogRepository(lambda: FakeSession(revision))  # type: ignore[arg-type]

    snapshot = await repository.get_active_snapshot()

    assert snapshot.revision_number == 2
    assert snapshot.products[0].variants[0].price_minor == 1250
    assert snapshot.products[0].media[0].object_key == "product.webp"


@pytest.mark.asyncio
async def test_repository_rejects_missing_active_revision() -> None:
    def factory() -> FakeSession:
        return FakeSession(None)

    repository = MasterCatalogRepository(factory)  # type: ignore[arg-type]

    with pytest.raises(CatalogNotAvailableError):
        await repository.get_active_snapshot()


@pytest.mark.asyncio
async def test_runtime_database_is_reader_role_and_repository_has_no_write_surface() -> None:
    settings = get_settings()
    database = ReaderDatabase(settings)
    try:
        assert database.engine.url.username == "ecommerce_reader"
        public = {name for name in dir(MasterCatalogRepository) if not name.startswith("_")}
        assert public == {"get_active_snapshot"}
        assert not {"add", "delete", "save", "update", "commit"} & public
    finally:
        await database.close()
