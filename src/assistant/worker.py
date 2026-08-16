"""Periodic Groq embedding refresh for RAG chunks."""

from __future__ import annotations

import asyncio
import logging

from src.assistant.service import AssistantService

logger = logging.getLogger(__name__)


class AssistantIndexWorker:
    def __init__(self, service: AssistantService, *, interval_seconds: int) -> None:
        self._service = service
        self._interval_seconds = interval_seconds

    async def run_once(self) -> dict[str, int]:
        stats = await self._service.reindex()
        logger.info("assistant reindex complete: %s", stats)
        return stats

    async def run_forever(self) -> None:
        while True:
            try:
                if self._service.configured():
                    await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("assistant reindex failed")
            await asyncio.sleep(self._interval_seconds)
