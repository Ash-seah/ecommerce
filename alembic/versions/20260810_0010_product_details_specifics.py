"""Add product details text and specifics tag list.

Revision ID: 20260810_0010
Revises: 20260810_0009
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260810_0010"
down_revision = "20260810_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_products",
        sa.Column("details", sa.Text(), nullable=True),
    )
    op.add_column(
        "catalog_products",
        sa.Column(
            "specifics",
            postgresql.ARRAY(sa.String(length=80)),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("catalog_products", "specifics")
    op.drop_column("catalog_products", "details")
