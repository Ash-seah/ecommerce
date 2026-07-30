"""Create the revisioned master catalog.

Revision ID: 20260730_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260730_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "catalog_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("revision_number > 0", name="ck_catalog_revisions_number_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_number"),
    )
    op.create_index(
        "uq_catalog_revisions_one_active",
        "catalog_revisions",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "catalog_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("length(slug) > 0", name="ck_catalog_categories_slug_not_empty"),
        sa.ForeignKeyConstraint(["parent_id"], ["catalog_categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revision_id"], ["catalog_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "slug", name="uq_catalog_categories_revision_slug"),
    )
    op.create_index(
        "ix_catalog_categories_revision_active",
        "catalog_categories",
        ["revision_id", "is_active"],
    )
    op.create_table(
        "catalog_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("length(slug) > 0", name="ck_catalog_products_slug_not_empty"),
        sa.ForeignKeyConstraint(["category_id"], ["catalog_categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revision_id"], ["catalog_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "slug", name="uq_catalog_products_revision_slug"),
    )
    op.create_index("ix_catalog_products_category", "catalog_products", ["category_id"])
    op.create_index(
        "ix_catalog_products_revision_active",
        "catalog_products",
        ["revision_id", "is_active"],
    )
    op.create_table(
        "catalog_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("length(currency) = 3", name="ck_catalog_variants_currency_length"),
        sa.CheckConstraint(
            "currency = upper(currency)",
            name="ck_catalog_variants_currency_uppercase",
        ),
        sa.CheckConstraint("price_minor >= 0", name="ck_catalog_variants_price_nonnegative"),
        sa.ForeignKeyConstraint(["product_id"], ["catalog_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "sku", name="uq_catalog_variants_product_sku"),
    )
    op.create_index(
        "ix_catalog_variants_product_active",
        "catalog_variants",
        ["product_id", "is_active"],
    )
    op.create_table(
        "catalog_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("alt_text", sa.String(length=240), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("byte_size >= 0", name="ck_catalog_media_size_nonnegative"),
        sa.CheckConstraint("sort_order >= 0", name="ck_catalog_media_sort_nonnegative"),
        sa.ForeignKeyConstraint(["product_id"], ["catalog_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "object_key", name="uq_catalog_media_product_object"),
    )
    op.create_index(
        "ix_catalog_media_product_active",
        "catalog_media",
        ["product_id", "is_active"],
    )
    op.execute(
        "GRANT SELECT ON catalog_revisions, catalog_categories, catalog_products, "
        "catalog_variants, catalog_media TO ecommerce_reader"
    )


def downgrade() -> None:
    op.drop_table("catalog_media")
    op.drop_table("catalog_variants")
    op.drop_table("catalog_products")
    op.drop_table("catalog_categories")
    op.drop_table("catalog_revisions")
