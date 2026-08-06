"""Database infrastructure: reader for runtime reads, owner for master writes."""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.config import Settings


class ReaderDatabase:
    """Own the runtime engine, which is always created from reader credentials."""

    def __init__(self, settings: Settings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            str(settings.database_url), pool_pre_ping=True
        )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def ping(self) -> bool:
        async with self.engine.connect() as connection:
            result: int | None = await connection.scalar(text("SELECT 1"))
        return result == 1

    async def close(self) -> None:
        await self.engine.dispose()


class OwnerDatabase:
    """Owner-role engine used only by master-catalog admin mutations."""

    def __init__(self, settings: Settings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            str(settings.migration_database_url), pool_pre_ping=True
        )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def close(self) -> None:
        await self.engine.dispose()
