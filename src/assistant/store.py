"""Persist and retrieve RAG chunks (pgvector when available, cosine fallback)."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.assistant.chunker import RagDocument
from src.assistant.models import RagChunkRow


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    title: str
    content: str
    source: str
    ref_id: str
    score: float


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = sqrt(sum(a * a for a in left))
    norm_right = sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


class RagChunkRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._vector_ready: bool | None = None

    async def count(self) -> int:
        async with self._sessions() as session:
            total = await session.scalar(select(func.count()).select_from(RagChunkRow))
        return int(total or 0)

    async def vector_available(self) -> bool:
        if self._vector_ready is not None:
            return self._vector_ready
        async with self._sessions() as session:
            exists = await session.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'rag_chunks' AND column_name = 'embedding_vec'
                    )
                    """
                )
            )
        self._vector_ready = bool(exists)
        return self._vector_ready

    async def upsert(self, document: RagDocument, embedding: list[float]) -> None:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(RagChunkRow).where(
                    RagChunkRow.source == document.source,
                    RagChunkRow.ref_id == document.ref_id,
                )
            )
            if row is None:
                row = RagChunkRow(
                    id=uuid4(),
                    source=document.source,
                    ref_id=document.ref_id,
                    title=document.title,
                    content=document.content,
                    content_sha256=document.content_sha256,
                    embedding=embedding,
                    product_id=document.product_id,
                )
                session.add(row)
            else:
                row.title = document.title
                row.content = document.content
                row.content_sha256 = document.content_sha256
                row.embedding = embedding
                row.product_id = document.product_id
            await session.flush()
        if await self.vector_available():
            literal = "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"
            try:
                async with self._sessions.begin() as session:
                    await session.execute(
                        text(
                            """
                            UPDATE rag_chunks
                            SET embedding_vec = CAST(:vec AS vector)
                            WHERE source = :source AND ref_id = :ref_id
                            """
                        ),
                        {
                            "vec": literal,
                            "source": document.source,
                            "ref_id": document.ref_id,
                        },
                    )
            except Exception:
                self._vector_ready = False

    async def existing_hashes(self) -> dict[tuple[str, str], str]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(RagChunkRow.source, RagChunkRow.ref_id, RagChunkRow.content_sha256)
                )
            ).all()
        return {(str(source), str(ref_id)): str(digest) for source, ref_id, digest in rows}

    async def search(
        self, query_embedding: list[float], *, limit: int
    ) -> list[RetrievedChunk]:
        if await self.vector_available():
            return await self._search_vector(query_embedding, limit=limit)
        return await self._search_python(query_embedding, limit=limit)

    async def _search_vector(
        self, query_embedding: list[float], *, limit: int
    ) -> list[RetrievedChunk]:
        literal = "[" + ",".join(f"{value:.8f}" for value in query_embedding) + "]"
        async with self._sessions() as session:
            result = await session.execute(
                text(
                    """
                    SELECT title, content, source, ref_id,
                           1 - (embedding_vec <=> CAST(:vec AS vector)) AS score
                    FROM rag_chunks
                    WHERE embedding_vec IS NOT NULL
                    ORDER BY embedding_vec <=> CAST(:vec AS vector)
                    LIMIT :limit
                    """
                ),
                {"vec": literal, "limit": limit},
            )
            rows = result.all()
        return [
            RetrievedChunk(
                title=str(row.title),
                content=str(row.content),
                source=str(row.source),
                ref_id=str(row.ref_id),
                score=float(row.score or 0),
            )
            for row in rows
        ]

    async def _search_python(
        self, query_embedding: list[float], *, limit: int
    ) -> list[RetrievedChunk]:
        async with self._sessions() as session:
            rows = (await session.scalars(select(RagChunkRow))).all()
        ranked = sorted(
            (
                RetrievedChunk(
                    title=row.title,
                    content=row.content,
                    source=row.source,
                    ref_id=row.ref_id,
                    score=cosine_similarity(query_embedding, list(row.embedding or [])),
                )
                for row in rows
            ),
            key=lambda item: item.score,
            reverse=True,
        )
        return ranked[:limit]
