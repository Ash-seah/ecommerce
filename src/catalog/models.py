"""Relational master-catalog models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Catalog declarative base."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CatalogRevision(Base, TimestampMixin):
    __tablename__ = "catalog_revisions"
    __table_args__ = (
        CheckConstraint("revision_number > 0", name="ck_catalog_revisions_number_positive"),
        Index(
            "uq_catalog_revisions_one_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    categories: Mapped[list[Category]] = relationship(back_populates="revision")
    products: Mapped[list[Product]] = relationship(back_populates="revision")


class Category(Base, TimestampMixin):
    __tablename__ = "catalog_categories"
    __table_args__ = (
        UniqueConstraint("revision_id", "slug", name="uq_catalog_categories_revision_slug"),
        CheckConstraint("length(slug) > 0", name="ck_catalog_categories_slug_not_empty"),
        Index("ix_catalog_categories_revision_active", "revision_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_revisions.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog_categories.id", ondelete="RESTRICT")
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    revision: Mapped[CatalogRevision] = relationship(back_populates="categories")
    products: Mapped[list[Product]] = relationship(back_populates="category")


class Product(Base, TimestampMixin):
    __tablename__ = "catalog_products"
    __table_args__ = (
        UniqueConstraint("revision_id", "slug", name="uq_catalog_products_revision_slug"),
        CheckConstraint("length(slug) > 0", name="ck_catalog_products_slug_not_empty"),
        CheckConstraint(
            "discount_percent >= 0 AND discount_percent <= 100",
            name="ck_catalog_products_discount_percent",
        ),
        Index("ix_catalog_products_revision_active", "revision_id", "is_active"),
        Index("ix_catalog_products_category", "category_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_revisions.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_categories.id", ondelete="RESTRICT"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    revision: Mapped[CatalogRevision] = relationship(back_populates="products")
    category: Mapped[Category] = relationship(back_populates="products")
    variants: Mapped[list[ProductVariant]] = relationship(back_populates="product")
    media: Mapped[list[MediaMetadata]] = relationship(back_populates="product")


class ProductVariant(Base, TimestampMixin):
    __tablename__ = "catalog_variants"
    __table_args__ = (
        UniqueConstraint("product_id", "sku", name="uq_catalog_variants_product_sku"),
        CheckConstraint("price_minor >= 0", name="ck_catalog_variants_price_nonnegative"),
        CheckConstraint("length(currency) = 3", name="ck_catalog_variants_currency_length"),
        CheckConstraint(
            "currency = upper(currency)", name="ck_catalog_variants_currency_uppercase"
        ),
        Index("ix_catalog_variants_product_active", "product_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_products.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    product: Mapped[Product] = relationship(back_populates="variants")
    media: Mapped[list[MediaMetadata]] = relationship(back_populates="variant")


class MediaMetadata(Base, TimestampMixin):
    __tablename__ = "catalog_media"
    __table_args__ = (
        UniqueConstraint("product_id", "object_key", name="uq_catalog_media_product_object"),
        CheckConstraint("byte_size >= 0", name="ck_catalog_media_size_nonnegative"),
        CheckConstraint("sort_order >= 0", name="ck_catalog_media_sort_nonnegative"),
        Index("ix_catalog_media_product_active", "product_id", "is_active"),
        Index(
            "ix_catalog_media_variant_active",
            "variant_id",
            "is_active",
            postgresql_where=text("variant_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_products.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="CASCADE")
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(240), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_main: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    product: Mapped[Product] = relationship(back_populates="media")
    variant: Mapped[ProductVariant | None] = relationship(back_populates="media")
