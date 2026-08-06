"""Create durable view_events ledger for master traffic analytics.

Revision ID: 20260806_0005
Revises: 20260806_0004
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260806_0005"
down_revision = "20260806_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "view_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="client"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="recorded"),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=True),
        sa.Column("referrer", sa.String(length=500), nullable=True),
        sa.Column("query", sa.String(length=240), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("product_slug", sa.String(length=120), nullable=True),
        sa.Column("product_name", sa.String(length=200), nullable=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category_slug", sa.String(length=100), nullable=True),
        sa.Column("category_name", sa.String(length=160), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("region", sa.String(length=100), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("user_agent", sa.String(length=400), nullable=True),
        sa.Column("sandbox_session_id", sa.String(length=80), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.String(length=240), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("status IN ('recorded', 'voided')", name="ck_view_events_status"),
        sa.CheckConstraint(
            "source IN ('client', 'auto', 'admin', 'import')",
            name="ck_view_events_source",
        ),
        sa.CheckConstraint(
            "kind IN ('visit', 'product_view', 'category_view', 'listing_view', 'search')",
            name="ck_view_events_kind",
        ),
    )
    op.create_index("ix_view_events_occurred_at", "view_events", ["occurred_at"])
    op.create_index("ix_view_events_kind_occurred", "view_events", ["kind", "occurred_at"])
    op.create_index("ix_view_events_product", "view_events", ["product_id"])
    op.create_index("ix_view_events_category", "view_events", ["category_id"])
    op.create_index(
        "ix_view_events_status_occurred", "view_events", ["status", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_view_events_status_occurred", table_name="view_events")
    op.drop_index("ix_view_events_category", table_name="view_events")
    op.drop_index("ix_view_events_product", table_name="view_events")
    op.drop_index("ix_view_events_kind_occurred", table_name="view_events")
    op.drop_index("ix_view_events_occurred_at", table_name="view_events")
    op.drop_table("view_events")
