"""Seed a shoes demo catalog through the JWT-protected master HTTP API.

Creates 2 parent categories, 3 children each, 3 products each, 3 variants each
(2 + 6 + 18 + 54 = 80 writes), then publishes the Redis catalog snapshot.

Stdlib only (no venv required on the host). Reads ADMIN_* from the process env or
a local .env file. Base URL defaults to http://127.0.0.1:8001; override with
MASTER_API_BASE_URL (include /api when calling Nginx).

  python3 -m scripts.seed_shoes_via_master_api
  MASTER_API_BASE_URL=https://ecommerce.terabitventure.com/api \\
    python3 -m scripts.seed_shoes_via_master_api

Not idempotent: re-running creates duplicate categories/products (variant SKUs 409).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from uuid import UUID

from scripts.master_api_client import MasterApiError, login_from_env

# size label -> price uplift over the product base (IRR minor units)
_SIZES: tuple[tuple[str, int], ...] = (
    ("S", 0),
    ("M", 0),
    ("L", 100_000),
)


@dataclass(frozen=True)
class ProductSpec:
    name: str
    description: str
    sku_prefix: str
    base_price_minor: int


@dataclass(frozen=True)
class CategorySpec:
    name: str
    description: str
    sort_order: int
    products: tuple[ProductSpec, ...]


@dataclass(frozen=True)
class MotherSpec:
    name: str
    description: str
    sort_order: int
    children: tuple[CategorySpec, ...]


CATALOG: tuple[MotherSpec, ...] = (
    MotherSpec(
        name="Men",
        description="Footwear for men",
        sort_order=10,
        children=(
            CategorySpec(
                name="Men Running",
                description="Performance running shoes",
                sort_order=11,
                products=(
                    ProductSpec(
                        "AeroStride Runner",
                        "Breathable mesh trainer for daily road runs",
                        "AERO-M",
                        2_450_000,
                    ),
                    ProductSpec(
                        "TrailPeak Pro",
                        "Trail grip sole for mixed terrain",
                        "TRAIL-M",
                        3_200_000,
                    ),
                    ProductSpec(
                        "PaceLite Elite",
                        "Race-day lightweight racing shoe",
                        "PACE-M",
                        4_100_000,
                    ),
                ),
            ),
            CategorySpec(
                name="Men Casual",
                description="Everyday sneakers and loafers",
                sort_order=12,
                products=(
                    ProductSpec(
                        "CityWalk Classic",
                        "Daily canvas sneaker",
                        "CITY-M",
                        1_800_000,
                    ),
                    ProductSpec(
                        "UrbanSoft Loafer",
                        "Comfort slip-on casual",
                        "URBN-M",
                        2_100_000,
                    ),
                    ProductSpec(
                        "MetroSlip Knit",
                        "Knit slip-on for city wear",
                        "METRO-M",
                        2_400_000,
                    ),
                ),
            ),
            CategorySpec(
                name="Men Boots",
                description="Outdoor and formal boots",
                sort_order=13,
                products=(
                    ProductSpec(
                        "RidgeGuard Hiker",
                        "Waterproof hiking boot",
                        "RIDGE-M",
                        5_500_000,
                    ),
                    ProductSpec(
                        "Forge Leather Boot",
                        "Formal leather boot",
                        "FORGE-M",
                        6_200_000,
                    ),
                    ProductSpec(
                        "NorthTrail Chelsea",
                        "Classic chelsea boot",
                        "NORTH-M",
                        4_800_000,
                    ),
                ),
            ),
        ),
    ),
    MotherSpec(
        name="Women",
        description="Footwear for women",
        sort_order=20,
        children=(
            CategorySpec(
                name="Women Running",
                description="Lightweight running shoes",
                sort_order=21,
                products=(
                    ProductSpec(
                        "SwiftStep Runner",
                        "Cushioned road running shoe",
                        "SWIFT-W",
                        2_400_000,
                    ),
                    ProductSpec(
                        "CloudPath Air",
                        "Soft daily trainer",
                        "CLOUD-W",
                        2_900_000,
                    ),
                    ProductSpec(
                        "PulseRun Flex",
                        "Flexible knit upper runner",
                        "PULSE-W",
                        3_500_000,
                    ),
                ),
            ),
            CategorySpec(
                name="Women Casual",
                description="Lifestyle sneakers and flats",
                sort_order=22,
                products=(
                    ProductSpec(
                        "BloomWalk Sneaker",
                        "Lifestyle sneaker",
                        "BLOOM-W",
                        1_900_000,
                    ),
                    ProductSpec(
                        "SoftDay Flat",
                        "Leather everyday flat",
                        "SOFT-W",
                        2_200_000,
                    ),
                    ProductSpec(
                        "NovaKnit Slip",
                        "Knit slip-on casual",
                        "NOVA-W",
                        2_600_000,
                    ),
                ),
            ),
            CategorySpec(
                name="Women Heels",
                description="Dress heels and pumps",
                sort_order=23,
                products=(
                    ProductSpec(
                        "VelvetRise Pump",
                        "Classic dress pump",
                        "VELVET-W",
                        3_100_000,
                    ),
                    ProductSpec(
                        "AuraStiletto",
                        "Evening stiletto heel",
                        "AURA-W",
                        3_800_000,
                    ),
                    ProductSpec(
                        "LunaBlock Heel",
                        "Stable block heel",
                        "LUNA-W",
                        3_400_000,
                    ),
                ),
            ),
        ),
    ),
)


def catalog_counts(catalog: tuple[MotherSpec, ...] = CATALOG) -> dict[str, int]:
    mothers = len(catalog)
    children = sum(len(mother.children) for mother in catalog)
    products = sum(len(child.products) for mother in catalog for child in mother.children)
    variants = products * len(_SIZES)
    return {
        "mothers": mothers,
        "children": children,
        "products": products,
        "variants": variants,
    }


def seed(base_url: str | None = None) -> dict[str, int]:
    client, resolved_base = login_from_env(base_url)
    print(f"logged in @ {resolved_base}")

    counts = {"categories": 0, "products": 0, "variants": 0}
    for mother in CATALOG:
        parent = client.create_category(
            name=mother.name,
            description=mother.description,
            sort_order=mother.sort_order,
        )
        parent_id = UUID(parent["id"])
        counts["categories"] += 1
        print(f"category {parent['name']} ({parent['slug']})")

        for child in mother.children:
            leaf = client.create_category(
                name=child.name,
                description=child.description,
                sort_order=child.sort_order,
                parent_id=parent_id,
            )
            leaf_id = UUID(leaf["id"])
            counts["categories"] += 1
            print(f"  category {leaf['name']} ({leaf['slug']})")

            for product_spec in child.products:
                product = client.create_product(
                    category_id=leaf_id,
                    name=product_spec.name,
                    description=product_spec.description,
                )
                product_id = UUID(product["id"])
                counts["products"] += 1
                print(f"    product {product['name']} ({product['slug']})")

                for size, uplift in _SIZES:
                    variant = client.create_variant(
                        product_id=product_id,
                        sku=f"{product_spec.sku_prefix}-{size}",
                        name=f"{product_spec.name} / {size}",
                        price_minor=product_spec.base_price_minor + uplift,
                    )
                    counts["variants"] += 1
                    print(
                        f"      variant {variant['sku']} "
                        f"{variant['price_minor']} {variant['currency']}"
                    )

    published = client.publish()
    print(
        "published revision "
        f"{published.get('revision_number')} "
        f"categories={published.get('category_count')} "
        f"products={published.get('product_count')}"
    )
    return counts


def main() -> None:
    expected = catalog_counts()
    print(
        "seeding shoes catalog: "
        f"{expected['mothers']} mothers, {expected['children']} children, "
        f"{expected['products']} products, {expected['variants']} variants"
    )
    try:
        counts = seed()
    except MasterApiError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    except OSError as exc:
        print(f"connection failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        "done: "
        f"{counts['categories']} categories, "
        f"{counts['products']} products, "
        f"{counts['variants']} variants"
    )


if __name__ == "__main__":
    main()
