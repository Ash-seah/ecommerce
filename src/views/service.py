"""Sandbox and master traffic / views administration."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from src.sandbox.models import SandboxState
from src.sandbox.service import SandboxService
from src.views import analytics
from src.views.capture import apply_view_update, view_from_create
from src.views.repository import MasterViewsRepository
from src.views.schemas import (
    TopPaths,
    TopViewedCategories,
    TopViewedProducts,
    ViewCreate,
    ViewEvent,
    ViewKind,
    ViewList,
    ViewsByKind,
    ViewsFeed,
    ViewsSeries,
    ViewsSummary,
    ViewUpdate,
)


class ViewsAdminError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def _list_response(items: list[ViewEvent], page: int, page_size: int) -> ViewList:
    page_items, total, pages = analytics.page_items(items, page, page_size)
    return ViewList(
        items=tuple(page_items),
        page=page,
        page_size=page_size,
        total=total,
        pages=pages,
    )


class SandboxViewsService:
    def __init__(self, sandbox: SandboxService) -> None:
        self._sandbox = sandbox

    async def _events(self, session_id: str) -> list[ViewEvent]:
        state = await self._sandbox.inspect(session_id)
        return list(state.views.values())

    async def append(self, session_id: str, event: ViewEvent) -> ViewEvent:
        def mutation(state: SandboxState) -> SandboxState:
            views = dict(state.views)
            views[event.id] = event
            return state.model_copy(update={"views": views})

        await self._sandbox.mutate(session_id, mutation)
        return event

    async def list_views(
        self,
        session_id: str,
        *,
        page: int,
        page_size: int,
        status: Literal["recorded", "voided", "all"] = "recorded",
        kind: ViewKind | None = None,
        product_id: UUID | None = None,
        category_id: UUID | None = None,
        path: str | None = None,
        country_code: str | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> ViewList:
        events = analytics.filter_views(
            await self._events(session_id),
            status=status,
            kind=kind,
            product_id=product_id,
            category_id=category_id,
            path=path,
            country_code=country_code,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        )
        return _list_response(events, page, page_size)

    async def get(self, session_id: str, view_id: UUID) -> ViewEvent:
        state = await self._sandbox.inspect(session_id)
        event = state.views.get(view_id)
        if event is None:
            raise ViewsAdminError(404, "view_not_found", "View event was not found")
        return event

    async def create(self, session_id: str, body: ViewCreate) -> ViewEvent:
        event = view_from_create(body, sandbox_session_id=session_id)
        return await self.append(session_id, event)

    async def update(self, session_id: str, view_id: UUID, body: ViewUpdate) -> ViewEvent:
        result: list[ViewEvent] = []

        def mutation(state: SandboxState) -> SandboxState:
            current = state.views.get(view_id)
            if current is None:
                raise ViewsAdminError(404, "view_not_found", "View event was not found")
            updated = apply_view_update(current, body.model_dump(exclude_unset=True))
            views = dict(state.views)
            views[view_id] = updated
            result.append(updated)
            return state.model_copy(update={"views": views})

        await self._sandbox.mutate(session_id, mutation)
        return result[-1]

    async def void(self, session_id: str, view_id: UUID, *, reason: str | None) -> ViewEvent:
        return await self.update(
            session_id, view_id, ViewUpdate(status="voided", void_reason=reason)
        )

    async def delete(self, session_id: str, view_id: UUID) -> None:
        def mutation(state: SandboxState) -> SandboxState:
            views = dict(state.views)
            if views.pop(view_id, None) is None:
                raise ViewsAdminError(404, "view_not_found", "View event was not found")
            return state.model_copy(update={"views": views})

        await self._sandbox.mutate(session_id, mutation)

    async def summary(self, session_id: str, **filters: object) -> ViewsSummary:
        events = analytics.filter_views(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.summarize(events)

    async def timeseries(
        self, session_id: str, *, bucket: Literal["hour", "day"], **filters: object
    ) -> ViewsSeries:
        events = analytics.filter_views(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.timeseries(events, bucket=bucket)

    async def top_products(
        self, session_id: str, *, limit: int, **filters: object
    ) -> TopViewedProducts:
        events = analytics.filter_views(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.top_products(events, limit=limit)

    async def top_categories(
        self, session_id: str, *, limit: int, **filters: object
    ) -> TopViewedCategories:
        events = analytics.filter_views(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.top_categories(events, limit=limit)

    async def top_paths(
        self, session_id: str, *, limit: int, **filters: object
    ) -> TopPaths:
        events = analytics.filter_views(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.top_paths(events, limit=limit)

    async def by_kind(self, session_id: str, **filters: object) -> ViewsByKind:
        events = analytics.filter_views(await self._events(session_id), **filters)  # type: ignore[arg-type]
        return analytics.by_kind(events)

    async def feed(
        self, session_id: str, *, since: datetime | None, limit: int
    ) -> ViewsFeed:
        items = analytics.feed(await self._events(session_id), since=since, limit=limit)
        return ViewsFeed(
            items=tuple(items),
            next_since=items[0].recorded_at if items else since,
        )


class MasterViewsService:
    def __init__(self, repository: MasterViewsRepository) -> None:
        self._repo = repository

    async def list_views(
        self,
        *,
        page: int,
        page_size: int,
        status: Literal["recorded", "voided", "all"] = "recorded",
        kind: ViewKind | None = None,
        product_id: UUID | None = None,
        category_id: UUID | None = None,
        path: str | None = None,
        country_code: str | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> ViewList:
        events = analytics.filter_views(
            await self._repo.list_all(),
            status=status,
            kind=kind,
            product_id=product_id,
            category_id=category_id,
            path=path,
            country_code=country_code,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        )
        return _list_response(events, page, page_size)

    async def get(self, view_id: UUID) -> ViewEvent:
        return await self._repo.get(view_id)

    async def create(self, body: ViewCreate) -> ViewEvent:
        return await self._repo.create(body)

    async def update(self, view_id: UUID, body: ViewUpdate) -> ViewEvent:
        return await self._repo.update(view_id, body)

    async def void(self, view_id: UUID, *, reason: str | None) -> ViewEvent:
        return await self._repo.void(view_id, reason=reason)

    async def delete(self, view_id: UUID) -> None:
        await self._repo.delete(view_id)

    async def summary(self, **filters: object) -> ViewsSummary:
        events = analytics.filter_views(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.summarize(events)

    async def timeseries(self, *, bucket: Literal["hour", "day"], **filters: object) -> ViewsSeries:
        events = analytics.filter_views(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.timeseries(events, bucket=bucket)

    async def top_products(self, *, limit: int, **filters: object) -> TopViewedProducts:
        events = analytics.filter_views(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.top_products(events, limit=limit)

    async def top_categories(self, *, limit: int, **filters: object) -> TopViewedCategories:
        events = analytics.filter_views(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.top_categories(events, limit=limit)

    async def top_paths(self, *, limit: int, **filters: object) -> TopPaths:
        events = analytics.filter_views(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.top_paths(events, limit=limit)

    async def by_kind(self, **filters: object) -> ViewsByKind:
        events = analytics.filter_views(await self._repo.list_all(), **filters)  # type: ignore[arg-type]
        return analytics.by_kind(events)

    async def feed(self, *, since: datetime | None, limit: int) -> ViewsFeed:
        items = analytics.feed(await self._repo.list_all(), since=since, limit=limit)
        return ViewsFeed(
            items=tuple(items),
            next_since=items[0].recorded_at if items else since,
        )
