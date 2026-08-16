"""Generate random master sales and view events from the active catalog.

Writes to PostgreSQL as ecommerce_owner (MIGRATION_DATABASE_URL). Requires an
active catalog revision with at least one product variant.

  python -m scripts.seed_master_traffic
  python -m scripts.seed_master_traffic --sessions 80 --days 14 --seed 42
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from src.catalog.repository import CatalogNotAvailableError, MasterCatalogRepository
from src.catalog.schemas import CatalogSnapshot, CategorySnapshot, ProductSnapshot, VariantSnapshot
from src.core.config import get_settings
from src.infrastructure.database import OwnerDatabase
from src.sales.capture import sale_from_create
from src.sales.repository import MasterSalesRepository
from src.sales.schemas import SaleCreate, SaleEvent
from src.views.capture import view_from_create
from src.views.repository import MasterViewsRepository
from src.views.schemas import ViewCreate, ViewEvent

LOCATIONS: tuple[tuple[str, str, str], ...] = (
    ("IR", "Tehran", "Tehran"),
    ("IR", "Isfahan", "Isfahan"),
    ("IR", "Fars", "Shiraz"),
    ("US", "California", "Los Angeles"),
    ("US", "New York", "New York"),
    ("DE", "Berlin", "Berlin"),
    ("GB", "England", "London"),
    ("AE", "Dubai", "Dubai"),
)

USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 14) Chrome/128.0.6613.99",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Safari/604.1",
)

REFERRERS: tuple[str | None, ...] = (
    None,
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://instagram.com/",
    "/",
)

SEARCH_QUERIES: tuple[str, ...] = (
    "shoes",
    "running",
    "black",
    "sale",
    "classic",
    "boots",
)

COUPONS: tuple[str | None, ...] = (None, None, None, "SAVE10")


def _sale_price(list_price_minor: int, discount_percent: int) -> int:
    if discount_percent <= 0:
        return list_price_minor
    return list_price_minor * (100 - discount_percent) // 100


def _sellable_products(catalog: CatalogSnapshot) -> list[ProductSnapshot]:
    return [
        product
        for product in catalog.products
        if product.is_active and any(variant.is_active for variant in product.variants)
    ]


def _active_variant(product: ProductSnapshot, rng: random.Random) -> VariantSnapshot:
    variants = [variant for variant in product.variants if variant.is_active]
    return rng.choice(variants)


def _category_for(catalog: CatalogSnapshot, category_id: UUID) -> CategorySnapshot | None:
    return next((item for item in catalog.categories if item.id == category_id), None)


def _random_when(rng: random.Random, *, days: int) -> datetime:
    offset = rng.random() * days * 24 * 3600
    return datetime.now(UTC) - timedelta(seconds=offset)


def _location(rng: random.Random) -> tuple[str, str, str]:
    return rng.choice(LOCATIONS)


def _session_id(rng: random.Random) -> str:
    return f"seed-{rng.randrange(16**16):016x}"[:80]


def _build_views_for_session(
    *,
    rng: random.Random,
    catalog: CatalogSnapshot,
    products: list[ProductSnapshot],
    session_id: str,
    started_at: datetime,
) -> list[ViewEvent]:
    country, region, city = _location(rng)
    user_agent = rng.choice(USER_AGENTS)
    referrer = rng.choice(REFERRERS)
    events: list[ViewEvent] = []
    cursor = started_at

    def stamp() -> datetime:
        nonlocal cursor
        cursor = cursor + timedelta(seconds=rng.randint(4, 90))
        return cursor

    def emit(body: ViewCreate) -> None:
        events.append(view_from_create(body, sandbox_session_id=session_id))

    emit(
        ViewCreate(
            occurred_at=stamp(),
            source="import",
            kind="visit",
            path="/",
            referrer=referrer,
            country_code=country,
            region=region,
            city=city,
            user_agent=user_agent,
            notes="seed_master_traffic",
        )
    )
    if rng.random() < 0.35:
        emit(
            ViewCreate(
                occurred_at=stamp(),
                source="import",
                kind="search",
                path="/catalog",
                query=rng.choice(SEARCH_QUERIES),
                country_code=country,
                region=region,
                city=city,
                user_agent=user_agent,
                notes="seed_master_traffic",
            )
        )
    if catalog.categories and rng.random() < 0.45:
        category = rng.choice(catalog.categories)
        emit(
            ViewCreate(
                occurred_at=stamp(),
                source="import",
                kind="category_view",
                path=f"/categories/{category.slug}",
                category_id=category.id,
                category_slug=category.slug,
                category_name=category.name,
                country_code=country,
                region=region,
                city=city,
                user_agent=user_agent,
                notes="seed_master_traffic",
            )
        )
    if rng.random() < 0.5:
        emit(
            ViewCreate(
                occurred_at=stamp(),
                source="import",
                kind="listing_view",
                path="/catalog",
                country_code=country,
                region=region,
                city=city,
                user_agent=user_agent,
                notes="seed_master_traffic",
            )
        )

    viewed = rng.sample(products, k=min(len(products), rng.randint(1, 5)))
    for product in viewed:
        category = _category_for(catalog, product.category_id)
        emit(
            ViewCreate(
                occurred_at=stamp(),
                source="import",
                kind="product_view",
                path=f"/products/{product.slug}",
                product_id=product.id,
                product_slug=product.slug,
                product_name=product.name,
                category_id=product.category_id,
                category_slug=None if category is None else category.slug,
                category_name=None if category is None else category.name,
                country_code=country,
                region=region,
                city=city,
                user_agent=user_agent,
                notes="seed_master_traffic",
            )
        )
    return events


def _build_order_sales(
    *,
    rng: random.Random,
    catalog: CatalogSnapshot,
    products: list[ProductSnapshot],
    session_id: str,
    occurred_at: datetime,
) -> list[SaleEvent]:
    country, region, city = _location(rng)
    order_id = uuid4()
    line_count = rng.randint(1, min(4, len(products)))
    chosen = rng.sample(products, k=line_count)
    coupon = rng.choice(COUPONS)
    sales: list[SaleEvent] = []
    for index, product in enumerate(chosen):
        variant = _active_variant(product, rng)
        quantity = rng.randint(1, 3)
        unit = _sale_price(variant.price_minor, product.discount_percent)
        discount = 0 if coupon is None else unit * quantity // 10
        shipping = 0 if index else rng.choice((0, 500, 1500))
        category = _category_for(catalog, product.category_id)
        body = SaleCreate(
            occurred_at=occurred_at,
            source="import",
            order_id=order_id,
            line_index=index,
            product_id=product.id,
            product_slug=product.slug,
            product_name=product.name,
            category_id=product.category_id,
            category_slug=None if category is None else category.slug,
            category_name=None if category is None else category.name,
            variant_id=variant.id,
            variant_sku=variant.sku,
            variant_name=variant.name,
            currency=variant.currency,
            quantity=quantity,
            list_unit_price_minor=variant.price_minor,
            unit_price_minor=unit,
            allocated_discount_minor=discount,
            allocated_shipping_minor=shipping,
            allocated_tax_minor=0,
            product_discount_percent=product.discount_percent,
            coupon_code=coupon,
            country_code=country,
            region=region,
            city=city,
            postal_code=str(rng.randint(10000, 99999)),
            notes="seed_master_traffic",
        )
        sales.append(sale_from_create(body, sandbox_session_id=session_id))
    return sales


def generate_traffic(
    catalog: CatalogSnapshot,
    *,
    sessions: int,
    days: int,
    purchase_rate: float,
    rng: random.Random,
) -> tuple[list[ViewEvent], list[SaleEvent]]:
    products = _sellable_products(catalog)
    if not products:
        raise SystemExit("active catalog has no sellable product variants")
    views: list[ViewEvent] = []
    sales: list[SaleEvent] = []
    for _ in range(sessions):
        session_id = _session_id(rng)
        started = _random_when(rng, days=days)
        session_views = _build_views_for_session(
            rng=rng,
            catalog=catalog,
            products=products,
            session_id=session_id,
            started_at=started,
        )
        views.extend(session_views)
        if rng.random() < purchase_rate:
            last_view = session_views[-1].occurred_at
            sales.extend(
                _build_order_sales(
                    rng=rng,
                    catalog=catalog,
                    products=products,
                    session_id=session_id,
                    occurred_at=last_view + timedelta(seconds=rng.randint(20, 180)),
                )
            )
    return views, sales


async def seed(args: argparse.Namespace) -> None:
    settings = get_settings()
    database = OwnerDatabase(settings)
    catalog_repo = MasterCatalogRepository(database.session_factory)
    sales_repo = MasterSalesRepository(database.session_factory)
    views_repo = MasterViewsRepository(database.session_factory)
    rng = random.Random(args.seed)
    try:
        catalog = await catalog_repo.get_active_snapshot()
    except CatalogNotAvailableError as exc:
        raise SystemExit(str(exc)) from exc
    views, sales = generate_traffic(
        catalog,
        sessions=args.sessions,
        days=args.days,
        purchase_rate=args.purchase_rate,
        rng=rng,
    )
    try:
        await views_repo.insert_many(views)
        await sales_repo.insert_many(sales)
    finally:
        await database.close()
    print(
        f"inserted {len(views)} view events and {len(sales)} sale events "
        f"across {args.sessions} sessions (catalog revision {catalog.revision_number})"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=60, help="synthetic shopper sessions")
    parser.add_argument("--days", type=int, default=14, help="spread events over this many days")
    parser.add_argument(
        "--purchase-rate",
        type=float,
        default=0.28,
        help="probability a session converts to an order (0-1)",
    )
    parser.add_argument("--seed", type=int, default=None, help="optional RNG seed")
    args = parser.parse_args()
    if args.sessions < 1:
        parser.error("--sessions must be >= 1")
    if args.days < 1:
        parser.error("--days must be >= 1")
    if not 0 <= args.purchase_rate <= 1:
        parser.error("--purchase-rate must be between 0 and 1")
    return args


def main() -> None:
    asyncio.run(seed(parse_args()))


if __name__ == "__main__":
    main()
