"""Create durable sales_events ledger for master analytics.

Revision ID: 20260806_0004
Revises: 20260806_0003
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260806_0004"
down_revision = "20260806_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="checkout"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="recorded"),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("line_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_slug", sa.String(length=120), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_slug", sa.String(length=100), nullable=True),
        sa.Column("category_name", sa.String(length=160), nullable=True),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_sku", sa.String(length=80), nullable=False),
        sa.Column("variant_name", sa.String(length=160), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("list_unit_price_minor", sa.Integer(), nullable=False),
        sa.Column("unit_price_minor", sa.Integer(), nullable=False),
        sa.Column("line_gross_minor", sa.Integer(), nullable=False),
        sa.Column("allocated_discount_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("allocated_shipping_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("allocated_tax_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("line_net_minor", sa.Integer(), nullable=False),
        sa.Column("product_discount_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coupon_code", sa.String(length=40), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("region", sa.String(length=100), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("postal_code", sa.String(length=32), nullable=True),
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
        sa.CheckConstraint("quantity > 0", name="ck_sales_events_quantity_positive"),
        sa.CheckConstraint(
            "list_unit_price_minor >= 0", name="ck_sales_events_list_price_nonnegative"
        ),
        sa.CheckConstraint(
            "unit_price_minor >= 0", name="ck_sales_events_unit_price_nonnegative"
        ),
        sa.CheckConstraint("line_gross_minor >= 0", name="ck_sales_events_gross_nonnegative"),
        sa.CheckConstraint(
            "allocated_discount_minor >= 0", name="ck_sales_events_discount_nonnegative"
        ),
        sa.CheckConstraint(
            "allocated_shipping_minor >= 0", name="ck_sales_events_shipping_nonnegative"
        ),
        sa.CheckConstraint("allocated_tax_minor >= 0", name="ck_sales_events_tax_nonnegative"),
        sa.CheckConstraint("line_net_minor >= 0", name="ck_sales_events_net_nonnegative"),
        sa.CheckConstraint(
            "product_discount_percent >= 0 AND product_discount_percent <= 100",
            name="ck_sales_events_product_discount",
        ),
        sa.CheckConstraint("status IN ('recorded', 'voided')", name="ck_sales_events_status"),
        sa.CheckConstraint(
            "source IN ('checkout', 'admin', 'import')", name="ck_sales_events_source"
        ),
    )
    op.create_index("ix_sales_events_occurred_at", "sales_events", ["occurred_at"])
    op.create_index("ix_sales_events_product", "sales_events", ["product_id"])
    op.create_index("ix_sales_events_category", "sales_events", ["category_id"])
    op.create_index("ix_sales_events_order", "sales_events", ["order_id"])
    op.create_index(
        "ix_sales_events_status_occurred", "sales_events", ["status", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_sales_events_status_occurred", table_name="sales_events")
    op.drop_index("ix_sales_events_order", table_name="sales_events")
    op.drop_index("ix_sales_events_category", table_name="sales_events")
    op.drop_index("ix_sales_events_product", table_name="sales_events")
    op.drop_index("ix_sales_events_occurred_at", table_name="sales_events")
    op.drop_table("sales_events")
