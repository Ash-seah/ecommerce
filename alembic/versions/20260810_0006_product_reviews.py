"""Create durable product_reviews ledger for verified-buyer comments.

Revision ID: 20260810_0006
Revises: 20260806_0005
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260810_0006"
down_revision = "20260806_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="checkout"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="published"),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_slug", sa.String(length=120), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sandbox_session_id", sa.String(length=80), nullable=True),
        sa.Column(
            "author_label",
            sa.String(length=80),
            nullable=False,
            server_default="Verified buyer",
        ),
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
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_product_reviews_rating"),
        sa.CheckConstraint(
            "status IN ('published', 'hidden')", name="ck_product_reviews_status"
        ),
        sa.CheckConstraint(
            "source IN ('checkout', 'admin', 'import')",
            name="ck_product_reviews_source",
        ),
        sa.UniqueConstraint(
            "sandbox_session_id",
            "product_id",
            name="uq_product_reviews_session_product",
        ),
    )
    op.create_index(
        "ix_product_reviews_product_status",
        "product_reviews",
        ["product_id", "status"],
    )
    op.create_index("ix_product_reviews_created_at", "product_reviews", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_product_reviews_created_at", table_name="product_reviews")
    op.drop_index("ix_product_reviews_product_status", table_name="product_reviews")
    op.drop_table("product_reviews")
