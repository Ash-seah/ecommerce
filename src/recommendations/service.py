"""Recommendation domain service: intent scoring + ranked product ID lists."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import UUID

from src.catalog.schemas import CatalogSnapshot, ProductSnapshot
from src.recommendations.algorithms import aggregate_ranked_ids
from src.recommendations.scoring import IntentAction, intent_weight
from src.recommendations.store import RecommendationStore
from src.sandbox.models import SandboxState


class RecommendationService:
    def __init__(
        self,
        store: RecommendationStore,
        *,
        association_limit: int = 12,
        personal_seed_products: int = 3,
    ) -> None:
        self._store = store
        self._association_limit = association_limit
        self._personal_seed_products = personal_seed_products
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule_intent(self, product_id: UUID, action: IntentAction) -> None:
        """Fire-and-forget intent increment (does not block the request path)."""

        task = asyncio.create_task(self._safe_record_intent(product_id, action))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def record_intent(self, product_id: UUID, action: IntentAction) -> float:
        return await self._store.increment_intent(product_id, intent_weight(action))

    async def _safe_record_intent(self, product_id: UUID, action: IntentAction) -> None:
        with suppress(Exception):
            await self.record_intent(product_id, action)

    async def similar_product_ids(
        self,
        catalog: CatalogSnapshot,
        product_id: UUID,
        *,
        limit: int,
    ) -> list[UUID]:
        target = _product_by_id(catalog, product_id)
        if target is None:
            return []
        candidates = [
            product
            for product in catalog.products
            if product.id != product_id
            and product.category_id == target.category_id
            and product.variants
        ]
        scores = await self._store.intent_scores([product.id for product in candidates])
        ranked = sorted(
            candidates,
            key=lambda product: (
                -scores.get(product.id, 0.0),
                product.name.casefold(),
                str(product.id),
            ),
        )
        return [product.id for product in ranked[:limit]]

    async def cross_sell_product_ids(
        self,
        catalog: CatalogSnapshot,
        product_id: UUID,
        *,
        limit: int,
    ) -> list[UUID]:
        catalog_ids = {product.id for product in catalog.products if product.variants}
        neighbors = [
            item
            for item in await self._store.get_bought_together(product_id)
            if item in catalog_ids and item != product_id
        ]
        if neighbors:
            return neighbors[:limit]
        return await self.similar_product_ids(catalog, product_id, limit=limit)

    async def personal_product_ids(
        self,
        state: SandboxState,
        catalog: CatalogSnapshot,
        *,
        limit: int,
    ) -> list[UUID] | None:
        """Return personalized IDs, or ``None`` to signal cold-start fallback."""

        recent = _recent_product_ids(state, limit=self._personal_seed_products)
        if not recent:
            return None
        catalog_ids = {product.id for product in catalog.products if product.variants}
        seed_lists: list[list[UUID]] = []
        for product_id in recent:
            neighbors = [
                item
                for item in await self._store.get_session_next(product_id)
                if item in catalog_ids
            ]
            if neighbors:
                seed_lists.append(neighbors)
        if not seed_lists:
            return None
        ranked = aggregate_ranked_ids(
            seed_lists,
            exclude=set(recent),
            limit=limit,
        )
        return ranked or None

    async def intent_scores_for(self, product_ids: list[UUID]) -> dict[UUID, float]:
        return await self._store.intent_scores(product_ids)


def _product_by_id(catalog: CatalogSnapshot, product_id: UUID) -> ProductSnapshot | None:
    for product in catalog.products:
        if product.id == product_id:
            return product
    return None


def _recent_product_ids(state: SandboxState, *, limit: int) -> list[UUID]:
    events = [
        event
        for event in state.views.values()
        if event.status == "recorded" and event.product_id is not None
    ]
    events.sort(key=lambda event: event.occurred_at, reverse=True)
    ordered: list[UUID] = []
    seen: set[UUID] = set()
    for event in events:
        product_id = event.product_id
        if product_id is None or product_id in seen:
            continue
        seen.add(product_id)
        ordered.append(product_id)
        if len(ordered) >= limit:
            break
    return ordered
