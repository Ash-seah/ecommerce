"""Traffic / views analytics tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_commerce_services import commerce
from test_sandbox_engine import service_fixture

from src.views.analytics import (
    by_kind,
    summarize,
    timeseries,
    top_categories,
    top_paths,
    top_products,
)
from src.views.capture import view_from_create
from src.views.schemas import ViewCreate, ViewRecordRequest, ViewUpdate
from src.views.service import SandboxViewsService, ViewsAdminError


@pytest.mark.asyncio
async def test_product_fetch_and_beacon_write_view_events() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    service = commerce(sandbox, stock=5)
    views = SandboxViewsService(sandbox)
    session_id, _nonce, _state = await sandbox.create()
    product = master.products[0]
    category = master.categories[0]

    await service.product(session_id, str(product.id))
    await service.category(session_id, str(category.id))
    listed = await views.list_views(session_id, page=1, page_size=20)
    assert listed.total == 2
    kinds = {item.kind for item in listed.items}
    assert kinds == {"product_view", "category_view"}

    await service.record_view(
        session_id,
        ViewRecordRequest(kind="visit", path="/", referrer="https://news.test"),
        user_agent="DemoBrowser/1.0",
    )
    await service.record_view(
        session_id,
        ViewRecordRequest(kind="search", path="/products", query="shoes"),
    )

    summary = await views.summary(session_id)
    assert summary.product_views == 1
    assert summary.category_views == 1
    assert summary.visits == 1
    assert summary.searches == 1
    assert summary.total_events == 4

    series = await views.timeseries(session_id, bucket="day")
    assert len(series.points) == 1
    assert series.points[0].total == 4

    top = await views.top_products(session_id, limit=5)
    assert top.items[0].product_id == product.id
    assert top.items[0].views == 1
    assert (await views.top_categories(session_id, limit=5)).items[0].views >= 1
    assert (await views.top_paths(session_id, limit=5)).items
    assert (await views.by_kind(session_id)).items
    feed = await views.feed(session_id, since=None, limit=10)
    assert len(feed.items) == 4


@pytest.mark.asyncio
async def test_admin_can_seed_alter_and_void_views() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    views = SandboxViewsService(sandbox)
    session_id, _nonce, _state = await sandbox.create()
    product = master.products[0]
    created = await views.create(
        session_id,
        ViewCreate(
            kind="product_view",
            path=f"/products/{product.slug}",
            product_id=product.id,
            product_slug=product.slug,
            product_name=product.name,
            category_id=product.category_id,
            country_code="US",
            city="Austin",
            notes="seeded traffic",
        ),
    )
    assert created.source == "admin"
    updated = await views.update(
        session_id, created.id, ViewUpdate(path="/products/custom", notes="tweaked")
    )
    assert updated.path == "/products/custom"
    assert updated.notes == "tweaked"
    voided = await views.void(session_id, created.id, reason="bot traffic")
    assert voided.status == "voided"
    await views.delete(session_id, created.id)
    with pytest.raises(ViewsAdminError):
        await views.get(session_id, created.id)


def test_views_analytics_helpers() -> None:
    now = datetime.now(UTC)
    product = uuid4()
    events = [
        view_from_create(
            ViewCreate(
                occurred_at=now - timedelta(hours=1),
                kind="visit",
                path="/",
            )
        ),
        view_from_create(
            ViewCreate(
                occurred_at=now,
                kind="product_view",
                path="/p/a",
                product_id=product,
                product_slug="a",
                product_name="A",
            )
        ),
        view_from_create(
            ViewCreate(
                occurred_at=now,
                kind="product_view",
                path="/p/a",
                product_id=product,
                product_slug="a",
                product_name="A",
            )
        ),
    ]
    summary = summarize(events)
    assert summary.visits == 1
    assert summary.product_views == 2
    assert top_products(events).items[0].views == 2
    assert top_categories(
        [
            *events,
            view_from_create(
                ViewCreate(
                    kind="category_view",
                    category_id=uuid4(),
                    category_slug="c",
                    category_name="C",
                    path="/c",
                )
            ),
        ]
    ).items[0].views == 1
    assert top_paths(events).items[0].hits >= 1
    assert by_kind(events).items
    assert timeseries(events, bucket="hour").points[-1].product_views >= 1
