from datetime import timedelta
from io import BytesIO
from typing import cast
from uuid import uuid4

import pytest
from test_commerce_services import address, commerce
from test_sandbox_engine import service_fixture

from src.admin.schemas import (
    CouponInput,
    InventoryAdjustment,
    ProductInput,
    VariantInput,
)
from src.admin.service import AdminError, AdminService
from src.infrastructure.minio import MediaError, MediaService, MinioProtocol


@pytest.mark.asyncio
async def test_admin_overlays_are_isolated_and_never_mutate_master() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    admin = AdminService(sandbox, default_stock=7)
    first, _nonce, _state = await sandbox.create()
    second, _nonce, _state = await sandbox.create()
    product = master.products[0]
    variant = product.variants[0]

    await admin.update_product(
        first,
        product.id,
        ProductInput(
            category_id=product.category_id,
            name="Sandbox product",
            description="local",
        ),
    )
    await admin.adjust_price(first, variant.id, 250, None)
    await admin.adjust_inventory(
        first, variant.id, InventoryAdjustment(operation="set", quantity=3)
    )

    first_state, first_catalog = await admin.catalog(first)
    second_state, second_catalog = await admin.catalog(second)
    assert first_catalog.products[0].name == "Sandbox product"
    assert first_catalog.products[0].variants[0].price_minor == 250
    assert first_state.stock_overrides[variant.id] == 3
    assert second_catalog.products[0].name == "Base product"
    assert second_state.stock_overrides == {}
    assert master.products[0].name == "Base product"
    assert master.products[0].variants[0].price_minor == 100


@pytest.mark.asyncio
async def test_tombstones_restore_and_restore_all_preserve_commerce_state() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    admin = AdminService(sandbox, default_stock=5)
    commerce_service = commerce(sandbox, stock=5)
    session_id, _nonce, _state = await sandbox.create()
    product = master.products[0]
    variant = product.variants[0]
    await admin.delete_product(session_id, product.id)
    state = await sandbox.inspect(session_id)
    assert product.id in state.product_tombstones
    restored_state, restored = await admin.restore_product(session_id, product.id)
    assert restored.name == product.name
    assert product.id not in restored_state.product_tombstones
    await commerce_service.change_cart(session_id, variant.id, 1, add=False)
    await commerce_service.adjust_wallet(session_id, 500, "preserved credit", operation="credit")

    await admin.update_product(
        session_id,
        product.id,
        ProductInput(
            category_id=product.category_id,
            name="Temporary",
            description=None,
        ),
    )
    await admin.adjust_inventory(
        session_id, variant.id, InventoryAdjustment(operation="set", quantity=1)
    )
    await admin.put_coupon(
        session_id,
        CouponInput(code="LOCAL", kind="fixed", value=10),
        create=True,
    )
    reset = await admin.restore_all(session_id)
    assert reset.product_overlays == {}
    assert reset.stock_overrides == {}
    assert reset.coupons == {}
    assert reset.cart.lines[0].variant_id == variant.id
    assert reset.wallet.balance_minor == 500


@pytest.mark.asyncio
async def test_restore_variant_resets_overlay_inventory_and_tombstone() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    admin = AdminService(sandbox, default_stock=5)
    session_id, _nonce, _state = await sandbox.create()
    product = master.products[0]
    variant = master.products[0].variants[0]
    await admin.create_variant(
        session_id,
        product.id,
        VariantInput(
            sku="SECOND",
            name="Second variant",
            price_minor=100,
            currency=variant.currency,
        ),
    )

    await admin.update_variant(
        session_id,
        variant.id,
        VariantInput(
            sku=variant.sku,
            name="Changed variant",
            price_minor=250,
            currency=variant.currency,
        ),
    )
    await admin.adjust_inventory(
        session_id, variant.id, InventoryAdjustment(operation="set", quantity=1)
    )
    await admin.set_active(session_id, "variants", variant.id, active=False)

    restored_state, restored = await admin.restore_variant(session_id, variant.id)
    assert restored == variant
    assert variant.id not in restored_state.variant_overlays
    assert variant.id not in restored_state.variant_tombstones
    assert variant.id not in restored_state.stock_overrides


@pytest.mark.asyncio
async def test_admin_validates_references_and_duplicate_skus() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    admin = AdminService(sandbox, default_stock=5)
    session_id, _nonce, _state = await sandbox.create()

    with pytest.raises(AdminError, match="category"):
        await admin.create_product(
            session_id,
            ProductInput(
                category_id=uuid4(),
                name="Orphan",
                description=None,
            ),
        )

    product_state, product = await admin.create_product(
        session_id,
        ProductInput(
            category_id=master.categories[0].id,
            name="Custom",
            description=None,
        ),
    )
    assert len(product.slug) == 12
    assert product_state.version > 0
    with pytest.raises(AdminError, match="SKU"):
        await admin.create_variant(
            session_id,
            product.id,
            VariantInput(
                sku=master.products[0].variants[0].sku,
                name="Duplicate",
                price_minor=10,
                currency="USD",
            ),
        )
    with pytest.raises(AdminError, match="products"):
        await admin.delete_category(session_id, master.categories[0].id)


