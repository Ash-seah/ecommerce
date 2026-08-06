"""Sales analytics unit tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_commerce_services import address, commerce
from test_sandbox_engine import service_fixture

from src.sales.allocate import allocate_proportionally
from src.sales.analytics import bestsellers, by_category, filter_sales, summarize, timeseries
from src.sales.capture import sale_from_create
from src.sales.schemas import SaleCreate
from src.sales.service import SandboxSalesService, SalesAdminError


def test_allocate_proportionally_preserves_total() -> None:
    shares = allocate_proportionally([2, 1, 1], 10)
    assert sum(shares) == 10
    assert allocate_proportionally([], 5) == []
    assert allocate_proportionally([1, 1], 0) == [0, 0]
    assert sum(allocate_proportionally([100, 100], 1)) == 1


@pytest.mark.asyncio
async def test_checkout_writes_sale_events_and_admin_analytics() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    service = commerce(sandbox, stock=5)
    sales = SandboxSalesService(sandbox)
    session_id, _nonce, _state = await sandbox.create()
    shipping = address()
    await service.put_address(session_id, shipping)
    await service.adjust_wallet(session_id, 1_000, "credit", operation="credit")
    variant = master.products[0].variants[0]
    await service.change_cart(session_id, variant.id, 2, add=False)
    order = await service.checkout(session_id, shipping.id, None, "sale-1")

    listed = await sales.list_sales(session_id, page=1, page_size=20)
    assert listed.total == 1
    sale = listed.items[0]
    assert sale.order_id == order.id
    assert sale.quantity == 2
    assert sale.line_gross_minor == 200
    assert sale.country_code == "US"
    assert sale.occurred_at == order.created_at

    summary = await sales.summary(session_id)
    assert summary.orders == 1
    assert summary.units_sold == 2
    assert summary.net_minor == 200

    top = await sales.bestsellers(session_id, metric="units", limit=5)
    assert top.items[0].product_id == master.products[0].id
    assert top.items[0].units_sold == 2

    series = await sales.timeseries(session_id, bucket="day")
    assert len(series.points) == 1
    assert series.points[0].units_sold == 2

    categories = await sales.by_category(session_id)
    assert categories.items[0].category_id == master.products[0].category_id

    await service.transition_order(session_id, order.id, "cancel")
    voided = await sales.list_sales(session_id, page=1, page_size=20, status="voided")
    assert voided.total == 1
    assert voided.items[0].void_reason == "order_cancelled"


@pytest.mark.asyncio
async def test_admin_can_seed_alter_and_watch_sales() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    sales = SandboxSalesService(sandbox)
    session_id, _nonce, _state = await sandbox.create()
    product = master.products[0]
    variant = product.variants[0]
    created = await sales.create(
        session_id,
        SaleCreate(
            product_id=product.id,
            product_slug=product.slug,
            product_name=product.name,
            category_id=product.category_id,
            category_slug=master.categories[0].slug,
            category_name=master.categories[0].name,
            variant_id=variant.id,
            variant_sku=variant.sku,
            variant_name=variant.name,
            list_unit_price_minor=100,
            unit_price_minor=80,
            quantity=3,
            country_code="DE",
            city="Berlin",
            notes="seeded for demo",
        ),
    )
    assert created.source == "admin"
    assert created.line_net_minor == 240

    from src.sales.schemas import SaleUpdate

    updated = await sales.update(
        session_id, created.id, SaleUpdate(quantity=5, notes="adjusted")
    )
    assert updated.quantity == 5
    assert updated.line_gross_minor == 400
    assert updated.notes == "adjusted"

    feed = await sales.feed(session_id, since=None, limit=10)
    assert any(item.id == created.id for item in feed.items)

    voided = await sales.void(session_id, created.id, reason="demo void")
    assert voided.status == "voided"
    assert voided.void_reason == "demo void"

    await sales.delete(session_id, created.id)
    with pytest.raises(SalesAdminError):
        await sales.get(session_id, created.id)


def test_analytics_helpers_rank_and_filter() -> None:
    now = datetime.now(UTC)
    product_a = uuid4()
    product_b = uuid4()
    category = uuid4()
    events = [
        sale_from_create(
            SaleCreate(
                occurred_at=now - timedelta(hours=2),
                product_id=product_a,
                product_slug="a",
                product_name="A",
                category_id=category,
                category_name="Cat",
                variant_id=uuid4(),
                variant_sku="A1",
                variant_name="A1",
                list_unit_price_minor=100,
                unit_price_minor=100,
                quantity=1,
                coupon_code="SAVE10",
                country_code="US",
            )
        ),
        sale_from_create(
            SaleCreate(
                occurred_at=now,
                product_id=product_b,
                product_slug="b",
                product_name="B",
                category_id=category,
                category_name="Cat",
                variant_id=uuid4(),
                variant_sku="B1",
                variant_name="B1",
                list_unit_price_minor=50,
                unit_price_minor=50,
                quantity=4,
                country_code="US",
            )
        ),
    ]
    ranked = bestsellers(events, metric="units", limit=1)
    assert ranked.items[0].product_id == product_b
    assert summarize(events).units_sold == 5
    assert by_category(events).items[0].units_sold == 5
    assert len(timeseries(events, bucket="hour").points) >= 1
    only_coupon = filter_sales(events, coupon_code="save10")
    assert len(only_coupon) == 1
