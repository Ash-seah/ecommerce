"""Server-assigned public identifiers for catalog entities."""

from uuid import uuid4

# 12 hex chars: URL-safe, fits slug columns, collision risk is negligible at catalog scale.
_SHORT_UUID_LENGTH = 12


def short_uuid() -> str:
    """Return a lowercase hex short id for use as a category/product slug."""
    return uuid4().hex[:_SHORT_UUID_LENGTH]
