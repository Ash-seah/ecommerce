"""Redis client ownership and cleanup."""

from redis.asyncio import Redis
from redis.asyncio.client import Pipeline

from src.core.config import Settings


class RedisClient:
    def __init__(self, settings: Settings) -> None:
        self.client: Redis = Redis.from_url(
            str(settings.redis_url), decode_responses=False, health_check_interval=30
        )

    async def get(self, key: str) -> bytes | str | None:
        return await self.client.get(key)

    async def set(
        self,
        key: str,
        value: bytes | str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> object:
        return await self.client.set(key, value, ex=ex, nx=nx)

    async def expire(self, key: str, ttl: int) -> object:
        return await self.client.expire(key, ttl)

    def pipeline(self) -> Pipeline:
        return self.client.pipeline()

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def rate_limit(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Atomically increment a fixed-window counter and return count and TTL."""
        result = await self.client.eval(
            """
            local count = redis.call('INCR', KEYS[1])
            if count == 1 then
                redis.call('EXPIRE', KEYS[1], ARGV[1])
            end
            local ttl = redis.call('TTL', KEYS[1])
            return {count, ttl}
            """,
            1,
            key,
            window_seconds,
        )
        count, ttl = result
        return int(count), max(1, int(ttl))

    async def close(self) -> None:
        await self.client.aclose()
