"""HTTP surface for sandbox traffic / views administration."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from src.sandbox.router import SessionContext, _existing_context, _require_csrf
from src.views.schemas import (
    TopPaths,
    TopViewedCategories,
    TopViewedProducts,
    ViewCreate,
    ViewKind,
    ViewList,
    ViewResponse,
    ViewsByKind,
    ViewsFeed,
    ViewsSeries,
    ViewsSummary,
    ViewUpdate,
    ViewVoidRequest,
)
from src.views.service import SandboxViewsService

router = APIRouter(prefix="/v1/admin/views", tags=["admin-views"])


def _service(request: Request) -> SandboxViewsService:
    service: SandboxViewsService = request.app.state.sandbox_views_service
    return service


async def _write(request: Request, token: str | None) -> SessionContext:
    return await _require_csrf(request, token)


async def _read(request: Request) -> SessionContext:
    return await _existing_context(request)


def _filters(
    status: Literal["recorded", "voided", "all"] = "recorded",
    kind: ViewKind | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    path: str | None = None,
    country_code: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "kind": kind,
        "product_id": product_id,
        "category_id": category_id,
        "path": path,
        "country_code": country_code,
        "occurred_from": occurred_from,
        "occurred_to": occurred_to,
    }


@router.get("", response_model=ViewList)
async def list_views(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["recorded", "voided", "all"] = "recorded",
    kind: ViewKind | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    path: str | None = None,
    country_code: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> ViewList:
    context = await _read(request)
    return await _service(request).list_views(
        context.session_id,
        page=page,
        page_size=page_size,
        **_filters(
            status, kind, product_id, category_id, path, country_code, occurred_from, occurred_to
        ),  # type: ignore[arg-type]
    )


@router.get("/feed", response_model=ViewsFeed)
async def views_feed(
    request: Request,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ViewsFeed:
    context = await _read(request)
    return await _service(request).feed(context.session_id, since=since, limit=limit)


@router.get("/analytics/summary", response_model=ViewsSummary)
async def views_summary(
    request: Request,
    status: Literal["recorded", "voided", "all"] = "recorded",
    kind: ViewKind | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> ViewsSummary:
    context = await _read(request)
    return await _service(request).summary(
        context.session_id,
        **_filters(status, kind, product_id, category_id, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/timeseries", response_model=ViewsSeries)
async def views_timeseries(
    request: Request,
    bucket: Literal["hour", "day"] = "day",
    status: Literal["recorded", "voided", "all"] = "recorded",
    kind: ViewKind | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> ViewsSeries:
    context = await _read(request)
    return await _service(request).timeseries(
        context.session_id,
        bucket=bucket,
        **_filters(status, kind, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/top-products", response_model=TopViewedProducts)
async def views_top_products(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> TopViewedProducts:
    context = await _read(request)
    return await _service(request).top_products(
        context.session_id,
        limit=limit,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/top-categories", response_model=TopViewedCategories)
async def views_top_categories(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> TopViewedCategories:
    context = await _read(request)
    return await _service(request).top_categories(
        context.session_id,
        limit=limit,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/top-paths", response_model=TopPaths)
async def views_top_paths(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> TopPaths:
    context = await _read(request)
    return await _service(request).top_paths(
        context.session_id,
        limit=limit,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/by-kind", response_model=ViewsByKind)
async def views_by_kind(
    request: Request,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> ViewsByKind:
    context = await _read(request)
    return await _service(request).by_kind(
        context.session_id,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/{view_id}", response_model=ViewResponse)
async def get_view(view_id: UUID, request: Request) -> ViewResponse:
    context = await _read(request)
    return ViewResponse(view=await _service(request).get(context.session_id, view_id))


@router.post("", response_model=ViewResponse, status_code=201)
async def create_view(
    body: ViewCreate,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ViewResponse:
    context = await _write(request, x_csrf_token)
    return ViewResponse(view=await _service(request).create(context.session_id, body))


@router.patch("/{view_id}", response_model=ViewResponse)
async def update_view(
    view_id: UUID,
    body: ViewUpdate,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ViewResponse:
    context = await _write(request, x_csrf_token)
    return ViewResponse(view=await _service(request).update(context.session_id, view_id, body))


@router.post("/{view_id}/void", response_model=ViewResponse)
async def void_view(
    view_id: UUID,
    request: Request,
    body: ViewVoidRequest | None = None,
    x_csrf_token: str | None = Header(default=None),
) -> ViewResponse:
    context = await _write(request, x_csrf_token)
    reason = None if body is None else body.reason
    return ViewResponse(
        view=await _service(request).void(context.session_id, view_id, reason=reason)
    )


@router.delete("/{view_id}", status_code=204)
async def delete_view(
    view_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> None:
    context = await _write(request, x_csrf_token)
    await _service(request).delete(context.session_id, view_id)