@pytest.mark.asyncio
async def test_custom_coupon_changes_checkout_pricing() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    admin = AdminService(sandbox, default_stock=5)
    commerce_service = commerce(sandbox, stock=5)
    session_id, _nonce, _state = await sandbox.create()
    shipping = address()
    await commerce_service.put_address(session_id, shipping)
    await commerce_service.adjust_wallet(session_id, 1_000, "credit", operation="credit")
    await commerce_service.change_cart(session_id, master.products[0].variants[0].id, 2, add=False)
    await admin.put_coupon(
        session_id,
        CouponInput(
            code="half",
            kind="percent",
            value=50,
            minimum_subtotal_minor=100,
            maximum_discount_minor=75,
        ),
        create=True,
    )

    order = await commerce_service.checkout(session_id, shipping.id, "HALF", "coupon")
    assert order.subtotal_minor == 200
    assert order.discount_minor == 75
    assert order.total_minor == 125


class Object:
    def __init__(self, object_name: str) -> None:
        self.object_name = object_name


class FakeMinio:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BytesIO,
        length: int,
        *,
        content_type: str,
    ) -> object:
        del content_type
        self.objects[(bucket_name, object_name)] = data.read(length)
        return object()

    def remove_object(self, bucket_name: str, object_name: str) -> object:
        self.objects.pop((bucket_name, object_name), None)
        return object()

    def list_objects(self, bucket_name: str, *, prefix: str, recursive: bool) -> list[Object]:
        del recursive
        return [
            Object(key)
            for bucket, key in self.objects
            if bucket == bucket_name and key.startswith(prefix)
        ]

    def presigned_get_object(
        self, bucket_name: str, object_name: str, *, expires: timedelta
    ) -> str:
        del expires
        return f"https://media.test/{bucket_name}/{object_name}"


def media_service(fake: FakeMinio, *, maximum: int = 1024) -> MediaService:
    return MediaService(
        cast(MinioProtocol, fake),
        master_bucket="master-media",
        sandbox_bucket="sandbox-media",
        max_upload_bytes=maximum,
        media_base_url="https://cdn.test",
    )


@pytest.mark.asyncio
async def test_add_media_round_trip_keeps_product_identity_fields() -> None:
    from src.catalog.schemas import MediaSnapshot

    sandbox, _redis, _secrets, master = await service_fixture()
    admin = AdminService(sandbox, default_stock=5)
    commerce_service = commerce(sandbox, stock=5)
    session_id, _nonce, _state = await sandbox.create()
    product = master.products[0]
    media = MediaSnapshot(
        id=uuid4(),
        object_key="sandboxes/demo/green.jpg",
        content_type="image/jpeg",
        alt_text="green shoes",
        byte_size=85624,
        sort_order=0,
        url="https://cdn.test/ecommerce-sandboxes/sandboxes/demo/green.jpg",
    )
    _state, updated = await admin.add_media(session_id, product.id, media)
    assert updated.category_id == product.category_id
    assert updated.slug == product.slug
    assert updated.name == product.name
    assert any(item.id == media.id for item in updated.media)

    page = await commerce_service.products(
        session_id,
        page=1,
        page_size=20,
        search=None,
        category=None,
        min_price_minor=None,
        max_price_minor=None,
        available=None,
        sort="name",
    )
    assert page.total >= 1
    assert page.items[0].name == product.name
    assert page.items[0].category_id == product.category_id


@pytest.mark.asyncio
async def test_media_rejects_spoofing_and_size_and_enforces_namespace() -> None:
    fake = FakeMinio()
    service = media_service(fake, maximum=20)
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 8
    uploaded = await service.upload("safe-a", png, "image/png", "Demo", 0)
    assert uploaded.object_key.startswith("sandboxes/safe-a/")
    assert uploaded.object_key.endswith(".png")
    assert uploaded.url is not None and uploaded.url.startswith("https://cdn.test/")

    with pytest.raises(MediaError, match="signature"):
        await service.upload("safe-a", b"not an image", "image/png", "Bad", 0)
    with pytest.raises(MediaError, match="Declared type"):
        await service.upload("safe-a", png, "image/jpeg", "Spoof", 0)
    with pytest.raises(MediaError, match="size"):
        await service.upload("safe-a", png + b"x" * 20, "image/png", "Large", 0)
    with pytest.raises(MediaError, match="not owned"):
        await service.delete("safe-b", uploaded.object_key)

    await service.delete("safe-a", uploaded.object_key)
    assert fake.objects == {}


@pytest.mark.asyncio
async def test_media_cleanup_removes_only_current_sandbox_prefix() -> None:
    fake = FakeMinio()
    service = media_service(fake)
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 8
    first = await service.upload("safe-a", png, "image/png", "First", 0)
    second = await service.upload("safe-b", png, "image/png", "Second", 0)

    await service.cleanup("safe-a")
    assert ("sandbox-media", first.object_key) not in fake.objects
    assert ("sandbox-media", second.object_key) in fake.objects
