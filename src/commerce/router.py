"""Thin FastAPI v1 routers for commerce domain services."""

from collections.abc import Awaitable
from typing import Annotated, Literal, TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Request

from src.commerce.schemas import (
    AddressInput,
    AddressList,
    CartQuantityRequest,
    CartView,
    CategoryNode,
    CategoryPage,
    CheckoutRequest,
    DeliveryOptionList,
    LedgerPage,
    OrderPage,
    OrderTransitionRequest,
    ProductPage,
    ProductView,
    WalletAdjustmentRequest,
    WalletView,
    WishlistRequest,
    WishlistView,
)
from src.commerce.service import CommerceError, CommerceService
from src.reviews.schemas import (
    ReviewCreateRequest,
    ReviewList,
    ReviewResponse,
    ReviewUpdate,
)
from src.sandbox.models import AddressRecord, OrderRecord
from src.sandbox.router import SessionContext, _existing_context, _require_csrf
from src.sandbox.service import CatalogUnavailableError
from src.views.schemas import ViewRecordRequest, ViewResponse

_T = TypeVar("_T")

router = APIRouter(prefix="/v1", tags=["commerce"])
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1)]


def _service(request: Request) -> CommerceService:
    service: CommerceService = request.app.state.commerce_service
    return service


def _bounded_page_size(request: Request, page_size: int) -> int:
    maximum: int = request.app.state.settings.commerce_page_max
    if page_size > maximum:
        raise CommerceError(422, "page_size_too_large", f"page_size cannot exceed {maximum}")
    return page_size


async def _write_context(request: Request, x_csrf_token: str | None) -> SessionContext:
    return await _require_csrf(request, x_csrf_token)


async def _catalog_call(operation: Awaitable[_T]) -> _T:
    try:
        return await operation
    except CatalogUnavailableError as exc:
        raise CommerceError(503, "catalog_unavailable", str(exc)) from exc


@router.get("/catalog/categories", response_model=CategoryPage)
async def list_categories(
    request: Request, page: Page = 1, page_size: PageSize = 20
) -> CategoryPage:
    context = await _existing_context(request)
    return await _catalog_call(
        _service(request).categories(
            context.session_id, page, _bounded_page_size(request, page_size)
        )
    )


@router.get("/catalog/categories/{identifier}", response_model=CategoryNode)
async def get_category(request: Request, identifier: str) -> CategoryNode:
    context = await _existing_context(request)
    return await _catalog_call(_service(request).category(context.session_id, identifier))


@router.get("/catalog/products", response_model=ProductPage)
async def list_products(
    request: Request,
    page: Page = 1,
    page_size: PageSize = 20,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    category: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    brand: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    min_price_minor: Annotated[int | None, Query(ge=0)] = None,
    max_price_minor: Annotated[int | None, Query(ge=0)] = None,
    available: Annotated[
        bool | None,
        Query(description="If set, only products with matching in-stock availability"),
    ] = None,
    min_stars: Annotated[int | None, Query(ge=1, le=5)] = None,
    max_stars: Annotated[int | None, Query(ge=1, le=5)] = None,
    stars: Annotated[int | None, Query(ge=1, le=5)] = None,
    sort: Literal[
        "name", "-name", "price", "-price", "rating", "-rating", "sold", "-sold"
    ] = "name",
) -> ProductPage:
    if (
        min_price_minor is not None
        and max_price_minor is not None
        and min_price_minor > max_price_minor
    ):
        raise CommerceError(422, "invalid_price_range", "Minimum price exceeds maximum")
    if min_stars is not None and max_stars is not None and min_stars > max_stars:
        raise CommerceError(422, "invalid_star_range", "Minimum stars exceeds maximum")
    context = await _existing_context(request)
    return await _catalog_call(
        _service(request).products(
            context.session_id,
            page=page,
            page_size=_bounded_page_size(request, page_size),
            search=search,
            category=category,
            brand=brand,
            min_price_minor=min_price_minor,
            max_price_minor=max_price_minor,
            available=available,
            min_stars=min_stars,
            max_stars=max_stars,
            stars=stars,
            sort=sort,
        )
    )


@router.get("/catalog/products/trending", response_model=ProductPage)
async def list_trending_products(
    request: Request,
    page: Page = 1,
    page_size: PageSize = 20,
    window_days: Annotated[int, Query(ge=1, le=365)] = 7,
) -> ProductPage:
    """Rank in-catalog products by recorded units sold in the recent sales window."""

    context = await _existing_context(request)
    return await _catalog_call(
        _service(request).trending_products(
            context.session_id,
            page=page,
            page_size=_bounded_page_size(request, page_size),
            window_days=window_days,
        )
    )


@router.get("/catalog/products/{identifier}/similar", response_model=ProductPage)
async def list_similar_products(
    request: Request,
    identifier: str,
    page: Page = 1,
    page_size: PageSize = 20,
) -> ProductPage:
    """Same-category products ranked by buying-intent score."""

    context = await _existing_context(request)
    return await _catalog_call(
        _service(request).similar_products(
            context.session_id,
            identifier,
            page=page,
            page_size=_bounded_page_size(request, page_size),
        )
    )


