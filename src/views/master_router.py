"""JWT-protected master traffic / views administration."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request

from src.master.router import AdminUser
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
from src.views.service import MasterViewsService

router = APIRouter(prefix="/v1/master/views", tags=["master-views"])


def _service(request: Request) -> MasterViewsService:
    service: MasterViewsService = request.app.state.master_views_service
    return service


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
    _admin: AdminUser,
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
    return await _service(request).list_views(
        page=page,
        page_size=page_size,
        **_filters(
            status, kind, product_id, category_id, path, country_code, occurred_from, occurred_to
        ),  # type: ignore[arg-type]
    )


@router.get("/feed", response_model=ViewsFeed)
async def views_feed(
    request: Request,
    _admin: AdminUser,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ViewsFeed:
    return await _service(request).feed(since=since, limit=limit)


@router.get("/analytics/summary", response_model=ViewsSummary)
async def views_summary(
    request: Request,
    _admin: AdminUser,
    status: Literal["recorded", "voided", "all"] = "recorded",
    kind: ViewKind | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> ViewsSummary:
    return await _service(request).summary(
        **_filters(status, kind, None, None, None, None, occurred_from, occurred_to)
    )


@router.get("/analytics/timeseries", response_model=ViewsSeries)
async def views_timeseries(
    request: Request,
    _admin: AdminUser,
    bucket: Literal["hour", "day"] = "day",
    status: Literal["recorded", "voided", "all"] = "recorded",
    kind: ViewKind | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> ViewsSeries:
    return await _service(request).timeseries(
        bucket=bucket,
        **_filters(status, kind, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/top-products", response_model=TopViewedProducts)
async def views_top_products(
    request: Request,
    _admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> TopViewedProducts:
    return await _service(request).top_products(
        limit=limit,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/top-categories", response_model=TopViewedCategories)
async def views_top_categories(
    request: Request,
    _admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> TopViewedCategories:
    return await _service(request).top_categories(
        limit=limit,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/top-paths", response_model=TopPaths)
async def views_top_paths(
    request: Request,
    _admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> TopPaths:
    return await _service(request).top_paths(
        limit=limit,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/by-kind", response_model=ViewsByKind)
async def views_by_kind(
    request: Request,
    _admin: AdminUser,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> ViewsByKind:
    return await _service(request).by_kind(
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to)
    )


@router.get("/{view_id}", response_model=ViewResponse)
async def get_view(view_id: UUID, request: Request, _admin: AdminUser) -> ViewResponse:
    return ViewResponse(view=await _service(request).get(view_id))


@router.post("", response_model=ViewResponse, status_code=201)
async def create_view(
    body: ViewCreate, request: Request, _admin: AdminUser
) -> ViewResponse:
    return ViewResponse(view=await _service(request).create(body))


@router.patch("/{view_id}", response_model=ViewResponse)
async def update_view(
    view_id: UUID, body: ViewUpdate, request: Request, _admin: AdminUser
) -> ViewResponse:
    return ViewResponse(view=await _service(request).update(view_id, body))


@router.post("/{view_id}/void", response_model=ViewResponse)
async def void_view(
    view_id: UUID,
    request: Request,
    _admin: AdminUser,
    body: ViewVoidRequest | None = None,
) -> ViewResponse:
    reason = None if body is None else body.reason
    return ViewResponse(view=await _service(request).void(view_id, reason=reason))


@router.delete("/{view_id}", status_code=204)
async def delete_view(view_id: UUID, request: Request, _admin: AdminUser) -> None:
    await _service(request).delete(view_id)
