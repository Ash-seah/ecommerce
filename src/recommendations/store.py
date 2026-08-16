"""Redis-backed recommendation caches (intent ZSET + association lists)."""

from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID


class RecommendationsRedis(Protocol):
    async def zincrby(self, key: str, amount: float, member: str) -> float: ...

    async def zrevrange(
        self, key: str, start: int, end: int, *, withscores: bool = False
    ) -> list[bytes | str] | list[tuple[bytes | str, float]]: ...

    async def zscore(self, key: str, member: str) -> float | None: ...

    async def get(self, key: str) -> bytes | str | None: ...

    async def set(
        self,
        key: str,
        value: bytes | str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> object: ...

    async def delete(self, *keys: str) -> int: ...


def _decode(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return value


class RecommendationStore:
    """Namespaced Redis keys under ``{prefix}:recs:...``."""

    def __init__(self, redis: RecommendationsRedis, *, key_prefix: str) -> None:
        self._redis = redis
        self._intent_key = f"{key_prefix}:recs:intent"
        self._bought_prefix = f"{key_prefix}:recs:bought_together"
        self._session_next_prefix = f"{key_prefix}:recs:session_next"

    def bought_together_key(self, product_id: UUID) -> str:
        return f"{self._bought_prefix}:{product_id}"

    def session_next_key(self, product_id: UUID) -> str:
        return f"{self._session_next_prefix}:{product_id}"

    async def increment_intent(self, product_id: UUID, amount: int) -> float:
        if amount == 0:
            score = await self._redis.zscore(self._intent_key, str(product_id))
            return float(score or 0.0)
        return float(await self._redis.zincrby(self._intent_key, float(amount), str(product_id)))

    async def intent_score(self, product_id: UUID) -> float:
        score = await self._redis.zscore(self._intent_key, str(product_id))
        return float(score or 0.0)

    async def intent_scores(self, product_ids: list[UUID]) -> dict[UUID, float]:
        scores: dict[UUID, float] = {}
        for product_id in product_ids:
            scores[product_id] = await self.intent_score(product_id)
        return scores

    async def top_intent_product_ids(self, *, limit: int = 50) -> list[UUID]:
        rows = await self._redis.zrevrange(self._intent_key, 0, max(0, limit - 1))
        result: list[UUID] = []
        for member in rows:
            try:
                result.append(UUID(_decode(member)))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        return result

    async def get_bought_together(self, product_id: UUID) -> list[UUID]:
        return await self._read_id_list(self.bought_together_key(product_id))

    async def get_session_next(self, product_id: UUID) -> list[UUID]:
        return await self._read_id_list(self.session_next_key(product_id))

    async def replace_bought_together(self, mapping: dict[UUID, list[UUID]]) -> None:
        await self._replace_map(self._bought_prefix, mapping)

    async def replace_session_next(self, mapping: dict[UUID, list[UUID]]) -> None:
        await self._replace_map(self._session_next_prefix, mapping)

    async def _replace_map(self, prefix: str, mapping: dict[UUID, list[UUID]]) -> None:
        # Best-effort overwrite of known keys; stale products keep prior lists until GC.
        for product_id, neighbors in mapping.items():
            payload = json.dumps([str(item) for item in neighbors])
            await self._redis.set(f"{prefix}:{product_id}", payload)

    async def _read_id_list(self, key: str) -> list[UUID]:
        raw = await self._redis.get(key)
        if raw is None:
            return []
        try:
            payload = json.loads(_decode(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        result: list[UUID] = []
        for item in payload:
            try:
                result.append(UUID(str(item)))
            except (TypeError, ValueError):
                continue
        return result
