"""Master sales ledger backed by Postgres."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.sales.capture import apply_sale_update, sale_from_create
from src.sales.models import SalesEventRow
from src.sales.schemas import SaleCreate, SaleEvent, SaleUpdate


class MasterSalesError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def _to_event(row: SalesEventRow) -> SaleEvent:
    return SaleEvent.model_validate(row, from_attributes=True)


def _apply_row(row: SalesEventRow, event: SaleEvent) -> None:
    for field in SaleEvent.model_fields:
        setattr(row, field, getattr(event, field))


class MasterSalesRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(self) -> list[SaleEvent]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(SalesEventRow).order_by(SalesEventRow.occurred_at.desc())
                )
            ).all()
        return [_to_event(row) for row in rows]

    async def get(self, sale_id: UUID) -> SaleEvent:
        async with self._sessions() as session:
            row = await session.get(SalesEventRow, sale_id)
            if row is None:
                raise MasterSalesError(404, "sale_not_found", "Sale was not found")
            return _to_event(row)

    async def insert_many(self, events: list[SaleEvent]) -> None:
        if not events:
            return
        async with self._sessions.begin() as session:
            for event in events:
                row = SalesEventRow(id=event.id)
                _apply_row(row, event)
                session.add(row)

    async def create(self, body: SaleCreate) -> SaleEvent:
        event = sale_from_create(body)
        async with self._sessions.begin() as session:
            row = SalesEventRow(id=event.id)
            _apply_row(row, event)
            session.add(row)
        return event

    async def update(self, sale_id: UUID, body: SaleUpdate) -> SaleEvent:
        async with self._sessions.begin() as session:
            row = await session.get(SalesEventRow, sale_id)
            if row is None:
                raise MasterSalesError(404, "sale_not_found", "Sale was not found")
            current = _to_event(row)
            updated = apply_sale_update(current, body.model_dump(exclude_unset=True))
            _apply_row(row, updated)
            await session.flush()
            return updated

    async def void(
        self, sale_id: UUID, *, reason: str | None
    ) -> SaleEvent:
        async with self._sessions.begin() as session:
            row = await session.get(SalesEventRow, sale_id)
            if row is None:
                raise MasterSalesError(404, "sale_not_found", "Sale was not found")
            if row.status == "voided":
                raise MasterSalesError(409, "sale_already_voided", "Sale is already voided")
            row.status = "voided"
            row.voided_at = datetime.now(UTC)
            row.void_reason = reason
            await session.flush()
            return _to_event(row)

    async def delete(self, sale_id: UUID) -> None:
        async with self._sessions.begin() as session:
            row = await session.get(SalesEventRow, sale_id)
            if row is None:
                raise MasterSalesError(404, "sale_not_found", "Sale was not found")
            await session.delete(row)

    async def void_order(
        self, order_id: UUID, *, reason: str
    ) -> int:
        async with self._sessions.begin() as session:
            rows = (
                await session.scalars(
                    select(SalesEventRow).where(
                        SalesEventRow.order_id == order_id,
                        SalesEventRow.status == "recorded",
                    )
                )
            ).all()
            now = datetime.now(UTC)
            for row in rows:
                row.status = "voided"
                row.voided_at = now
                row.void_reason = reason
            return len(rows)
