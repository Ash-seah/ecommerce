"""Build nested category trees from the flat catalog snapshot."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from uuid import UUID

from src.catalog.schemas import CategorySnapshot
from src.commerce.schemas import CategoryNode


def _sort_key(category: CategorySnapshot) -> tuple[int, str, str]:
    return (category.sort_order, category.name.casefold(), str(category.id))


def build_category_forest(categories: Sequence[CategorySnapshot]) -> tuple[CategoryNode, ...]:
    """Return root categories with nested `children` (full subtrees).

    Categories whose parent is missing from the catalog are treated as roots.
    Cycles are truncated so a node never nests under itself.
    """
    by_id = {category.id: category for category in categories}
    children_of: dict[UUID | None, list[CategorySnapshot]] = defaultdict(list)
    for category in categories:
        parent_id = category.parent_id
        if parent_id is not None and parent_id not in by_id:
            parent_id = None
        children_of[parent_id].append(category)

    for siblings in children_of.values():
        siblings.sort(key=_sort_key)

    seen: set[UUID] = set()

    def build(category: CategorySnapshot, ancestry: frozenset[UUID]) -> CategoryNode:
        seen.add(category.id)
        nested: list[CategoryNode] = []
        if category.id not in ancestry:
            next_ancestry = ancestry | {category.id}
            for child in children_of.get(category.id, ()):
                nested.append(build(child, next_ancestry))
        return CategoryNode(
            id=category.id,
            parent_id=category.parent_id,
            slug=category.slug,
            name=category.name,
            description=category.description,
            sort_order=category.sort_order,
            children=tuple(nested),
        )

    roots = tuple(build(category, frozenset()) for category in children_of.get(None, ()))
    # Attach any cycle-only leftovers so they remain visible in the API.
    leftovers = tuple(
        build(category, frozenset())
        for category in sorted(
            (category for category in categories if category.id not in seen),
            key=_sort_key,
        )
    )
    return roots + leftovers


def category_subtree(
    categories: Sequence[CategorySnapshot], identifier: str
) -> CategoryNode | None:
    """Return one category and its nested descendants, or None if not found."""
    match = next(
        (
            category
            for category in categories
            if str(category.id) == identifier or category.slug == identifier
        ),
        None,
    )
    if match is None:
        return None

    by_id = {category.id: category for category in categories}
    children_of: dict[UUID, list[CategorySnapshot]] = defaultdict(list)
    for category in categories:
        if category.parent_id is not None and category.parent_id in by_id:
            children_of[category.parent_id].append(category)
    for siblings in children_of.values():
        siblings.sort(key=_sort_key)

    def build(category: CategorySnapshot, ancestry: frozenset[UUID]) -> CategoryNode:
        nested: Iterable[CategoryNode] = ()
        if category.id not in ancestry:
            next_ancestry = ancestry | {category.id}
            nested = (build(child, next_ancestry) for child in children_of.get(category.id, ()))
        return CategoryNode(
            id=category.id,
            parent_id=category.parent_id,
            slug=category.slug,
            name=category.name,
            description=category.description,
            sort_order=category.sort_order,
            children=tuple(nested),
        )

    return build(match, frozenset())
