"""Thin FastAPI v1 routes for session-local administration."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, Request, UploadFile

from src.admin.schemas import (
    ActiveAdjustment,
    AdminCatalogResponse,
    CategoryInput,
    CategoryResponse,
    CouponInput,
    CouponList,
    CouponResponse,
    InventoryAdjustment,
    MediaUploadResponse,
    PriceAdjustment,
    ProductInput,
    ProductResponse,
    VariantInput,
    VariantResponse,
)
from src.admin.service import AdminService
from src.infrastructure.minio import MediaService
from src.sandbox.router import SessionContext, _require_csrf

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def _service(request: Request) -> AdminService:
    service: AdminService = request.app.state.admin_service
    return service


def _media(request: Request) -> MediaService:
    service: MediaService = request.app.state.media_service
    return service


async def _context(request: Request, token: str | None) -> SessionContext:
    return await _require_csrf(request, token)


async def _catalog_response(service: AdminService, session_id: str) -> AdminCatalogResponse:
    state, catalog = await service.catalog(session_id)
    return AdminCatalogResponse(catalog=catalog, version=state.version)


@router.get("/catalog", response_model=AdminCatalogResponse)
async def get_admin_catalog(request: Request) -> AdminCatalogResponse:
    from src.sandbox.router import _existing_context

    context = await _existing_context(request)
    return await _catalog_response(_service(request), context.session_id)


@router.post("/categories", response_model=CategoryResponse, status_code=201)
async def create_category(
    body: CategoryInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> CategoryResponse:
    context = await _context(request, x_csrf_token)
    state, category = await _service(request).create_category(context.session_id, body)
    return CategoryResponse(category=category, version=state.version)


@router.put("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: UUID,
    body: CategoryInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> CategoryResponse:
    context = await _context(request, x_csrf_token)
    state, category = await _service(request).update_category(context.session_id, category_id, body)
    return CategoryResponse(category=category, version=state.version)


@router.delete("/categories/{category_id}", response_model=AdminCatalogResponse)
async def delete_category(
    category_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> AdminCatalogResponse:
    context = await _context(request, x_csrf_token)
    await _service(request).delete_category(context.session_id, category_id)
    return await _catalog_response(_service(request), context.session_id)


@router.post("/categories/{category_id}/restore", response_model=CategoryResponse)
async def restore_category(
    category_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> CategoryResponse:
    context = await _context(request, x_csrf_token)
    state, category = await _service(request).restore_category(context.session_id, category_id)
    return CategoryResponse(category=category, version=state.version)


@router.post("/products", response_model=ProductResponse, status_code=201)
async def create_product(
    body: ProductInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ProductResponse:
    context = await _context(request, x_csrf_token)
    state, product = await _service(request).create_product(context.session_id, body)
    return ProductResponse(product=product, version=state.version)


@router.put("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: UUID,
    body: ProductInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ProductResponse:
    context = await _context(request, x_csrf_token)
    state, product = await _service(request).update_product(context.session_id, product_id, body)
    return ProductResponse(product=product, version=state.version)


@router.delete("/products/{product_id}", response_model=AdminCatalogResponse)
async def delete_product(
    product_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> AdminCatalogResponse:
    context = await _context(request, x_csrf_token)
    await _service(request).delete_product(context.session_id, product_id)
    return await _catalog_response(_service(request), context.session_id)


@router.post("/products/{product_id}/restore", response_model=ProductResponse)
async def restore_product(
    product_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ProductResponse:
    context = await _context(request, x_csrf_token)
    state, product = await _service(request).restore_product(context.session_id, product_id)
    return ProductResponse(product=product, version=state.version)


@router.post("/products/{product_id}/variants", response_model=VariantResponse, status_code=201)
async def create_variant(
    product_id: UUID,
    body: VariantInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> VariantResponse:
    context = await _context(request, x_csrf_token)
    state, variant = await _service(request).create_variant(context.session_id, product_id, body)
    return VariantResponse(variant=variant, version=state.version)


@router.put("/variants/{variant_id}", response_model=VariantResponse)
async def update_variant(
    variant_id: UUID,
    body: VariantInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> VariantResponse:
    context = await _context(request, x_csrf_token)
    state, variant = await _service(request).update_variant(context.session_id, variant_id, body)
    return VariantResponse(variant=variant, version=state.version)


@router.delete("/variants/{variant_id}", response_model=AdminCatalogResponse)
async def delete_variant(
    variant_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> AdminCatalogResponse:
    context = await _context(request, x_csrf_token)
    await _service(request).delete_variant(context.session_id, variant_id)
    return await _catalog_response(_service(request), context.session_id)


@router.post("/variants/{variant_id}/restore", response_model=VariantResponse)
async def restore_variant(
    variant_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> VariantResponse:
    context = await _context(request, x_csrf_token)
    state, variant = await _service(request).restore_variant(context.session_id, variant_id)
    return VariantResponse(variant=variant, version=state.version)


@router.post("/variants/{variant_id}/price", response_model=VariantResponse)
async def adjust_price(
    variant_id: UUID,
    body: PriceAdjustment,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> VariantResponse:
    context = await _context(request, x_csrf_token)
    state, variant = await _service(request).adjust_price(
        context.session_id, variant_id, body.price_minor, body.currency
    )
    return VariantResponse(variant=variant, version=state.version)


@router.post("/variants/{variant_id}/inventory", response_model=AdminCatalogResponse)
async def adjust_inventory(
    variant_id: UUID,
    body: InventoryAdjustment,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> AdminCatalogResponse:
    context = await _context(request, x_csrf_token)
    await _service(request).adjust_inventory(context.session_id, variant_id, body)
    return await _catalog_response(_service(request), context.session_id)


@router.post("/{entity}/{entity_id}/active", response_model=AdminCatalogResponse)
async def adjust_active(
    entity: Literal["categories", "products", "variants"],
    entity_id: UUID,
    body: ActiveAdjustment,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> AdminCatalogResponse:
    context = await _context(request, x_csrf_token)
    await _service(request).set_active(context.session_id, entity, entity_id, active=body.active)
    return await _catalog_response(_service(request), context.session_id)


@router.get("/coupons", response_model=CouponList)
async def list_coupons(request: Request) -> CouponList:
    from src.sandbox.router import _existing_context

    context = await _existing_context(request)
    return CouponList(items=await _service(request).coupons(context.session_id))


@router.post("/coupons", response_model=CouponResponse, status_code=201)
async def create_coupon(
    body: CouponInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> CouponResponse:
    context = await _context(request, x_csrf_token)
    state, coupon = await _service(request).put_coupon(context.session_id, body, create=True)
    return CouponResponse(coupon=coupon, version=state.version)


@router.put("/coupons/{code}", response_model=CouponResponse)
async def update_coupon(
    code: str,
    body: CouponInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> CouponResponse:
    if code.upper() != body.code.upper():
        from src.admin.service import AdminError

        raise AdminError(422, "coupon_code_mismatch", "Path and body coupon codes differ")
    context = await _context(request, x_csrf_token)
    state, coupon = await _service(request).put_coupon(context.session_id, body, create=False)
    return CouponResponse(coupon=coupon, version=state.version)


@router.delete("/coupons/{code}", response_model=CouponList)
async def delete_coupon(
    code: str,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> CouponList:
    context = await _context(request, x_csrf_token)
    await _service(request).delete_coupon(context.session_id, code)
    return CouponList(items=await _service(request).coupons(context.session_id))


@router.post("/products/{product_id}/media", response_model=MediaUploadResponse)
async def upload_product_media(
    product_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    alt_text: Annotated[str, Form(min_length=1, max_length=300)],
    sort_order: Annotated[int, Form(ge=0)] = 0,
    is_main: Annotated[bool, Form()] = False,
    x_csrf_token: str | None = Header(default=None),
) -> MediaUploadResponse:
    context = await _context(request, x_csrf_token)
    media_service = _media(request)
    data = await file.read(media_service.max_upload_bytes + 1)
    media = await media_service.upload(
        context.safe_id, data, file.content_type, alt_text, sort_order, is_main=is_main
    )
    try:
        await _service(request).add_media(context.session_id, product_id, media)
    except Exception:
        await media_service.delete(context.safe_id, media.object_key)
        raise
    return MediaUploadResponse(media=media)


@router.post("/variants/{variant_id}/media", response_model=MediaUploadResponse)
async def upload_variant_media(
    variant_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    alt_text: Annotated[str, Form(min_length=1, max_length=300)],
    sort_order: Annotated[int, Form(ge=0)] = 0,
    is_main: Annotated[bool, Form()] = False,
    x_csrf_token: str | None = Header(default=None),
) -> MediaUploadResponse:
    context = await _context(request, x_csrf_token)
    media_service = _media(request)
    data = await file.read(media_service.max_upload_bytes + 1)
    media = await media_service.upload(
        context.safe_id, data, file.content_type, alt_text, sort_order, is_main=is_main
    )
    try:
        await _service(request).add_variant_media(context.session_id, variant_id, media)
    except Exception:
        await media_service.delete(context.safe_id, media.object_key)
        raise
    return MediaUploadResponse(media=media)


@router.post("/media/{media_id}/main", response_model=MediaUploadResponse)
async def set_media_main(
    media_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> MediaUploadResponse:
    context = await _context(request, x_csrf_token)
    _state, media = await _service(request).set_media_main(context.session_id, media_id)
    return MediaUploadResponse(media=media)


@router.delete("/media/{media_id}", response_model=AdminCatalogResponse)
async def delete_media(
    media_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> AdminCatalogResponse:
    context = await _context(request, x_csrf_token)
    _state, media = await _service(request).remove_media(context.session_id, media_id)
    await _media(request).delete(context.safe_id, media.object_key)
    return await _catalog_response(_service(request), context.session_id)


@router.post("/restore", response_model=AdminCatalogResponse)
async def restore_all(
    request: Request, x_csrf_token: str | None = Header(default=None)
) -> AdminCatalogResponse:
    context = await _context(request, x_csrf_token)
    await _service(request).restore_all(context.session_id)
    await _media(request).cleanup(context.safe_id)
    return await _catalog_response(_service(request), context.session_id)
