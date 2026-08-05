from typing import cast
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import AnyHttpUrl
from test_sandbox_engine import service_fixture

from src.commerce.router import router as commerce_router
from src.commerce.service import (
    BasisPointTaxPolicy,
    CommerceError,
    CommerceLimits,
    CommerceService,
    DemoCouponPolicy,
    FlatShippingPolicy,
    PricingService,
)
from src.core.config import get_settings
from src.sandbox.models import AddressRecord
from src.sandbox.router import SandboxAPIError
from src.sandbox.router import router as sandbox_router


def commerce(sandbox: object, *, stock: int = 5) -> CommerceService:
    from src.sandbox.service import SandboxService

    return CommerceService(
        cast(SandboxService, sandbox),
        PricingService(
            coupon=DemoCouponPolicy(),
            shipping=FlatShippingPolicy(flat_minor=0, free_threshold_minor=0),
            tax_policy=BasisPointTaxPolicy(0),
        ),
        CommerceLimits(
            page_max=50,
            cart_quantity_max=4,
            address_max=2,
            default_stock=stock,
        ),
    )


def address() -> AddressRecord:
    return AddressRecord(
        id=uuid4(),
        label="Home",
        recipient="Demo User",
        line1="1 Main Street",
        city="Example",
        postal_code="10000",
        country_code="US",
    )


@pytest.mark.asyncio
async def test_catalog_filters_sort_and_availability_use_merged_state() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    service = commerce(sandbox, stock=0)
    session_id, _nonce, _state = await sandbox.create()
    product = master.products[0]

    result = await service.products(
        session_id,
        page=1,
        page_size=10,
        search="BASE",
        category=str(product.category_id),
        min_price_minor=50,
        max_price_minor=150,
        available=False,
        sort="-price",
    )
    assert result.total == 1
    assert result.items[0].available is False
    assert result.items[0].variants[0].stock == 0

    absent = await service.products(
        session_id,
        page=1,
        page_size=10,
        search="missing",
        category=None,
        min_price_minor=None,
        max_price_minor=None,
        available=None,
        sort="name",
    )
    assert absent.total == 0


@pytest.mark.asyncio
async def test_cart_isolation_server_prices_quantities_and_conflict_retry() -> None:
    sandbox, redis, _secrets, master = await service_fixture()
    service = commerce(sandbox)
    first, _nonce, _state = await sandbox.create()
    second, _nonce, _state = await sandbox.create()
    variant_id = master.products[0].variants[0].id

    redis.conflicts = 1
    cart = await service.change_cart(first, variant_id, 2, add=True)
    assert cart.item_count == 2
    assert cart.subtotal_minor == 200
    assert cart.lines[0].unit_price_minor == 100
    assert (await service.cart(second)).lines == ()

    cart = await service.change_cart(first, variant_id, 1, add=True)
    assert cart.lines[0].quantity == 3
    with pytest.raises(CommerceError, match="Quantity exceeds"):
        await service.change_cart(first, variant_id, 2, add=True)


@pytest.mark.asyncio
async def test_checkout_rejects_stock_and_funds_then_is_atomic_and_idempotent() -> None:
    sandbox, redis, _secrets, master = await service_fixture()
    service = commerce(sandbox, stock=2)
    session_id, _nonce, _state = await sandbox.create()
    variant_id = master.products[0].variants[0].id
    shipping = address()
    await service.put_address(session_id, shipping)

    with pytest.raises(CommerceError, match="stock"):
        await service.change_cart(session_id, variant_id, 3, add=False)
    await service.change_cart(session_id, variant_id, 2, add=False)
    with pytest.raises(CommerceError, match="funds"):
        await service.checkout(session_id, shipping.id, None, "funds-attempt")

    await service.adjust_wallet(session_id, 1_000, "test credit", operation="credit")
    redis.conflicts = 1
    order = await service.checkout(session_id, shipping.id, "SAVE10", "checkout-1")
    state = await sandbox.inspect(session_id)
    assert order.total_minor == 180
    assert state.wallet.balance_minor == 820
    assert state.stock_overrides[master.products[0].variants[0].id] == 0
    assert state.inventory_ledger[-1].quantity_delta == -2
    assert state.inventory_ledger[-1].kind == "checkout_decrement"
    assert state.cart.lines == []
    assert len(state.orders.orders) == 1

    replay = await service.checkout(session_id, shipping.id, "SAVE10", "checkout-1")
    replay_state = await sandbox.inspect(session_id)
    assert replay.id == order.id
    assert replay_state.wallet.balance_minor == 820
    assert (
        len([entry for entry in replay_state.wallet.ledger if entry.kind == "checkout_debit"]) == 1
    )


