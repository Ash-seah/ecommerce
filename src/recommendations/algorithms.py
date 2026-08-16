"""Pure, dependency-free association and transition ranking.

FP-Growth-style frequent pairs are approximated with co-occurrence counts so the
API stays free of numpy/pandas/mlxtend while still producing bought-together
candidates suitable for Redis O(1) lookups.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations
from uuid import UUID


def bought_together_map(
    baskets: Sequence[set[UUID]],
    *,
    min_support: int = 2,
    limit: int = 12,
) -> dict[UUID, list[UUID]]:
    """Rank co-purchased partners per product from order baskets.

    Support is the number of baskets containing both items. Confidence is
    support / count(antecedent). Partners are ordered by (support, confidence).
    """

    item_counts: dict[UUID, int] = defaultdict(int)
    pair_counts: dict[tuple[UUID, UUID], int] = defaultdict(int)

    for basket in baskets:
        if len(basket) < 2:
            continue
        unique = sorted(basket, key=str)
        for item in unique:
            item_counts[item] += 1
        for left, right in combinations(unique, 2):
            pair_counts[(left, right)] += 1

    partners: dict[UUID, list[tuple[int, float, UUID]]] = defaultdict(list)
    for (left, right), support in pair_counts.items():
        if support < min_support:
            continue
        left_conf = support / item_counts[left]
        right_conf = support / item_counts[right]
        partners[left].append((support, left_conf, right))
        partners[right].append((support, right_conf, left))

    result: dict[UUID, list[UUID]] = {}
    for product_id, ranked in partners.items():
        ranked.sort(key=lambda row: (-row[0], -row[1], str(row[2])))
        seen: set[UUID] = set()
        ordered: list[UUID] = []
        for _support, _confidence, partner in ranked:
            if partner in seen or partner == product_id:
                continue
            seen.add(partner)
            ordered.append(partner)
            if len(ordered) >= limit:
                break
        if ordered:
            result[product_id] = ordered
    return result


def session_next_map(
    sessions: Mapping[str, Sequence[UUID]],
    *,
    limit: int = 12,
) -> dict[UUID, list[UUID]]:
    """Build item→item transition weights from ordered per-session product views.

    Any later product in the same session increments the edge from an earlier
    product (order-aware collaborative signal without a heavy CF model).
    """

    edges: dict[UUID, dict[UUID, int]] = defaultdict(lambda: defaultdict(int))
    for sequence in sessions.values():
        seen_order: list[UUID] = []
        for product_id in sequence:
            for prior in seen_order:
                if prior != product_id:
                    edges[prior][product_id] += 1
            if not seen_order or seen_order[-1] != product_id:
                seen_order.append(product_id)

    result: dict[UUID, list[UUID]] = {}
    for source, targets in edges.items():
        ranked = sorted(targets.items(), key=lambda item: (-item[1], str(item[0])))
        ordered = [product_id for product_id, _weight in ranked[:limit] if product_id != source]
        if ordered:
            result[source] = ordered
    return result


def aggregate_ranked_ids(
    seed_lists: Iterable[Sequence[UUID]],
    *,
    exclude: set[UUID] | None = None,
    limit: int = 20,
) -> list[UUID]:
    """Merge precomputed neighbor lists with simple frequency ranking."""

    blocked = exclude or set()
    scores: dict[UUID, int] = defaultdict(int)
    for neighbors in seed_lists:
        for index, product_id in enumerate(neighbors):
            if product_id in blocked:
                continue
            # Prefer earlier (higher-ranked) neighbors.
            scores[product_id] += max(1, len(neighbors) - index)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], str(item[0])))
    return [product_id for product_id, _score in ranked[:limit]]
