"""Add pgvector RAG chunk store.

Revision ID: 20260816_0011
Revises: 20260810_0010
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260816_0011"
down_revision = "20260810_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS vector;
        EXCEPTION
            WHEN OTHERS THEN
                RAISE NOTICE 'pgvector extension not installed: %', SQLERRM;
        END
        $$;
        """
    )
    op.create_table(
        "rag_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ref_id", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.UniqueConstraint("source", "ref_id", name="uq_rag_chunks_source_ref"),
    )
    op.create_index("ix_rag_chunks_source", "rag_chunks", ["source"])
    op.create_index("ix_rag_chunks_product", "rag_chunks", ["product_id"])
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                ALTER TABLE rag_chunks
                    ADD COLUMN IF NOT EXISTS embedding_vec vector(768);
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'rag_chunks' AND column_name = 'embedding_vec'
            ) THEN
                CREATE INDEX IF NOT EXISTS ix_rag_chunks_embedding_hnsw
                    ON rag_chunks USING hnsw (embedding_vec vector_cosine_ops);
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_embedding_hnsw")
    op.drop_index("ix_rag_chunks_product", table_name="rag_chunks")
    op.drop_index("ix_rag_chunks_source", table_name="rag_chunks")
    op.drop_table("rag_chunks")
