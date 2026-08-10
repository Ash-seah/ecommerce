"""Store delivery option on durable sale events for historical reporting.

Revision ID: 20260810_0009
Revises: 20260810_0008
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260810_0009"
down_revision = "20260810_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_events",
        sa.Column("delivery_option_id", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "sales_events",
        sa.Column("delivery_option_label", sa.String(length=120), nullable=True),
    )
    op.create_index(
        "ix_sales_events_delivery_option",
        "sales_events",
        ["delivery_option_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_sales_events_delivery_option", table_name="sales_events")
    op.drop_column("sales_events", "delivery_option_label")
    op.drop_column("sales_events", "delivery_option_id")
