"""Postgres persistence for durable master traffic analytics."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.catalog.models import Base, TimestampMixin


class ViewEventRow(Base, TimestampMixin):
    __tablename__ = "view_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('recorded', 'voided')", name="ck_view_events_status"
        ),
        CheckConstraint(
            "source IN ('client', 'auto', 'admin', 'import')",
            name="ck_view_events_source",
        ),
        CheckConstraint(
            "kind IN ('visit', 'product_view', 'category_view', 'listing_view', 'search')",
            name="ck_view_events_kind",
        ),
        Index("ix_view_events_occurred_at", "occurred_at"),
        Index("ix_view_events_kind_occurred", "kind", "occurred_at"),
        Index("ix_view_events_product", "product_id"),
        Index("ix_view_events_category", "category_id"),
        Index("ix_view_events_status_occurred", "status", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="client")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="recorded")
    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    path: Mapped[str | None] = mapped_column(String(500))
    referrer: Mapped[str | None] = mapped_column(String(500))
    query: Mapped[str | None] = mapped_column(String(240))

    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    product_slug: Mapped[str | None] = mapped_column(String(120))
    product_name: Mapped[str | None] = mapped_column(String(200))
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    category_slug: Mapped[str | None] = mapped_column(String(100))
    category_name: Mapped[str | None] = mapped_column(String(160))

    country_code: Mapped[str | None] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))

    user_agent: Mapped[str | None] = mapped_column(String(400))
    sandbox_session_id: Mapped[str | None] = mapped_column(String(80))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(240))
    notes: Mapped[str | None] = mapped_column(Text)
