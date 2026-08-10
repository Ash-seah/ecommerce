"""Add optional brand text field on catalog products.

Revision ID: 20260810_0007
Revises: 20260810_0006
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260810_0007"
down_revision = "20260810_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_products",
        sa.Column("brand", sa.String(length=120), nullable=True),
    )
    op.create_index("ix_catalog_products_brand", "catalog_products", ["brand"])


def downgrade() -> None:
    op.drop_index("ix_catalog_products_brand", table_name="catalog_products")
    op.drop_column("catalog_products", "brand")
