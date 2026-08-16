"""Buying-intent weights for real-time product scoring."""

from typing import Literal

IntentAction = Literal[
    "visit",
    "product_view",
    "category_view",
    "listing_view",
    "search",
    "cart_add",
    "wishlist_add",
    "purchase",
]

# Global product intent deltas. Higher values mean stronger purchase signal.
INTENT_WEIGHTS: dict[IntentAction, int] = {
    "visit": 1,
    "product_view": 1,
    "category_view": 1,
    "listing_view": 1,
    "search": 1,
    "cart_add": 5,
    "wishlist_add": 10,
    "purchase": 25,
}


def intent_weight(action: IntentAction) -> int:
    """Return the configured weight for an intent action."""

    return INTENT_WEIGHTS[action]
