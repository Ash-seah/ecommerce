"""Add product discount_percent and media is_main.

Revision ID: 20260806_0003
Revises: 20260806_0002
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260806_0003"
down_revision = "20260806_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_products",
        sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_catalog_products_discount_percent",
        "catalog_products",
        "discount_percent >= 0 AND discount_percent <= 100",
    )
    op.add_column(
        "catalog_media",
        sa.Column("is_main", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("catalog_media", "is_main")
    op.drop_constraint(
        "ck_catalog_products_discount_percent", "catalog_products", type_="check"
    )
    op.drop_column("catalog_products", "discount_percent")
