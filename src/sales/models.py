"""Postgres persistence for durable master sales analytics."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.catalog.models import Base, TimestampMixin


class SalesEventRow(Base, TimestampMixin):
    """Durable sale facts. Names/prices are denormalized so catalog edits do not rewrite history."""

    __tablename__ = "sales_events"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sales_events_quantity_positive"),
        CheckConstraint(
            "list_unit_price_minor >= 0", name="ck_sales_events_list_price_nonnegative"
        ),
        CheckConstraint("unit_price_minor >= 0", name="ck_sales_events_unit_price_nonnegative"),
        CheckConstraint("line_gross_minor >= 0", name="ck_sales_events_gross_nonnegative"),
        CheckConstraint(
            "allocated_discount_minor >= 0", name="ck_sales_events_discount_nonnegative"
        ),
        CheckConstraint(
            "allocated_shipping_minor >= 0", name="ck_sales_events_shipping_nonnegative"
        ),
        CheckConstraint("allocated_tax_minor >= 0", name="ck_sales_events_tax_nonnegative"),
        CheckConstraint("line_net_minor >= 0", name="ck_sales_events_net_nonnegative"),
        CheckConstraint(
            "product_discount_percent >= 0 AND product_discount_percent <= 100",
            name="ck_sales_events_product_discount",
        ),
        CheckConstraint("status IN ('recorded', 'voided')", name="ck_sales_events_status"),
        CheckConstraint(
            "source IN ('checkout', 'admin', 'import')", name="ck_sales_events_source"
        ),
        Index("ix_sales_events_occurred_at", "occurred_at"),
        Index("ix_sales_events_product", "product_id"),
        Index("ix_sales_events_category", "category_id"),
        Index("ix_sales_events_order", "order_id"),
        Index("ix_sales_events_status_occurred", "status", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="checkout")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="recorded")

    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    line_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    category_slug: Mapped[str | None] = mapped_column(String(100))
    category_name: Mapped[str | None] = mapped_column(String(160))

    variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    variant_sku: Mapped[str] = mapped_column(String(80), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(160), nullable=False)

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    list_unit_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    line_gross_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_discount_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allocated_shipping_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allocated_tax_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_net_minor: Mapped[int] = mapped_column(Integer, nullable=False)

    product_discount_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coupon_code: Mapped[str | None] = mapped_column(String(40))

    country_code: Mapped[str | None] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(32))

    sandbox_session_id: Mapped[str | None] = mapped_column(String(80))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(240))
    notes: Mapped[str | None] = mapped_column(Text)