@router.get("/catalog/products/{identifier}/cross-sell", response_model=ProductPage)
async def list_cross_sell_products(
    request: Request,
    identifier: str,
    page: Page = 1,
    page_size: PageSize = 20,
) -> ProductPage:
    """Frequently bought together; falls back to similar products."""

    context = await _existing_context(request)
    return await _catalog_call(
        _service(request).cross_sell_products(
            context.session_id,
            identifier,
            page=page,
            page_size=_bounded_page_size(request, page_size),
        )
    )


@router.get("/recommendations/personal", response_model=ProductPage)
async def list_personal_recommendations(
    request: Request,
    page: Page = 1,
    page_size: PageSize = 20,
) -> ProductPage:
    """Session-based recommendations; cold start uses trending products."""

    context = await _existing_context(request)
    return await _catalog_call(
        _service(request).personal_recommendations(
            context.session_id,
            page=page,
            page_size=_bounded_page_size(request, page_size),
        )
    )


@router.get("/catalog/products/{identifier}", response_model=ProductView)
async def get_product(request: Request, identifier: str) -> ProductView:
    context = await _existing_context(request)
    return await _catalog_call(_service(request).product(context.session_id, identifier))


@router.get("/catalog/products/{identifier}/reviews", response_model=ReviewList)
async def list_product_reviews(
    request: Request,
    identifier: str,
    page: Page = 1,
    page_size: PageSize = 20,
    stars: Annotated[int | None, Query(ge=1, le=5)] = None,
) -> ReviewList:
    context = await _existing_context(request)
    return await _catalog_call(
        _service(request).list_product_reviews(
            context.session_id,
            identifier,
            page=page,
            page_size=_bounded_page_size(request, page_size),
            stars=stars,
        )
    )


