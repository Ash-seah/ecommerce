"""Rebuild assistant RAG chunks from the active master catalog and ledgers.

Uses Groq for chat readiness checks and optional remote embeddings when
EMBEDDING_API_KEY is set. Without an embedder, chunks are indexed for text search.

  python -m scripts.reindex_assistant
"""

from __future__ import annotations

import asyncio

from src.assistant.groq import EmbeddingClient, GroqClient
from src.assistant.service import AssistantService
from src.assistant.store import RagChunkRepository
from src.catalog.repository import MasterCatalogRepository
from src.core.config import get_settings
from src.infrastructure.database import OwnerDatabase, ReaderDatabase
from src.reviews.repository import MasterReviewsRepository
from src.sales.repository import MasterSalesRepository
from src.views.repository import MasterViewsRepository


async def main() -> None:
    settings = get_settings()
    if settings.groq_api_key is None:
        raise SystemExit("GROQ_API_KEY is not set")
    reader = ReaderDatabase(settings)
    owner = OwnerDatabase(settings)
    groq = GroqClient(
        api_key=settings.groq_api_key.get_secret_value(),
        base_url=settings.groq_base_url,
        chat_model=settings.groq_chat_model,
    )
    embedder = None
    if settings.embedding_api_key is not None:
        embedder = EmbeddingClient(
            api_key=settings.embedding_api_key.get_secret_value(),
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
        )
    service = AssistantService(
        groq=groq,
        embedder=embedder,
        chunks=RagChunkRepository(owner.session_factory),
        catalog=MasterCatalogRepository(reader.session_factory),
        sales=MasterSalesRepository(owner.session_factory),
        views=MasterViewsRepository(owner.session_factory),
        reviews=MasterReviewsRepository(owner.session_factory),
        top_k=settings.assistant_top_k,
        enabled=True,
    )
    try:
        stats = await service.reindex()
        print(
            f"reindexed mode={service.retrieval_mode()} "
            f"documents={stats['documents']} "
            f"written={stats['written']} skipped={stats['skipped']}"
        )
    finally:
        await groq.close()
        if embedder is not None:
            await embedder.close()
        await reader.close()
        await owner.close()


if __name__ == "__main__":
    asyncio.run(main())
