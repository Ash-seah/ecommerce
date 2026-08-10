"""Add category colors and allow media to attach to categories.

Revision ID: 20260810_0008
Revises: 20260810_0007
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260810_0008"
down_revision = "20260810_0007"
branch_labels = None
depends_on = None

_HEX = r"^#[0-9A-Fa-f]{6}$"


def upgrade() -> None:
    op.add_column(
        "catalog_categories",
        sa.Column("color", sa.String(length=7), nullable=True),
    )
    op.add_column(
        "catalog_categories",
        sa.Column("accent_color", sa.String(length=7), nullable=True),
    )
    op.create_check_constraint(
        "ck_catalog_categories_color_hex",
        "catalog_categories",
        f"color IS NULL OR color ~ '{_HEX}'",
    )
    op.create_check_constraint(
        "ck_catalog_categories_accent_color_hex",
        "catalog_categories",
        f"accent_color IS NULL OR accent_color ~ '{_HEX}'",
    )

    op.add_column(
        "catalog_media",
        sa.Column("category_id", sa.UUID(), nullable=True),
    )
    op.alter_column("catalog_media", "product_id", existing_type=sa.UUID(), nullable=True)
    op.create_foreign_key(
        "fk_catalog_media_category_id",
        "catalog_media",
        "catalog_categories",
        ["category_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_catalog_media_product_object", "catalog_media", type_="unique")
    op.create_index(
        "uq_catalog_media_product_object",
        "catalog_media",
        ["product_id", "object_key"],
        unique=True,
        postgresql_where=sa.text("product_id IS NOT NULL"),
    )
    op.create_index(
        "uq_catalog_media_category_object",
        "catalog_media",
        ["category_id", "object_key"],
        unique=True,
        postgresql_where=sa.text("category_id IS NOT NULL"),
    )
    op.create_index(
        "ix_catalog_media_category_active",
        "catalog_media",
        ["category_id", "is_active"],
        postgresql_where=sa.text("category_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_catalog_media_owner",
        "catalog_media",
        "("
        "category_id IS NOT NULL AND product_id IS NULL AND variant_id IS NULL"
        ") OR ("
        "category_id IS NULL AND product_id IS NOT NULL"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_catalog_media_owner", "catalog_media", type_="check")
    op.drop_index("ix_catalog_media_category_active", table_name="catalog_media")
    op.drop_index("uq_catalog_media_category_object", table_name="catalog_media")
    op.drop_index("uq_catalog_media_product_object", table_name="catalog_media")
    op.create_unique_constraint(
        "uq_catalog_media_product_object",
        "catalog_media",
        ["product_id", "object_key"],
    )
    op.drop_constraint("fk_catalog_media_category_id", "catalog_media", type_="foreignkey")
    op.execute("DELETE FROM catalog_media WHERE product_id IS NULL")
    op.alter_column("catalog_media", "product_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("catalog_media", "category_id")

    op.drop_constraint("ck_catalog_categories_accent_color_hex", "catalog_categories", type_="check")
    op.drop_constraint("ck_catalog_categories_color_hex", "catalog_categories", type_="check")
    op.drop_column("catalog_categories", "accent_color")
    op.drop_column("catalog_categories", "color")
