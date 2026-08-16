"""Unit tests for lightweight recommendation scoring and association precompute."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from src.catalog.cache import CatalogSnapshotCache
from src.catalog.schemas import (
    CatalogSnapshot,
    CategorySnapshot,
    ProductSnapshot,
    VariantSnapshot,
)
from src.commerce.delivery import default_delivery_catalog
from src.commerce.service import (
    BasisPointTaxPolicy,
    CommerceLimits,
    CommerceService,
    DemoCouponPolicy,
    PricingService,
)
from src.recommendations.algorithms import (
    aggregate_ranked_ids,
    bought_together_map,
    session_next_map,
)
from src.recommendations.scoring import intent_weight
from src.recommendations.service import RecommendationService
from src.recommendations.store import RecommendationStore
from src.recommendations.worker import (
    RecommendationWorker,
    baskets_from_sales,
    sessions_from_views,
)
from src.sales.schemas import SaleEvent
from src.sandbox.security import SessionSecrets
from src.sandbox.service import RedisProtocol, SandboxService
from src.views.schemas import ViewEvent
from tests.test_sandbox_engine import FakeRedis, NullRepository


def _sale(
    *,
    order_id: object,
    product_id: object,
    sku: str,
    now: datetime,
) -> SaleEvent:
    return SaleEvent(
        id=uuid4(),
        occurred_at=now,
        recorded_at=now,
        order_id=order_id,  # type: ignore[arg-type]
        product_id=product_id,  # type: ignore[arg-type]
        product_slug=sku.lower(),
        product_name=sku,
        category_id=uuid4(),
        variant_id=uuid4(),
        variant_sku=sku,
        variant_name=sku,
        currency="USD",
        quantity=1,
        list_unit_price_minor=100,
        unit_price_minor=100,
        line_gross_minor=100,
        line_net_minor=100,
    )


def _catalog_with_products() -> CatalogSnapshot:
    category_a = uuid4()
    category_b = uuid4()
    products = []
    for index, category_id in enumerate((category_a, category_a, category_a, category_b)):
        product_id = uuid4()
        products.append(
            ProductSnapshot(
                id=product_id,
                category_id=category_id,
                slug=f"product-{index}",
                name=f"Product {index}",
                description=None,
                variants=(
                    VariantSnapshot(
                        id=uuid4(),
                        sku=f"SKU-{index}",
                        name="Default",
                        price_minor=1000 + index,
                        currency="USD",
                    ),
                ),
                media=(),
            )
        )
    return CatalogSnapshot(
        revision_id=uuid4(),
        revision_number=1,
        revision_label="v1",
        generated_at=datetime.now(UTC),
        categories=(
            CategorySnapshot(
                id=category_a,
                parent_id=None,
                slug="cat-a",
                name="Category A",
                description=None,
                sort_order=0,
            ),
            CategorySnapshot(
                id=category_b,
                parent_id=None,
                slug="cat-b",
                name="Category B",
                description=None,
                sort_order=1,
            ),
        ),
        products=tuple(products),
    )


async def _commerce_fixture() -> tuple[
    CommerceService, RecommendationService, FakeRedis, str, CatalogSnapshot
]:
    redis = FakeRedis()
    master = _catalog_with_products()
    cache = CatalogSnapshotCache(redis, NullRepository(), key_prefix="ecommerce")
    await redis.set(cache.snapshot_key(1), master.model_dump_json(), ex=7200)
    redis.values["ecommerce:catalog:current"] = "1"
    secrets = SessionSecrets(b"s" * 32, b"c" * 32)
    sandbox = SandboxService(
        cast(RedisProtocol, redis), cache, secrets, key_prefix="ecommerce"
    )
    store = RecommendationStore(redis, key_prefix="ecommerce")
    recommendations = RecommendationService(store)
    commerce = CommerceService(
        sandbox,
        PricingService(
            coupon=DemoCouponPolicy(),
            delivery=default_delivery_catalog(
                standard_minor=500,
                express_minor=1500,
                pickup_minor=0,
                free_threshold_minor=5000,
            ),
            tax_policy=BasisPointTaxPolicy(0),
        ),
        CommerceLimits(page_max=100, cart_quantity_max=20, address_max=10, default_stock=10),
        recommendations=recommendations,
    )
    session_id, _nonce, _state = await sandbox.create()
    return commerce, recommendations, redis, session_id, master


def test_intent_weights() -> None:
    assert intent_weight("product_view") == 1
    assert intent_weight("cart_add") == 5
    assert intent_weight("wishlist_add") == 10
    assert intent_weight("purchase") == 25


def test_bought_together_ranks_co_purchases() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    mapping = bought_together_map([{a, b}, {a, b}, {a, c}], min_support=2, limit=5)
    assert mapping[a][0] == b
    assert b in mapping
    assert c not in mapping.get(a, [])


def test_session_next_and_aggregate() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    next_map = session_next_map({"s1": [a, b, c], "s2": [a, b]}, limit=5)
    assert next_map[a][0] == b
    ranked = aggregate_ranked_ids([next_map[a], [c, b]], exclude={a}, limit=5)
    assert ranked[0] in {b, c}


def test_baskets_and_sessions_helpers() -> None:
    order_id = uuid4()
    product_a, product_b = uuid4(), uuid4()
    now = datetime.now(UTC)
    sales = [
        _sale(order_id=order_id, product_id=product_a, sku="A1", now=now),
        _sale(order_id=order_id, product_id=product_b, sku="B1", now=now),
    ]
    assert baskets_from_sales(sales) == [{product_a, product_b}]
    views = [
        ViewEvent(
            id=uuid4(),
            occurred_at=now,
            recorded_at=now,
            kind="product_view",
            product_id=product_a,
            sandbox_session_id="sess-1",
        ),
        ViewEvent(
            id=uuid4(),
            occurred_at=now,
            recorded_at=now,
            kind="product_view",
            product_id=product_b,
            sandbox_session_id="sess-1",
        ),
    ]
    assert sessions_from_views(views)["sess-1"] == [product_a, product_b]


@pytest.mark.asyncio
async def test_intent_scoring_from_wishlist_and_cart() -> None:
    commerce, recommendations, redis, session_id, master = await _commerce_fixture()
    product = master.products[0]
    variant = product.variants[0]

    await commerce.change_wishlist(session_id, product.id, remove=False)
    await commerce.change_cart(session_id, variant.id, 1, add=True)
    pending = list(recommendations._tasks)
    if pending:
        await asyncio.gather(*pending)

    score = await recommendations._store.intent_score(product.id)
    assert score == intent_weight("wishlist_add") + intent_weight("cart_add")
    assert str(product.id) in redis.zsets["ecommerce:recs:intent"]


@pytest.mark.asyncio
async def test_similar_products_rank_by_intent() -> None:
    commerce, recommendations, _redis, session_id, master = await _commerce_fixture()
    same_category = [
        product
        for product in master.products
        if product.category_id == master.products[0].category_id
    ]
    anchor = same_category[0]
    hot = same_category[1]
    await recommendations.record_intent(hot.id, "purchase")
    page = await commerce.similar_products(session_id, str(anchor.id), page=1, page_size=10)
    assert page.items
    assert page.items[0].id == hot.id
    assert anchor.id not in {item.id for item in page.items}


@pytest.mark.asyncio
async def test_cross_sell_uses_precomputed_neighbors() -> None:
    commerce, recommendations, _redis, session_id, master = await _commerce_fixture()
    anchor, partner = master.products[0], master.products[1]
    await recommendations._store.replace_bought_together({anchor.id: [partner.id]})
    page = await commerce.cross_sell_products(session_id, str(anchor.id), page=1, page_size=5)
    assert [item.id for item in page.items] == [partner.id]


@pytest.mark.asyncio
async def test_personal_falls_back_to_trending_when_cold() -> None:
    commerce, _recommendations, _redis, session_id, _master = await _commerce_fixture()
    page = await commerce.personal_recommendations(session_id, page=1, page_size=5)
    assert page.page == 1
    assert page.total == 0


@pytest.mark.asyncio
async def test_worker_writes_association_keys() -> None:
    redis = FakeRedis()
    store = RecommendationStore(redis, key_prefix="ecommerce")
    order_a, order_b = uuid4(), uuid4()
    product_a, product_b = uuid4(), uuid4()
    now = datetime.now(UTC)

    async def load_sales() -> list[SaleEvent]:
        return [
            _sale(order_id=order_a, product_id=product_a, sku="A1", now=now),
            _sale(order_id=order_a, product_id=product_b, sku="B1", now=now),
            _sale(order_id=order_b, product_id=product_a, sku="A2", now=now),
            _sale(order_id=order_b, product_id=product_b, sku="B2", now=now),
        ]

    async def load_views() -> list[ViewEvent]:
        return [
            ViewEvent(
                id=uuid4(),
                occurred_at=now,
                recorded_at=now,
                kind="product_view",
                product_id=product_a,
                sandbox_session_id="s1",
            ),
            ViewEvent(
                id=uuid4(),
                occurred_at=now,
                recorded_at=now,
                kind="product_view",
                product_id=product_b,
                sandbox_session_id="s1",
            ),
        ]

    worker = RecommendationWorker(
        store,
        load_sales=load_sales,
        load_views=load_views,
        min_support=2,
        association_limit=5,
        interval_seconds=60,
    )
    stats = await worker.run_once()
    assert stats["bought_together_products"] >= 2
    assert await store.get_bought_together(product_a) == [product_b]
    assert await store.get_session_next(product_a) == [product_b]
