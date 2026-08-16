"""ORM for RAG chunks. Embeddings live in float[] plus optional pgvector column."""

from __future__ import annotations

import uuid

from sqlalchemy import Float, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.catalog.models import Base, TimestampMixin


class RagChunkRow(Base, TimestampMixin):
    __tablename__ = "rag_chunks"
    __table_args__ = (
        UniqueConstraint("source", "ref_id", name="uq_rag_chunks_source_ref"),
        Index("ix_rag_chunks_source", "source"),
        Index("ix_rag_chunks_product", "product_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
