"""Periodic precompute of bought-together and session-next Redis caches."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from uuid import UUID

from src.recommendations.algorithms import bought_together_map, session_next_map
from src.recommendations.store import RecommendationStore
from src.sales.schemas import SaleEvent
from src.views.schemas import ViewEvent

logger = logging.getLogger(__name__)

SalesLoader = Callable[[], Awaitable[list[SaleEvent]]]
ViewsLoader = Callable[[], Awaitable[list[ViewEvent]]]


def baskets_from_sales(sales: list[SaleEvent]) -> list[set[UUID]]:
    """Group recorded sale lines into order baskets (admin singles are skipped)."""

    by_order: dict[UUID, set[UUID]] = defaultdict(set)
    for sale in sales:
        if sale.status != "recorded" or sale.order_id is None:
            continue
        by_order[sale.order_id].add(sale.product_id)
    return [basket for basket in by_order.values() if len(basket) >= 2]


def sessions_from_views(views: list[ViewEvent]) -> dict[str, list[UUID]]:
    """Ordered product sequences keyed by sandbox session id."""

    timed: dict[str, list[tuple[object, UUID]]] = defaultdict(list)
    for view in views:
        if view.status != "recorded" or view.product_id is None:
            continue
        session_key = view.sandbox_session_id or str(view.id)
        timed[session_key].append((view.occurred_at, view.product_id))
    sessions: dict[str, list[UUID]] = {}
    for session_key, rows in timed.items():
        rows.sort(key=lambda item: item[0])
        sessions[session_key] = [product_id for _when, product_id in rows]
    return sessions


class RecommendationWorker:
    """Refresh Redis association caches from durable master ledgers."""

    def __init__(
        self,
        store: RecommendationStore,
        *,
        load_sales: SalesLoader,
        load_views: ViewsLoader,
        min_support: int = 2,
        association_limit: int = 12,
        interval_seconds: int = 300,
    ) -> None:
        self._store = store
        self._load_sales = load_sales
        self._load_views = load_views
        self._min_support = min_support
        self._association_limit = association_limit
        self._interval_seconds = interval_seconds

    async def run_once(self) -> dict[str, int]:
        sales = await self._load_sales()
        views = await self._load_views()
        baskets = baskets_from_sales(sales)
        bought = bought_together_map(
            baskets,
            min_support=self._min_support,
            limit=self._association_limit,
        )
        sessions = sessions_from_views(views)
        session_next = session_next_map(sessions, limit=self._association_limit)
        await self._store.replace_bought_together(bought)
        await self._store.replace_session_next(session_next)
        stats = {
            "baskets": len(baskets),
            "bought_together_products": len(bought),
            "sessions": len(sessions),
            "session_next_products": len(session_next),
        }
        logger.info("recommendations precompute complete: %s", stats)
        return stats

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("recommendations precompute failed")
            await asyncio.sleep(self._interval_seconds)
