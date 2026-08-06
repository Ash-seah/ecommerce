"""HTTP surface for sandbox sales analytics administration."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from src.sales.schemas import (
    BestSellers,
    CategorySales,
    CouponSales,
    GeoSales,
    SaleCreate,
    SaleList,
    SaleResponse,
    SalesFeed,
    SalesSeries,
    SalesSummary,
    SaleUpdate,
    SaleVoidRequest,
)
from src.sales.service import SandboxSalesService
from src.sandbox.router import SessionContext, _existing_context, _require_csrf

router = APIRouter(prefix="/v1/admin/sales", tags=["admin-sales"])


def _service(request: Request) -> SandboxSalesService:
    service: SandboxSalesService = request.app.state.sandbox_sales_service
    return service


async def _write(request: Request, token: str | None) -> SessionContext:
    return await _require_csrf(request, token)


async def _read(request: Request) -> SessionContext:
    return await _existing_context(request)


def _filters(
    status: Literal["recorded", "voided", "all"] = "recorded",
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    variant_id: UUID | None = None,
    coupon_code: str | None = None,
    country_code: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "product_id": product_id,
        "category_id": category_id,
        "variant_id": variant_id,
        "coupon_code": coupon_code,
        "country_code": country_code,
        "occurred_from": occurred_from,
        "occurred_to": occurred_to,
    }


@router.get("", response_model=SaleList)
async def list_sales(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["recorded", "voided", "all"] = "recorded",
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    variant_id: UUID | None = None,
    coupon_code: str | None = None,
    country_code: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> SaleList:
    context = await _read(request)
    return await _service(request).list_sales(
        context.session_id,
        page=page,
        page_size=page_size,
        **_filters(
            status,
            product_id,
            category_id,
            variant_id,
            coupon_code,
            country_code,
            occurred_from,
            occurred_to,
        ),  # type: ignore[arg-type]
    )


@router.get("/feed", response_model=SalesFeed)
async def sales_feed(
    request: Request,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SalesFeed:
    context = await _read(request)
    return await _service(request).feed(context.session_id, since=since, limit=limit)


@router.get("/analytics/summary", response_model=SalesSummary)
async def sales_summary(
    request: Request,
    status: Literal["recorded", "voided", "all"] = "recorded",
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    variant_id: UUID | None = None,
    coupon_code: str | None = None,
    country_code: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> SalesSummary:
    context = await _read(request)
    return await _service(request).summary(
        context.session_id,
        **_filters(
            status,
            product_id,
            category_id,
            variant_id,
            coupon_code,
            country_code,
            occurred_from,
            occurred_to,
        ),
    )


@router.get("/analytics/bestsellers", response_model=BestSellers)
async def sales_bestsellers(
    request: Request,
    metric: Literal["revenue", "units"] = "revenue",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    status: Literal["recorded", "voided", "all"] = "recorded",
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> BestSellers:
    context = await _read(request)
    return await _service(request).bestsellers(
        context.session_id,
        metric=metric,
        limit=limit,
        **_filters(status, product_id, category_id, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/timeseries", response_model=SalesSeries)
async def sales_timeseries(
    request: Request,
    bucket: Literal["hour", "day"] = "day",
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> SalesSeries:
    context = await _read(request)
    return await _service(request).timeseries(
        context.session_id,
        bucket=bucket,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/by-category", response_model=CategorySales)
async def sales_by_category(
    request: Request,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> CategorySales:
    context = await _read(request)
    return await _service(request).by_category(
        context.session_id,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/by-coupon", response_model=CouponSales)
async def sales_by_coupon(
    request: Request,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> CouponSales:
    context = await _read(request)
    return await _service(request).by_coupon(
        context.session_id,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/by-geo", response_model=GeoSales)
async def sales_by_geo(
    request: Request,
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> GeoSales:
    context = await _read(request)
    return await _service(request).by_geo(
        context.session_id,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/{sale_id}", response_model=SaleResponse)
async def get_sale(sale_id: UUID, request: Request) -> SaleResponse:
    context = await _read(request)
    return SaleResponse(sale=await _service(request).get(context.session_id, sale_id))


@router.post("", response_model=SaleResponse, status_code=201)
async def create_sale(
    body: SaleCreate,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> SaleResponse:
    context = await _write(request, x_csrf_token)
    return SaleResponse(sale=await _service(request).create(context.session_id, body))


@router.patch("/{sale_id}", response_model=SaleResponse)
async def update_sale(
    sale_id: UUID,
    body: SaleUpdate,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> SaleResponse:
    context = await _write(request, x_csrf_token)
    return SaleResponse(sale=await _service(request).update(context.session_id, sale_id, body))


@router.post("/{sale_id}/void", response_model=SaleResponse)
async def void_sale(
    sale_id: UUID,
    request: Request,
    body: SaleVoidRequest | None = None,
    x_csrf_token: str | None = Header(default=None),
) -> SaleResponse:
    context = await _write(request, x_csrf_token)
    reason = None if body is None else body.reason
    return SaleResponse(
        sale=await _service(request).void(context.session_id, sale_id, reason=reason)
    )


@router.delete("/{sale_id}", status_code=204)
async def delete_sale(
    sale_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> None:
    context = await _write(request, x_csrf_token)
    await _service(request).delete(context.session_id, sale_id)
