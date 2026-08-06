"""Add optional variant_id to catalog media.

Revision ID: 20260806_0002
Revises: 20260730_0001
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260806_0002"
down_revision = "20260730_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_media",
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_catalog_media_variant_id",
        "catalog_media",
        "catalog_variants",
        ["variant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_catalog_media_variant_active",
        "catalog_media",
        ["variant_id", "is_active"],
        postgresql_where=sa.text("variant_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_media_variant_active", table_name="catalog_media")
    op.drop_constraint("fk_catalog_media_variant_id", "catalog_media", type_="foreignkey")
    op.drop_column("catalog_media", "variant_id")
