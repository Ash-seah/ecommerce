"""Build and mutate view events."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.catalog.schemas import CatalogSnapshot, CategorySnapshot, ProductSnapshot
from src.views.schemas import ViewCreate, ViewEvent, ViewKind, ViewRecordRequest


def view_from_create(
    body: ViewCreate,
    *,
    view_id: UUID | None = None,
    sandbox_session_id: str | None = None,
    source_override: str | None = None,
) -> ViewEvent:
    now = datetime.now(UTC)
    occurred = body.occurred_at or now
    return ViewEvent(
        id=view_id or uuid4(),
        occurred_at=occurred,
        recorded_at=now,
        source=source_override or body.source,  # type: ignore[arg-type]
        status="recorded",
        kind=body.kind,
        path=body.path,
        referrer=body.referrer,
        query=body.query,
        product_id=body.product_id,
        product_slug=body.product_slug,
        product_name=body.product_name,
        category_id=body.category_id,
        category_slug=body.category_slug,
        category_name=body.category_name,
        country_code=body.country_code,
        region=body.region,
        city=body.city,
        user_agent=body.user_agent,
        sandbox_session_id=sandbox_session_id,
        notes=body.notes,
    )


def apply_view_update(event: ViewEvent, updates: dict[str, object]) -> ViewEvent:
    data = dict(updates)
    if data.get("status") == "voided" and event.status != "voided":
        data.setdefault("voided_at", datetime.now(UTC))
    if data.get("status") == "recorded":
        data["voided_at"] = None
        data["void_reason"] = None
    return event.model_copy(update=data)


def enrich_record_request(
    body: ViewRecordRequest,
    catalog: CatalogSnapshot,
    *,
    sandbox_session_id: str,
    user_agent: str | None,
) -> ViewEvent:
    product: ProductSnapshot | None = None
    category: CategorySnapshot | None = None
    if body.product_id is not None:
        product = next((item for item in catalog.products if item.id == body.product_id), None)
    if body.category_id is not None:
        category = next(
            (item for item in catalog.categories if item.id == body.category_id), None
        )
    elif product is not None:
        category = next(
            (item for item in catalog.categories if item.id == product.category_id), None
        )
    create = ViewCreate(
        source="client",
        kind=body.kind,
        path=body.path,
        referrer=body.referrer,
        query=body.query,
        product_id=None if product is None else product.id,
        product_slug=None if product is None else product.slug,
        product_name=None if product is None else product.name,
        category_id=(
            body.category_id
            if body.category_id is not None
            else (None if category is None else category.id)
        ),
        category_slug=None if category is None else category.slug,
        category_name=None if category is None else category.name,
        country_code=body.country_code,
        region=body.region,
        city=body.city,
        user_agent=user_agent,
    )
    return view_from_create(
        create, sandbox_session_id=sandbox_session_id, source_override="client"
    )


def auto_product_view(
    product: ProductSnapshot,
    catalog: CatalogSnapshot,
    *,
    sandbox_session_id: str,
) -> ViewEvent:
    category = next(
        (item for item in catalog.categories if item.id == product.category_id), None
    )
    return view_from_create(
        ViewCreate(
            source="admin",
            kind="product_view",
            path=f"/products/{product.slug}",
            product_id=product.id,
            product_slug=product.slug,
            product_name=product.name,
            category_id=product.category_id,
            category_slug=None if category is None else category.slug,
            category_name=None if category is None else category.name,
        ),
        sandbox_session_id=sandbox_session_id,
        source_override="auto",
    )


def auto_category_view(
    category: CategorySnapshot, *, sandbox_session_id: str
) -> ViewEvent:
    return view_from_create(
        ViewCreate(
            source="admin",
            kind="category_view",
            path=f"/categories/{category.slug}",
            category_id=category.id,
            category_slug=category.slug,
            category_name=category.name,
        ),
        sandbox_session_id=sandbox_session_id,
        source_override="auto",
    )


def auto_listing_view(
    *,
    sandbox_session_id: str,
    path: str = "/products",
    query: str | None = None,
    category_id: UUID | None = None,
    category_slug: str | None = None,
    category_name: str | None = None,
) -> ViewEvent:
    kind: ViewKind = "search" if query else "listing_view"
    return view_from_create(
        ViewCreate(
            source="admin",
            kind=kind,
            path=path,
            query=query,
            category_id=category_id,
            category_slug=category_slug,
            category_name=category_name,
        ),
        sandbox_session_id=sandbox_session_id,
        source_override="auto",
    )
