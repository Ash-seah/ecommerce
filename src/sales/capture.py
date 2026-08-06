"""Build sale events from checkout / order transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.catalog.schemas import CatalogSnapshot, ProductSnapshot, VariantSnapshot
from src.sales.allocate import allocate_proportionally
from src.sales.schemas import SaleCreate, SaleEvent
from src.sandbox.models import OrderRecord


def _sale_price(list_price_minor: int, discount_percent: int) -> int:
    if discount_percent <= 0:
        return list_price_minor
    return list_price_minor * (100 - discount_percent) // 100


def _category_meta(
    catalog: CatalogSnapshot, category_id: UUID
) -> tuple[str | None, str | None]:
    for category in catalog.categories:
        if category.id == category_id:
            return category.slug, category.name
    return None, None


def build_checkout_sales(
    *,
    order: OrderRecord,
    resolved: list[tuple[ProductSnapshot, VariantSnapshot, int]],
    catalog: CatalogSnapshot,
    sandbox_session_id: str | None,
) -> list[SaleEvent]:
    """One analytics fact per order line, with order money allocated by gross share."""

    now = order.created_at
    grosses = [
        _sale_price(variant.price_minor, product.discount_percent) * quantity
        for product, variant, quantity in resolved
    ]
    discounts = allocate_proportionally(grosses, order.discount_minor)
    shippings = allocate_proportionally(grosses, order.shipping_minor)
    taxes = allocate_proportionally(grosses, order.tax_minor)
    events: list[SaleEvent] = []
    for index, ((product, variant, quantity), gross, discount, shipping, tax) in enumerate(
        zip(resolved, grosses, discounts, shippings, taxes, strict=True)
    ):
        unit = _sale_price(variant.price_minor, product.discount_percent)
        category_slug, category_name = _category_meta(catalog, product.category_id)
        events.append(
            SaleEvent(
                id=uuid4(),
                occurred_at=now,
                recorded_at=now,
                source="checkout",
                status="recorded",
                order_id=order.id,
                line_index=index,
                product_id=product.id,
                product_slug=product.slug,
                product_name=product.name,
                category_id=product.category_id,
                category_slug=category_slug,
                category_name=category_name,
                variant_id=variant.id,
                variant_sku=variant.sku,
                variant_name=variant.name,
                currency=order.currency,
                quantity=quantity,
                list_unit_price_minor=variant.price_minor,
                unit_price_minor=unit,
                line_gross_minor=gross,
                allocated_discount_minor=discount,
                allocated_shipping_minor=shipping,
                allocated_tax_minor=tax,
                line_net_minor=gross - discount + shipping + tax,
                product_discount_percent=product.discount_percent,
                coupon_code=order.coupon_code,
                country_code=order.address.country_code,
                region=order.address.region,
                city=order.address.city,
                postal_code=order.address.postal_code,
                sandbox_session_id=sandbox_session_id,
            )
        )
    return events


def void_sales_for_order(
    sales: dict[UUID, SaleEvent],
    order_id: UUID,
    *,
    reason: str,
    at: datetime | None = None,
) -> dict[UUID, SaleEvent]:
    stamp = at or datetime.now(UTC)
    updated = dict(sales)
    for sale_id, sale in sales.items():
        if sale.order_id == order_id and sale.status == "recorded":
            updated[sale_id] = sale.model_copy(
                update={
                    "status": "voided",
                    "voided_at": stamp,
                    "void_reason": reason,
                }
            )
    return updated


def sale_from_create(
    body: SaleCreate,
    *,
    sale_id: UUID | None = None,
    sandbox_session_id: str | None = None,
) -> SaleEvent:
    now = datetime.now(UTC)
    occurred = body.occurred_at or now
    gross = body.unit_price_minor * body.quantity
    return SaleEvent(
        id=sale_id or uuid4(),
        occurred_at=occurred,
        recorded_at=now,
        source=body.source,
        status="recorded",
        order_id=body.order_id,
        line_index=body.line_index,
        product_id=body.product_id,
        product_slug=body.product_slug,
        product_name=body.product_name,
        category_id=body.category_id,
        category_slug=body.category_slug,
        category_name=body.category_name,
        variant_id=body.variant_id,
        variant_sku=body.variant_sku,
        variant_name=body.variant_name,
        currency=body.currency,
        quantity=body.quantity,
        list_unit_price_minor=body.list_unit_price_minor,
        unit_price_minor=body.unit_price_minor,
        line_gross_minor=gross,
        allocated_discount_minor=body.allocated_discount_minor,
        allocated_shipping_minor=body.allocated_shipping_minor,
        allocated_tax_minor=body.allocated_tax_minor,
        line_net_minor=max(
            0,
            gross
            - body.allocated_discount_minor
            + body.allocated_shipping_minor
            + body.allocated_tax_minor,
        ),
        product_discount_percent=body.product_discount_percent,
        coupon_code=body.coupon_code.upper() if body.coupon_code else None,
        country_code=body.country_code,
        region=body.region,
        city=body.city,
        postal_code=body.postal_code,
        sandbox_session_id=sandbox_session_id,
        notes=body.notes,
    )


def apply_sale_update(sale: SaleEvent, updates: dict[str, object]) -> SaleEvent:
    data = dict(updates)
    if data.get("status") == "voided" and sale.status != "voided":
        data.setdefault("voided_at", datetime.now(UTC))
    if data.get("status") == "recorded":
        data["voided_at"] = None
        data["void_reason"] = None
    quantity = int(data.get("quantity", sale.quantity))  # type: ignore[arg-type]
    unit = int(data.get("unit_price_minor", sale.unit_price_minor))  # type: ignore[arg-type]
    discount = int(data.get("allocated_discount_minor", sale.allocated_discount_minor))  # type: ignore[arg-type]
    shipping = int(data.get("allocated_shipping_minor", sale.allocated_shipping_minor))  # type: ignore[arg-type]
    tax = int(data.get("allocated_tax_minor", sale.allocated_tax_minor))  # type: ignore[arg-type]
    gross = unit * quantity
    data["line_gross_minor"] = gross
    data["line_net_minor"] = max(0, gross - discount + shipping + tax)
    if "coupon_code" in data and isinstance(data["coupon_code"], str):
        data["coupon_code"] = data["coupon_code"].upper()
    return sale.model_copy(update=data)