@pytest.mark.asyncio
async def test_order_cancellation_and_refund_compensate_once() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    service = commerce(sandbox, stock=3)
    session_id, _nonce, _state = await sandbox.create()
    variant_id = master.products[0].variants[0].id
    shipping = address()
    await service.put_address(session_id, shipping)
    await service.adjust_wallet(session_id, 1_000, "credit", operation="credit")
    await service.change_cart(session_id, variant_id, 1, add=False)
    order = await service.checkout(session_id, shipping.id, None, "cancel-me")

    cancelled = await service.transition_order(session_id, order.id, "cancel")
    state = await sandbox.inspect(session_id)
    assert cancelled.status == "cancelled"
    assert state.wallet.balance_minor == 1_000
    assert state.stock_overrides[master.products[0].variants[0].id] == 3
    assert state.inventory_ledger[-1].kind == "cancellation_restock"
    with pytest.raises(CommerceError, match="already final"):
        await service.transition_order(session_id, order.id, "refund")

    await service.change_cart(session_id, variant_id, 1, add=False)
    second = await service.checkout(session_id, shipping.id, None, "refund-me")
    refunded = await service.transition_order(session_id, second.id, "refund")
    assert refunded.status == "refunded"


@pytest.mark.asyncio
async def test_wishlist_addresses_and_paginated_append_only_wallet_ledger() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    service = commerce(sandbox)
    session_id, _nonce, _state = await sandbox.create()
    product_id = master.products[0].id

    items = await service.change_wishlist(session_id, product_id, remove=False)
    assert [item.id for item in items] == [product_id]
    assert await service.change_wishlist(session_id, product_id, remove=True) == ()

    first = address()
    second = address().model_copy(update={"label": "Office"})
    await service.put_address(session_id, first)
    await service.put_address(session_id, second)
    with pytest.raises(CommerceError, match="Address limit"):
        await service.put_address(session_id, address())

    await service.adjust_wallet(session_id, 300, "credit", operation="credit")
    await service.adjust_wallet(session_id, 100, "debit", operation="debit")
    state = await sandbox.inspect(session_id)
    assert state.wallet.balance_minor == 200
    ledger = await service.ledger(session_id, page=1, page_size=1)
    assert ledger.total == 2
    assert ledger.pages == 2
    assert ledger.items[0].kind == "admin_debit"
    with pytest.raises(CommerceError, match="funds"):
        await service.adjust_wallet(session_id, 201, "too much", operation="debit")


@pytest.mark.asyncio
async def test_commerce_api_requires_csrf_and_ignores_client_totals() -> None:
    sandbox, _redis, secrets, master = await service_fixture()
    service = commerce(sandbox)
    settings = get_settings().model_copy(
        update={"cors_origins": [AnyHttpUrl("https://client.test")]}
    )
    app = FastAPI()
    app.state.settings = settings
    app.state.session_secrets = secrets
    app.state.sandbox_service = sandbox
    app.state.commerce_service = service
    app.include_router(sandbox_router)
    app.include_router(commerce_router)

    @app.exception_handler(SandboxAPIError)
    async def sandbox_handler(_request: Request, exc: SandboxAPIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(CommerceError)
    async def commerce_handler(_request: Request, exc: CommerceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://client.test"
    ) as client:
        created = await client.get(
            "/v1/sandbox/session/create", headers={"Origin": "https://client.test"}
        )
        token = created.json()["csrf_token"]
        variant_id = str(master.products[0].variants[0].id)
        rejected = await client.post(
            "/v1/cart/items", json={"variant_id": variant_id, "quantity": 1}
        )
        assert rejected.status_code == 403
        headers = {"Origin": "https://client.test", "X-CSRF-Token": token}
        added = await client.post(
            "/v1/cart/items",
            json={"variant_id": variant_id, "quantity": 1, "total_minor": 1},
            headers=headers,
        )
        assert added.status_code == 422
        added = await client.post(
            "/v1/cart/items",
            json={"variant_id": variant_id, "quantity": 1},
            headers=headers,
        )
        assert added.status_code == 200
        assert added.json()["subtotal_minor"] == 100
        listed = await client.get("/v1/catalog/products?search=base")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
