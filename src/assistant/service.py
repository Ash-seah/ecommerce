"""Index documents and answer with Groq over retrieved chunks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from src.assistant.chunker import (
    RagDocument,
    documents_from_analytics,
    documents_from_catalog,
    documents_from_reviews,
)
from src.assistant.groq import EmbeddingClient, GroqClient
from src.assistant.store import RagChunkRepository, RetrievedChunk
from src.catalog.repository import CatalogNotAvailableError, MasterCatalogRepository
from src.reviews.repository import MasterReviewsRepository
from src.sales.repository import MasterSalesRepository
from src.views.repository import MasterViewsRepository

SYSTEM_PROMPT = """You are the ecommerce sandbox assistant.
Answer only from the retrieved context. If the context is missing the answer, say you do not know.
Use product names and slugs. Never invent prices, stock, or order IDs.
Do not mention internal IDs, session cookies, API keys, or wallet balances.
Keep answers concise."""


class AssistantError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class AssistantService:
    def __init__(
        self,
        *,
        groq: GroqClient | None,
        embedder: EmbeddingClient | None,
        chunks: RagChunkRepository,
        catalog: MasterCatalogRepository,
        sales: MasterSalesRepository,
        views: MasterViewsRepository,
        reviews: MasterReviewsRepository,
        top_k: int,
        enabled: bool,
    ) -> None:
        self._groq = groq
        self._embedder = embedder
        self._chunks = chunks
        self._catalog = catalog
        self._sales = sales
        self._views = views
        self._reviews = reviews
        self._top_k = top_k
        self._enabled = enabled

    def configured(self) -> bool:
        return self._enabled and self._groq is not None

    def retrieval_mode(self) -> str:
        return "vector" if self._embedder is not None else "text"

    async def health(self) -> dict[str, object]:
        return {
            "enabled": self._enabled,
            "groq_configured": self._groq is not None,
            "embeddings_configured": self._embedder is not None,
            "retrieval_mode": self.retrieval_mode(),
            "indexed_chunks": await self._chunks.count(),
            "pgvector": await self._chunks.vector_available(),
        }

    async def reindex(self) -> dict[str, int]:
        self._require_groq()
        try:
            catalog = await self._catalog.get_active_snapshot()
        except CatalogNotAvailableError as exc:
            raise AssistantError(503, "catalog_unavailable", str(exc)) from exc
        reviews = await self._reviews.list_all()
        sales = await self._sales.list_all()
        views = await self._views.list_all()
        documents = [
            *documents_from_catalog(catalog),
            *documents_from_reviews(reviews),
            *documents_from_analytics(sales, views),
        ]
        existing = await self._chunks.existing_hashes()
        written = 0
        skipped = 0
        batch: list[RagDocument] = []
        for document in documents:
            digest = existing.get((document.source, document.ref_id))
            if digest == document.content_sha256:
                skipped += 1
                continue
            batch.append(document)
            if len(batch) >= 16:
                written += await self._write_batch(batch)
                batch = []
        if batch:
            written += await self._write_batch(batch)
        return {"documents": len(documents), "written": written, "skipped": skipped}

    async def _write_batch(self, batch: list[RagDocument]) -> int:
        vectors: list[list[float] | None]
        if self._embedder is not None:
            vectors = list(await self._embedder.embed([item.content for item in batch]))
        else:
            vectors = [None] * len(batch)
        for document, embedding in zip(batch, vectors, strict=True):
            await self._chunks.upsert(document, embedding)
        return len(batch)

    async def stream_answer(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
        product_id: UUID | None = None,
    ) -> AsyncIterator[tuple[str, object]]:
        groq = self._require_groq()
        query = question.strip()
        if not query:
            raise AssistantError(422, "empty_question", "Question is required")
        retrieved = await self._retrieve(query)
        if product_id is not None:
            retrieved = _prefer_product(retrieved, product_id)
        context = _format_context(retrieved)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": "Retrieved context:\n" + (context or "(no indexed chunks yet)"),
            },
            *history[-8:],
            {"role": "user", "content": query},
        ]
        yield (
            "meta",
            {
                "retrieval_mode": self.retrieval_mode(),
                "sources": [
                    {"title": item.title, "source": item.source, "score": round(item.score, 4)}
                    for item in retrieved
                ],
            },
        )
        async for delta in groq.stream_chat(messages):
            yield ("delta", delta)

    async def _retrieve(self, query: str) -> list[RetrievedChunk]:
        if self._embedder is not None:
            [query_vec] = await self._embedder.embed([query])
            hits = await self._chunks.search_vector(query_vec, limit=self._top_k)
            if hits:
                return hits
        return await self._chunks.search_text(query, limit=self._top_k)

    def _require_groq(self) -> GroqClient:
        if not self._enabled:
            raise AssistantError(503, "assistant_disabled", "Assistant is disabled")
        if self._groq is None:
            raise AssistantError(503, "assistant_unconfigured", "GROQ_API_KEY is not set")
        return self._groq


def _prefer_product(chunks: list[RetrievedChunk], product_id: UUID) -> list[RetrievedChunk]:
    needle = str(product_id)
    preferred = [item for item in chunks if item.ref_id == needle or needle in item.content]
    if not preferred:
        return chunks
    rest = [item for item in chunks if item not in preferred]
    return preferred + rest


def _format_context(chunks: list[RetrievedChunk]) -> str:
    blocks = []
    for index, chunk in enumerate(chunks, start=1):
        blocks.append(f"[{index}] {chunk.title} ({chunk.source})\n{chunk.content}")
    return "\n\n".join(blocks)
