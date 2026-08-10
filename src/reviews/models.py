"""Postgres persistence for durable product reviews."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.catalog.models import Base, TimestampMixin


class ProductReviewRow(Base, TimestampMixin):
    __tablename__ = "product_reviews"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_product_reviews_rating"),
        CheckConstraint(
            "status IN ('published', 'hidden')", name="ck_product_reviews_status"
        ),
        CheckConstraint(
            "source IN ('checkout', 'admin', 'import')", name="ck_product_reviews_source"
        ),
        UniqueConstraint(
            "sandbox_session_id",
            "product_id",
            name="uq_product_reviews_session_product",
        ),
        Index("ix_product_reviews_product_status", "product_id", "status"),
        Index("ix_product_reviews_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="checkout")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="published")

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)

    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text, nullable=False)

    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sandbox_session_id: Mapped[str | None] = mapped_column(String(80))
    author_label: Mapped[str] = mapped_column(
        String(80), nullable=False, default="Verified buyer"
    )
