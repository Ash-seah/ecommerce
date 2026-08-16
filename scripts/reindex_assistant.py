"""Rebuild Groq RAG chunks from the active master catalog and ledgers.

  python -m scripts.reindex_assistant
"""

from __future__ import annotations

import asyncio

from src.assistant.groq import GroqClient
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
        embed_model=settings.groq_embed_model,
    )
    service = AssistantService(
        groq=groq,
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
            f"reindexed documents={stats['documents']} "
            f"written={stats['written']} skipped={stats['skipped']}"
        )
    finally:
        await groq.close()
        await reader.close()
        await owner.close()


if __name__ == "__main__":
    asyncio.run(main())
