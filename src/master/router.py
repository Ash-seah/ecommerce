"""JWT-protected HTTP surface for editing the master catalog."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.config import Settings
from src.master.auth import (
    MasterAuthError,
    authenticate_password,
    decode_access_token,
    issue_access_token,
)
from src.master.schemas import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    LoginRequest,
    MediaResponse,
    ProductCreate,
    ProductResponse,
    ProductUpdate,
    PublishResponse,
    TokenResponse,
    VariantCreate,
    VariantResponse,
    VariantUpdate,
)
from src.master.service import MasterCatalogService

router = APIRouter(prefix="/v1/master", tags=["master"])
_bearer = HTTPBearer(auto_error=False)


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _service(request: Request) -> MasterCatalogService:
    service: MasterCatalogService = request.app.state.master_service
    return service


async def _require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise MasterAuthError(401, "auth_required", "Bearer access token is required")
    payload = decode_access_token(_settings(request), credentials.credentials)
    return str(payload["sub"])


AdminUser = Annotated[str, Depends(_require_admin)]


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    settings = _settings(request)
    subject = authenticate_password(settings, body.username, body.password)
    token = issue_access_token(settings, subject)
    return TokenResponse(access_token=token, expires_in=settings.jwt_ttl_seconds)


@router.post("/categories", response_model=CategoryResponse)
async def create_category(
    body: CategoryCreate, request: Request, _admin: AdminUser
) -> CategoryResponse:
    category = await _service(request).create_category(body)
    return CategoryResponse(category=category)


@router.patch("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: UUID, body: CategoryUpdate, request: Request, _admin: AdminUser
) -> CategoryResponse:
    category = await _service(request).update_category(category_id, body)
    return CategoryResponse(category=category)


@router.post("/products", response_model=ProductResponse)
async def create_product(
    body: ProductCreate, request: Request, _admin: AdminUser
) -> ProductResponse:
    product = await _service(request).create_product(body)
    return ProductResponse(product=product)


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: UUID, body: ProductUpdate, request: Request, _admin: AdminUser
) -> ProductResponse:
    product = await _service(request).update_product(product_id, body)
    return ProductResponse(product=product)


@router.post("/variants", response_model=VariantResponse)
async def create_variant(
    body: VariantCreate, request: Request, _admin: AdminUser
) -> VariantResponse:
    variant = await _service(request).create_variant(body)
    return VariantResponse(variant=variant)


@router.patch("/variants/{variant_id}", response_model=VariantResponse)
async def update_variant(
    variant_id: UUID, body: VariantUpdate, request: Request, _admin: AdminUser
) -> VariantResponse:
    variant = await _service(request).update_variant(variant_id, body)
    return VariantResponse(variant=variant)


@router.post("/products/{product_id}/media", response_model=MediaResponse)
async def upload_product_media(
    product_id: UUID,
    request: Request,
    _admin: AdminUser,
    file: Annotated[UploadFile, File()],
    alt_text: Annotated[str, Form(min_length=1, max_length=300)] = "Product image",
    sort_order: Annotated[int, Form(ge=0)] = 0,
    is_main: Annotated[bool, Form()] = False,
) -> MediaResponse:
    payload = await file.read()
    media = await _service(request).attach_media(
        product_id,
        payload,
        file.content_type,
        alt_text,
        sort_order,
        is_main=is_main,
    )
    return MediaResponse(media=media)


@router.post("/variants/{variant_id}/media", response_model=MediaResponse)
async def upload_variant_media(
    variant_id: UUID,
    request: Request,
    _admin: AdminUser,
    file: Annotated[UploadFile, File()],
    alt_text: Annotated[str, Form(min_length=1, max_length=300)] = "Variant image",
    sort_order: Annotated[int, Form(ge=0)] = 0,
    is_main: Annotated[bool, Form()] = False,
) -> MediaResponse:
    payload = await file.read()
    media = await _service(request).attach_variant_media(
        variant_id,
        payload,
        file.content_type,
        alt_text,
        sort_order,
        is_main=is_main,
    )
    return MediaResponse(media=media)


@router.post("/media/{media_id}/main", response_model=MediaResponse)
async def set_media_main(
    media_id: UUID, request: Request, _admin: AdminUser
) -> MediaResponse:
    media = await _service(request).set_media_main(media_id)
    return MediaResponse(media=media)


@router.delete("/media/{media_id}", status_code=204)
async def delete_media(media_id: UUID, request: Request, _admin: AdminUser) -> None:
    await _service(request).delete_media(media_id)


@router.post("/catalog/publish", response_model=PublishResponse)
async def publish_catalog(request: Request, _admin: AdminUser) -> PublishResponse:
    """Reload Redis from Postgres so new sandboxes (and cache reads) see master changes."""
    return await _service(request).publish()
