"""Sandbox and master sales administration."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from src.sales import analytics
from src.sales.capture import apply_sale_update, sale_from_create
from src.sales.repository import MasterSalesRepository
from src.sales.schemas import (
    BestSellers,
    CategorySales,
    CouponSales,
    GeoSales,
    SaleCreate,
    SaleEvent,
    SaleList,
    SalesFeed,
    SalesSeries,
    SalesSummary,
    SaleUpdate,
)
from src.sandbox.models import SandboxState
from src.sandbox.service import SandboxService


class SalesAdminError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def _list_response(items: list[SaleEvent], page: int, page_size: int) -> SaleList:
    page_items, total, pages = analytics.page_items(items, page, page_size)
    return SaleList(
        items=tuple(page_items),
        page=page,
        page_size=page_size,
        total=total,
        pages=pages,
    )


class SandboxSalesService:
    def __init__(self, sandbox: SandboxService) -> None:
        self._sandbox = sandbox

    async def _events(self, session_id: str) -> list[SaleEvent]:
        state = await self._sandbox.inspect(session_id)
        return list(state.sales.values())

    async def list_sales(
        self,
        session_id: str,
        *,
        page: int,
        page_size: int,
        status: Literal["recorded", "voided", "all"] = "recorded",
        product_id: UUID | None = None,
        category_id: UUID | None = None,
        variant_id: UUID | None = None,
        coupon_code: str | None = None,
        country_code: str | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> SaleList:
        events = analytics.filter_sales(
            await self._events(session_id),
            status=status,
            product_id=product_id,
            category_id=category_id,
            variant_id=variant_id,
            coupon_code=coupon_code,
            country_code=country_code,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        )
        return _list_response(events, page, page_size)

    async def get(self, session_id: str, sale_id: UUID) -> SaleEvent:
        state = await self._sandbox.inspect(session_id)
        sale = state.sales.get(sale_id)
        if sale is None:
            raise SalesAdminError(404, "sale_not_found", "Sale was not found")
        return sale

    async def create(self, session_id: str, body: SaleCreate) -> SaleEvent:
        event = sale_from_create(body, sandbox_session_id=session_id)

        def mutation(state: SandboxState) -> SandboxState:
            sales = dict(state.sales)
            sales[event.id] = event
            return state.model_copy(update={"sales": sales})

        await self._sandbox.mutate(session_id, mutation)
        return event

    async def update(self, session_id: str, sale_id: UUID, body: SaleUpdate) -> SaleEvent:
        result: list[SaleEvent] = []

        def mutation(state: SandboxState) -> SandboxState:
            current = state.sales.get(sale_id)
            if current is None:
                raise SalesAdminError(404, "sale_not_found", "Sale was not found")
            updated = apply_sale_update(current, body.model_dump(exclude_unset=True))
            sales = dict(state.sales)
            sales[sale_id] = updated
            result.append(updated)
            return state.model_copy(update={"sales": sales})

        await self._sandbox.mutate(session_id, mutation)
        return result[-1]

    async def void(self, session_id: str, sale_id: UUID, *, reason: str | None) -> SaleEvent:
        return await self.update(
            session_id,
            sale_id,
            SaleUpdate(status="voided", void_reason=reason),
        )

    async def delete(self, session_id: str, sale_id: UUID) -> None:
        def mutation(state: SandboxState) -> SandboxState:
            sales = dict(state.sales)
            if sales.pop(sale_id, None) is None:
                raise SalesAdminError(404, "sale_not_found", "Sale was not found")
            return state.model_copy(update={"sales": sales})

        await self._sandbox.mutate(session_id, mutation)

    async def summary(self, session_id: str, **filters: object) -> SalesSummary:
        events = analytics.filter_sales(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.summarize(events)

    async def bestsellers(
        self, session_id: str, *, metric: Literal["revenue", "units"], limit: int, **filters: object
    ) -> BestSellers:
        events = analytics.filter_sales(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.bestsellers(events, metric=metric, limit=limit)

    async def timeseries(
        self, session_id: str, *, bucket: Literal["hour", "day"], **filters: object
    ) -> SalesSeries:
        events = analytics.filter_sales(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.timeseries(events, bucket=bucket)

    async def by_category(self, session_id: str, **filters: object) -> CategorySales:
        events = analytics.filter_sales(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.by_category(events)

    async def by_coupon(self, session_id: str, **filters: object) -> CouponSales:
        events = analytics.filter_sales(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.by_coupon(events)

    async def by_geo(self, session_id: str, **filters: object) -> GeoSales:
        events = analytics.filter_sales(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.by_geo(events)

    async def feed(
        self, session_id: str, *, since: datetime | None, limit: int
    ) -> SalesFeed:
        items = analytics.feed(await self._events(session_id), since=since, limit=limit)
        return SalesFeed(
            items=tuple(items),
            next_since=items[0].recorded_at if items else since,
        )


class MasterSalesService:
    def __init__(self, repository: MasterSalesRepository) -> None:
        self._repo = repository

    async def list_sales(
        self,
        *,
        page: int,
        page_size: int,
        status: Literal["recorded", "voided", "all"] = "recorded",
        product_id: UUID | None = None,
        category_id: UUID | None = None,
        variant_id: UUID | None = None,
        coupon_code: str | None = None,
        country_code: str | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> SaleList:
        events = analytics.filter_sales(
            await self._repo.list_all(),
            status=status,
            product_id=product_id,
            category_id=category_id,
            variant_id=variant_id,
            coupon_code=coupon_code,
            country_code=country_code,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        )
        return _list_response(events, page, page_size)

    async def get(self, sale_id: UUID) -> SaleEvent:
        return await self._repo.get(sale_id)

    async def create(self, body: SaleCreate) -> SaleEvent:
        return await self._repo.create(body)

    async def update(self, sale_id: UUID, body: SaleUpdate) -> SaleEvent:
        return await self._repo.update(sale_id, body)

    async def void(self, sale_id: UUID, *, reason: str | None) -> SaleEvent:
        return await self._repo.void(sale_id, reason=reason)

    async def delete(self, sale_id: UUID) -> None:
        await self._repo.delete(sale_id)

    async def summary(self, **filters: object) -> SalesSummary:
        events = analytics.filter_sales(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.summarize(events)

    async def bestsellers(
        self, *, metric: Literal["revenue", "units"], limit: int, **filters: object
    ) -> BestSellers:
        events = analytics.filter_sales(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.bestsellers(events, metric=metric, limit=limit)

    async def timeseries(self, *, bucket: Literal["hour", "day"], **filters: object) -> SalesSeries:
        events = analytics.filter_sales(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.timeseries(events, bucket=bucket)

    async def by_category(self, **filters: object) -> CategorySales:
        events = analytics.filter_sales(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.by_category(events)

    async def by_coupon(self, **filters: object) -> CouponSales:
        events = analytics.filter_sales(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.by_coupon(events)

    async def by_geo(self, **filters: object) -> GeoSales:
        events = analytics.filter_sales(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.by_geo(events)

    async def feed(self, *, since: datetime | None, limit: int) -> SalesFeed:
        items = analytics.feed(await self._repo.list_all(), since=since, limit=limit)
        return SalesFeed(
            items=tuple(items),
            next_since=items[0].recorded_at if items else since,
        )
