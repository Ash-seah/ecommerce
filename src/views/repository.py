"""Master views ledger backed by Postgres."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.views.capture import apply_view_update, view_from_create
from src.views.models import ViewEventRow
from src.views.schemas import ViewCreate, ViewEvent, ViewUpdate


class MasterViewsError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def _to_event(row: ViewEventRow) -> ViewEvent:
    return ViewEvent.model_validate(row, from_attributes=True)


def _apply_row(row: ViewEventRow, event: ViewEvent) -> None:
    for field in ViewEvent.model_fields:
        setattr(row, field, getattr(event, field))


class MasterViewsRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(self) -> list[ViewEvent]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(ViewEventRow).order_by(ViewEventRow.occurred_at.desc())
                )
            ).all()
        return [_to_event(row) for row in rows]

    async def get(self, view_id: UUID) -> ViewEvent:
        async with self._sessions() as session:
            row = await session.get(ViewEventRow, view_id)
            if row is None:
                raise MasterViewsError(404, "view_not_found", "View event was not found")
            return _to_event(row)

    async def insert_many(self, events: list[ViewEvent]) -> None:
        if not events:
            return
        async with self._sessions.begin() as session:
            for event in events:
                row = ViewEventRow(id=event.id)
                _apply_row(row, event)
                session.add(row)

    async def create(self, body: ViewCreate) -> ViewEvent:
        event = view_from_create(body)
        async with self._sessions.begin() as session:
            row = ViewEventRow(id=event.id)
            _apply_row(row, event)
            session.add(row)
        return event

    async def update(self, view_id: UUID, body: ViewUpdate) -> ViewEvent:
        async with self._sessions.begin() as session:
            row = await session.get(ViewEventRow, view_id)
            if row is None:
                raise MasterViewsError(404, "view_not_found", "View event was not found")
            current = _to_event(row)
            updated = apply_view_update(current, body.model_dump(exclude_unset=True))
            _apply_row(row, updated)
            await session.flush()
            return updated

    async def void(self, view_id: UUID, *, reason: str | None) -> ViewEvent:
        async with self._sessions.begin() as session:
            row = await session.get(ViewEventRow, view_id)
            if row is None:
                raise MasterViewsError(404, "view_not_found", "View event was not found")
            if row.status == "voided":
                raise MasterViewsError(409, "view_already_voided", "View is already voided")
            row.status = "voided"
            row.voided_at = datetime.now(UTC)
            row.void_reason = reason
            await session.flush()
            return _to_event(row)

    async def delete(self, view_id: UUID) -> None:
        async with self._sessions.begin() as session:
            row = await session.get(ViewEventRow, view_id)
            if row is None:
                raise MasterViewsError(404, "view_not_found", "View event was not found")
            await session.delete(row)
