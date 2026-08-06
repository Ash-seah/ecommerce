"""Allocate order-level money across line items without losing cents."""

from __future__ import annotations


def allocate_proportionally(weights: list[int], total: int) -> list[int]:
    """Largest-remainder allocation so shares sum exactly to ``total``."""

    if total < 0:
        raise ValueError("total must be non-negative")
    if not weights:
        return []
    weight_sum = sum(weights)
    if weight_sum <= 0 or total == 0:
        return [0] * len(weights)
    raw = [total * weight / weight_sum for weight in weights]
    floors = [int(value) for value in raw]
    remainder = total - sum(floors)
    ranked = sorted(
        range(len(weights)),
        key=lambda index: (raw[index] - floors[index], weights[index], -index),
        reverse=True,
    )
    for index in ranked[:remainder]:
        floors[index] += 1
    return floors
