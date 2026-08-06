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

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

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


class MasterApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"master API {status}: {body}")
        self.status = status
        self.body = body


class MasterApiClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._token = token

    def login(self, username: str, password: str) -> str:
        payload = self._request(
            "POST",
            "/v1/master/auth/login",
            {"username": username, "password": password},
            auth=False,
        )
        token = payload["access_token"]
        if not isinstance(token, str) or not token:
            raise MasterApiError(500, "login response missing access_token")
        self._token = token
        return token

    def create_category(
        self,
        *,
        name: str,
        description: str,
        sort_order: int,
        parent_id: UUID | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "description": description,
            "sort_order": sort_order,
            "is_active": True,
        }
        if parent_id is not None:
            body["parent_id"] = str(parent_id)
        return self._request("POST", "/v1/master/categories", body)["category"]

    def create_product(self, *, category_id: UUID, name: str, description: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/master/products",
            {
                "category_id": str(category_id),
                "name": name,
                "description": description,
                "is_active": True,
            },
        )["product"]

    def create_variant(
        self,
        *,
        product_id: UUID,
        sku: str,
        name: str,
        price_minor: int,
        currency: str = "IRR",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/master/variants",
            {
                "product_id": str(product_id),
                "sku": sku,
                "name": name,
                "price_minor": price_minor,
                "currency": currency,
                "is_active": True,
            },
        )["variant"]

    def publish(self) -> dict[str, Any]:
        return self._request("POST", "/v1/master/catalog/publish", {})

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        *,
        auth: bool = True,
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if auth:
            if not self._token:
                raise MasterApiError(401, "not logged in")
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            f"{self._base}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MasterApiError(exc.code, detail) from exc
        if not raw:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise MasterApiError(500, f"expected JSON object, got {type(payload).__name__}")
        return payload


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


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values


def _env(name: str, dotenv: dict[str, str], default: str | None = None) -> str:
    value = os.environ.get(name) or dotenv.get(name) or default
    if value is None or value == "":
        raise MasterApiError(500, f"missing required setting {name}")
    return value


def seed(base_url: str | None = None) -> dict[str, int]:
    repo_root = Path(__file__).resolve().parents[1]
    dotenv = {**_load_dotenv(repo_root / ".env"), **_load_dotenv(Path.cwd() / ".env")}
    username = _env("ADMIN_USERNAME", dotenv, "admin")
    password = _env("ADMIN_PASSWORD", dotenv, "admin123")
    api_port = _env("API_PORT", dotenv, "8001")
    resolved_base = (
        base_url or os.environ.get("MASTER_API_BASE_URL") or f"http://127.0.0.1:{api_port}"
    )
    client = MasterApiClient(resolved_base)
    client.login(username, password)
    print(f"logged in as {username} @ {resolved_base}")

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