@router.post(
    "/catalog/products/{identifier}/reviews",
    response_model=ReviewResponse,
    status_code=201,
)
async def create_product_review(
    identifier: str,
    body: ReviewCreateRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ReviewResponse:
    context = await _write_context(request, x_csrf_token)
    review = await _catalog_call(
        _service(request).create_product_review(context.session_id, identifier, body)
    )
    return ReviewResponse(review=review)


@router.patch("/reviews/{review_id}", response_model=ReviewResponse)
async def update_my_review(
    review_id: UUID,
    body: ReviewUpdate,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ReviewResponse:
    context = await _write_context(request, x_csrf_token)
    review = await _service(request).update_product_review(
        context.session_id, review_id, body
    )
    return ReviewResponse(review=review)


@router.delete("/reviews/{review_id}", status_code=204)
async def delete_my_review(
    review_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> None:
    context = await _write_context(request, x_csrf_token)
    await _service(request).delete_product_review(context.session_id, review_id)


@router.post("/traffic/events", response_model=ViewResponse, status_code=201)
async def record_traffic_event(
    body: ViewRecordRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ViewResponse:
    """Beacon endpoint for storefront visits, searches, and explicit views."""

    context = await _write_context(request, x_csrf_token)
    user_agent = request.headers.get("user-agent")
    event = await _catalog_call(
        _service(request).record_view(
            context.session_id, body, user_agent=user_agent
        )
    )
    return ViewResponse(view=event)


@router.get("/cart", response_model=CartView)
async def get_cart(request: Request) -> CartView:
    context = await _existing_context(request)
    return await _service(request).cart(context.session_id)


@router.post("/cart/items", response_model=CartView)
async def add_cart_item(
    body: CartQuantityRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> CartView:
    context = await _write_context(request, x_csrf_token)
    return await _service(request).change_cart(
        context.session_id, body.variant_id, body.quantity, add=True
    )


@router.put("/cart/items/{variant_id}", response_model=CartView)
async def set_cart_item(
    variant_id: UUID,
    body: CartQuantityRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> CartView:
    if body.variant_id != variant_id:
        raise CommerceError(422, "variant_mismatch", "Path and body variant_id must match")
    context = await _write_context(request, x_csrf_token)
    return await _service(request).change_cart(
        context.session_id, variant_id, body.quantity, add=False
    )


@router.delete("/cart/items/{variant_id}", response_model=CartView)
async def remove_cart_item(
    variant_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> CartView:
    context = await _write_context(request, x_csrf_token)
    return await _service(request).remove_cart(context.session_id, variant_id)


@router.delete("/cart", response_model=CartView)
async def clear_cart(request: Request, x_csrf_token: str | None = Header(default=None)) -> CartView:
    context = await _write_context(request, x_csrf_token)
    return await _service(request).clear_cart(context.session_id)


@router.get("/wishlist", response_model=WishlistView)
async def get_wishlist(request: Request) -> WishlistView:
    context = await _existing_context(request)
    return WishlistView(items=await _service(request).wishlist(context.session_id))


@router.post("/wishlist/items", response_model=WishlistView)
async def add_wishlist_item(
    body: WishlistRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> WishlistView:
    context = await _write_context(request, x_csrf_token)
    items = await _service(request).change_wishlist(
        context.session_id, body.product_id, remove=False
    )
    return WishlistView(items=items)


@router.delete("/wishlist/items/{product_id}", response_model=WishlistView)
async def remove_wishlist_item(
    product_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> WishlistView:
    context = await _write_context(request, x_csrf_token)
    items = await _service(request).change_wishlist(context.session_id, product_id, remove=True)
    return WishlistView(items=items)


@router.delete("/wishlist", response_model=WishlistView)
async def clear_wishlist(
    request: Request, x_csrf_token: str | None = Header(default=None)
) -> WishlistView:
    context = await _write_context(request, x_csrf_token)
    return WishlistView(items=await _service(request).clear_wishlist(context.session_id))


@router.get("/addresses", response_model=AddressList)
async def list_addresses(request: Request) -> AddressList:
    context = await _existing_context(request)
    return AddressList(items=await _service(request).addresses(context.session_id))


@router.post("/addresses", response_model=AddressList)
async def create_address(
    body: AddressInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> AddressList:
    context = await _write_context(request, x_csrf_token)
    address = AddressRecord(id=uuid4(), **body.model_dump())
    return AddressList(items=await _service(request).put_address(context.session_id, address))


@router.put("/addresses/{address_id}", response_model=AddressList)
async def update_address(
    address_id: UUID,
    body: AddressInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> AddressList:
    context = await _write_context(request, x_csrf_token)
    address = AddressRecord(id=address_id, **body.model_dump())
    return AddressList(items=await _service(request).put_address(context.session_id, address))


@router.delete("/addresses/{address_id}", response_model=AddressList)
async def delete_address(
    address_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> AddressList:
    context = await _write_context(request, x_csrf_token)
    return AddressList(items=await _service(request).delete_address(context.session_id, address_id))


@router.get("/wallet", response_model=WalletView)
async def get_wallet(request: Request) -> WalletView:
    context = await _existing_context(request)
    return WalletView(
        balance_minor=context.state.wallet.balance_minor,
        currency=context.state.wallet.currency,
    )


@router.get("/wallet/ledger", response_model=LedgerPage)
async def get_wallet_ledger(
    request: Request, page: Page = 1, page_size: PageSize = 20
) -> LedgerPage:
    context = await _existing_context(request)
    return await _service(request).ledger(
        context.session_id, page, _bounded_page_size(request, page_size)
    )


@router.post("/commerce/wallet/adjustments/{operation}", response_model=WalletView)
async def adjust_wallet(
    operation: Literal["credit", "debit"],
    body: WalletAdjustmentRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> WalletView:
    context = await _write_context(request, x_csrf_token)
    state = await _service(request).adjust_wallet(
        context.session_id, body.amount_minor, body.reference, operation=operation
    )
    return WalletView(balance_minor=state.wallet.balance_minor, currency=state.wallet.currency)


@router.get("/checkout/delivery-options", response_model=DeliveryOptionList)
async def list_delivery_options(
    request: Request,
    coupon_code: Annotated[str | None, Query(min_length=1, max_length=40)] = None,
) -> DeliveryOptionList:
    """List delivery options for the current cart before final payment."""

    context = await _existing_context(request)
    return await _catalog_call(
        _service(request).delivery_options(context.session_id, coupon_code=coupon_code)
    )


@router.post("/checkout", response_model=OrderRecord)
async def checkout(
    body: CheckoutRequest,
    request: Request,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=120)],
    x_csrf_token: str | None = Header(default=None),
) -> OrderRecord:
    context = await _write_context(request, x_csrf_token)
    return await _service(request).checkout(
        context.session_id,
        body.address_id,
        body.coupon_code,
        idempotency_key,
        delivery_option_id=body.delivery_option_id,
    )


@router.get("/orders", response_model=OrderPage)
async def list_orders(request: Request, page: Page = 1, page_size: PageSize = 20) -> OrderPage:
    context = await _existing_context(request)
    items, total, pages = await _service(request).orders(
        context.session_id, page, _bounded_page_size(request, page_size)
    )
    return OrderPage(items=items, page=page, page_size=page_size, total=total, pages=pages)


@router.get("/orders/{order_id}", response_model=OrderRecord)
async def get_order(request: Request, order_id: UUID) -> OrderRecord:
    context = await _existing_context(request)
    return await _service(request).order(context.session_id, order_id)


@router.post("/orders/{order_id}/transition", response_model=OrderRecord)
async def transition_order(
    order_id: UUID,
    body: OrderTransitionRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> OrderRecord:
    context = await _write_context(request, x_csrf_token)
    return await _service(request).transition_order(context.session_id, order_id, body.action)
