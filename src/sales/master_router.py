"""JWT-protected master sales analytics administration."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request

from src.master.router import AdminUser
from src.sales.schemas import (
    BestSellers,
    SaleCreate,
    SaleList,
    SaleResponse,
    SalesBreakdown,
    SalesFeed,
    SalesGroupBy,
    SalesSeries,
    SalesSummary,
    SaleUpdate,
    SaleVoidRequest,
)
from src.sales.service import MasterSalesService

router = APIRouter(prefix="/v1/master/sales", tags=["master-sales"])


def _service(request: Request) -> MasterSalesService:
    service: MasterSalesService = request.app.state.master_sales_service
    return service


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
    _admin: AdminUser,
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
    return await _service(request).list_sales(
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
    _admin: AdminUser,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SalesFeed:
    return await _service(request).feed(since=since, limit=limit)


@router.get("/analytics/summary", response_model=SalesSummary)
async def sales_summary(
    request: Request,
    _admin: AdminUser,
    status: Literal["recorded", "voided", "all"] = "recorded",
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    variant_id: UUID | None = None,
    coupon_code: str | None = None,
    country_code: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> SalesSummary:
    return await _service(request).summary(
        **_filters(
            status,
            product_id,
            category_id,
            variant_id,
            coupon_code,
            country_code,
            occurred_from,
            occurred_to,
        )
    )


@router.get("/analytics/bestsellers", response_model=BestSellers)
async def sales_bestsellers(
    request: Request,
    _admin: AdminUser,
    metric: Literal["revenue", "units"] = "revenue",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    status: Literal["recorded", "voided", "all"] = "recorded",
    category_id: UUID | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> BestSellers:
    return await _service(request).bestsellers(
        metric=metric,
        limit=limit,
        **_filters(status, None, category_id, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/timeseries", response_model=SalesSeries)
async def sales_timeseries(
    request: Request,
    _admin: AdminUser,
    bucket: Literal["hour", "day"] = "day",
    status: Literal["recorded", "voided", "all"] = "recorded",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> SalesSeries:
    return await _service(request).timeseries(
        bucket=bucket,
        **_filters(status, None, None, None, None, None, occurred_from, occurred_to),
    )


@router.get("/analytics/breakdown", response_model=SalesBreakdown)
async def sales_breakdown(
    request: Request,
    _admin: AdminUser,
    group_by: SalesGroupBy = "category",
    status: Literal["recorded", "voided", "all"] = "recorded",
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    variant_id: UUID | None = None,
    coupon_code: str | None = None,
    country_code: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> SalesBreakdown:
    return await _service(request).breakdown(
        group_by=group_by,
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


@router.get("/{sale_id}", response_model=SaleResponse)
async def get_sale(sale_id: UUID, request: Request, _admin: AdminUser) -> SaleResponse:
    return SaleResponse(sale=await _service(request).get(sale_id))


@router.post("", response_model=SaleResponse, status_code=201)
async def create_sale(
    body: SaleCreate, request: Request, _admin: AdminUser
) -> SaleResponse:
    return SaleResponse(sale=await _service(request).create(body))


@router.patch("/{sale_id}", response_model=SaleResponse)
async def update_sale(
    sale_id: UUID, body: SaleUpdate, request: Request, _admin: AdminUser
) -> SaleResponse:
    return SaleResponse(sale=await _service(request).update(sale_id, body))


@router.post("/{sale_id}/void", response_model=SaleResponse)
async def void_sale(
    sale_id: UUID,
    request: Request,
    _admin: AdminUser,
    body: SaleVoidRequest | None = None,
) -> SaleResponse:
    reason = None if body is None else body.reason
    return SaleResponse(sale=await _service(request).void(sale_id, reason=reason))


@router.delete("/{sale_id}", status_code=204)
async def delete_sale(sale_id: UUID, request: Request, _admin: AdminUser) -> None:
    await _service(request).delete(sale_id)
