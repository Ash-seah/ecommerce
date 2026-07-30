"""Commerce domain services over the atomic sandbox document."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from src.catalog.schemas import (
    CatalogSnapshot,
    CategorySnapshot,
    ProductSnapshot,
    VariantSnapshot,
)
from src.commerce.schemas import (
    CartLineView,
    CartView,
    CategoryPage,
    LedgerPage,
    PricingBreakdown,
    ProductPage,
    ProductView,
    VariantView,
)
from src.sandbox.merge import merge_catalog
from src.sandbox.models import (
    AddressRecord,
    CartLine,
    CouponRecord,
    InventoryLedgerEntry,
    OrderLineSnapshot,
    OrderRecord,
    SandboxState,
    WalletLedgerEntry,
)
from src.sandbox.service import SandboxService


class CommerceError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class CouponPolicy(Protocol):
    def discount(self, code: str | None, subtotal_minor: int) -> int: ...


class ShippingPolicy(Protocol):
    def cost(self, subtotal_after_discount_minor: int) -> int: ...


class TaxPolicy(Protocol):
    def tax(self, taxable_minor: int) -> int: ...


@dataclass(frozen=True, slots=True)
class DemoCouponPolicy:
    """A deterministic demo coupon abstraction: SAVE10 discounts ten percent."""

    def discount(self, code: str | None, subtotal_minor: int) -> int:
        if code is None:
            return 0
        if code.upper() != "SAVE10":
            raise CommerceError(422, "invalid_coupon", "Coupon code is not valid")
        return subtotal_minor // 10


@dataclass(frozen=True, slots=True)
class FlatShippingPolicy:
    flat_minor: int
    free_threshold_minor: int

    def cost(self, subtotal_after_discount_minor: int) -> int:
        if subtotal_after_discount_minor >= self.free_threshold_minor:
            return 0
        return self.flat_minor


@dataclass(frozen=True, slots=True)
class BasisPointTaxPolicy:
    basis_points: int

    def tax(self, taxable_minor: int) -> int:
        return taxable_minor * self.basis_points // 10_000


@dataclass(frozen=True, slots=True)
class PricingService:
    coupon: CouponPolicy
    shipping: ShippingPolicy
    tax_policy: TaxPolicy

    def calculate(
        self,
        *,
        currency: str,
        subtotal_minor: int,
        coupon_code: str | None,
        coupons: dict[str, CouponRecord] | None = None,
    ) -> PricingBreakdown:
        custom = None if coupon_code is None else (coupons or {}).get(coupon_code.upper())
        if custom is not None:
            if not custom.active:
                raise CommerceError(422, "invalid_coupon", "Coupon code is not active")
            if subtotal_minor < custom.minimum_subtotal_minor:
                raise CommerceError(
                    422, "coupon_minimum_not_met", "Coupon minimum subtotal was not met"
                )
            discount = (
                subtotal_minor * custom.value // 100 if custom.kind == "percent" else custom.value
            )
            if custom.maximum_discount_minor is not None:
                discount = min(discount, custom.maximum_discount_minor)
        else:
            discount = self.coupon.discount(coupon_code, subtotal_minor)
        discount = min(discount, subtotal_minor)
        discounted = subtotal_minor - discount
        shipping = self.shipping.cost(discounted)
        tax = self.tax_policy.tax(discounted + shipping)
        return PricingBreakdown(
            currency=currency,
            subtotal_minor=subtotal_minor,
            discount_minor=discount,
            shipping_minor=shipping,
            tax_minor=tax,
            total_minor=discounted + shipping + tax,
        )


@dataclass(frozen=True, slots=True)
class CommerceLimits:
    page_max: int
    cart_quantity_max: int
    address_max: int
    default_stock: int


def _page[T](items: Sequence[T], page: int, page_size: int) -> tuple[tuple[T, ...], int, int]:
    total = len(items)
    pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    return tuple(items[start : start + page_size]), total, pages


class CommerceService:
    def __init__(
        self,
        sandbox: SandboxService,
        pricing: PricingService,
        limits: CommerceLimits,
    ) -> None:
        self._sandbox = sandbox
        self._pricing = pricing
        self._limits = limits

    @staticmethod
    def _variants(
        catalog: CatalogSnapshot,
    ) -> tuple[
        dict[str, tuple[ProductSnapshot, VariantSnapshot]],
        dict[UUID, tuple[ProductSnapshot, VariantSnapshot]],
    ]:
        by_sku: dict[str, tuple[ProductSnapshot, VariantSnapshot]] = {}
        by_id: dict[UUID, tuple[ProductSnapshot, VariantSnapshot]] = {}
        for product in catalog.products:
            for variant in product.variants:
                if variant.sku in by_sku:
                    raise CommerceError(409, "duplicate_sku", "Catalog contains duplicate SKUs")
                by_sku[variant.sku] = (product, variant)
                by_id[variant.id] = (product, variant)
        return by_sku, by_id

    def _stock(self, state: SandboxState, variant_id: UUID) -> int:
        return state.stock_overrides.get(variant_id, self._limits.default_stock)

    def _product_view(self, product: ProductSnapshot, state: SandboxState) -> ProductView:
        if not product.variants:
            raise CommerceError(409, "product_has_no_variants", "Product has no variants")
        currencies = {variant.currency for variant in product.variants}
        if len(currencies) != 1:
            raise CommerceError(409, "mixed_product_currency", "Product variants mix currencies")
        variants = tuple(
            VariantView(
                id=variant.id,
                sku=variant.sku,
                name=variant.name,
                price_minor=variant.price_minor,
                currency=variant.currency,
                stock=self._stock(state, variant.id),
                available=self._stock(state, variant.id) > 0,
            )
            for variant in product.variants
        )
        prices = [variant.price_minor for variant in product.variants]
        return ProductView(
            id=product.id,
            category_id=product.category_id,
            slug=product.slug,
            name=product.name,
            description=product.description,
            variants=variants,
            media=product.media,
            available=any(variant.available for variant in variants),
            price_min_minor=min(prices),
            price_max_minor=max(prices),
            currency=next(iter(currencies)),
        )

    async def _state_catalog(self, session_id: str) -> tuple[SandboxState, CatalogSnapshot]:
        state = await self._sandbox.inspect(session_id)
        master = await self._sandbox.master_catalog(state.pinned_master_revision)
        return state, merge_catalog(master, state)

    async def categories(self, session_id: str, page: int, page_size: int) -> CategoryPage:
        _state, catalog = await self._state_catalog(session_id)
        ordered = sorted(catalog.categories, key=lambda item: (item.sort_order, item.name.lower()))
        items, total, pages = _page(ordered, page, page_size)
        return CategoryPage(items=items, page=page, page_size=page_size, total=total, pages=pages)

    async def category(self, session_id: str, identifier: str) -> CategorySnapshot:
        _state, catalog = await self._state_catalog(session_id)
        for category in catalog.categories:
            if str(category.id) == identifier or category.slug == identifier:
                return category
        raise CommerceError(404, "category_not_found", "Category was not found")

    async def products(
        self,
        session_id: str,
        *,
        page: int,
        page_size: int,
        search: str | None,
        category: str | None,
        min_price_minor: int | None,
        max_price_minor: int | None,
        available: bool | None,
        sort: Literal["name", "-name", "price", "-price"],
    ) -> ProductPage:
        state, catalog = await self._state_catalog(session_id)
        category_ids = {
            item.id
            for item in catalog.categories
            if category is not None and (str(item.id) == category or item.slug == category)
        }
        if category is not None and not category_ids:
            raise CommerceError(404, "category_not_found", "Category was not found")
        views = [self._product_view(product, state) for product in catalog.products]
        if search:
            needle = search.casefold()
            views = [
                item
                for item in views
                if needle in item.name.casefold()
                or needle in (item.description or "").casefold()
                or any(needle in variant.sku.casefold() for variant in item.variants)
            ]
        if category_ids:
            views = [item for item in views if item.category_id in category_ids]
        if min_price_minor is not None:
            views = [item for item in views if item.price_max_minor >= min_price_minor]
        if max_price_minor is not None:
            views = [item for item in views if item.price_min_minor <= max_price_minor]
        if available is not None:
            views = [item for item in views if item.available is available]
        key = (
            (lambda item: item.name.casefold())
            if sort.lstrip("-") == "name"
            else (lambda item: item.price_min_minor)
        )
        views.sort(key=key, reverse=sort.startswith("-"))
        items, total, pages = _page(views, page, page_size)
        return ProductPage(items=items, page=page, page_size=page_size, total=total, pages=pages)

    async def product(self, session_id: str, identifier: str) -> ProductView:
        state, catalog = await self._state_catalog(session_id)
        for product in catalog.products:
            if str(product.id) == identifier or product.slug == identifier:
                return self._product_view(product, state)
        raise CommerceError(404, "product_not_found", "Product was not found")

    def _resolved_cart(
        self, state: SandboxState, catalog: CatalogSnapshot
    ) -> tuple[list[tuple[ProductSnapshot, VariantSnapshot, int]], str]:
        by_sku, by_id = self._variants(catalog)
        resolved: list[tuple[ProductSnapshot, VariantSnapshot, int]] = []
        currencies: set[str] = set()
        for line in state.cart.lines:
            item = by_sku.get(line.sku) if line.sku is not None else by_id.get(line.variant_id)
            if item is None:
                raise CommerceError(409, "cart_item_unavailable", "A cart item is unavailable")
            product, variant = item
            resolved.append((product, variant, line.quantity))
            currencies.add(variant.currency)
        if len(currencies) > 1:
            raise CommerceError(409, "mixed_cart_currency", "Cart items mix currencies")
        return resolved, next(iter(currencies), state.wallet.currency)

    def _cart_view(self, state: SandboxState, catalog: CatalogSnapshot) -> CartView:
        resolved, currency = self._resolved_cart(state, catalog)
        lines = tuple(
            CartLineView(
                variant_id=variant.id,
                sku=variant.sku,
                product_name=product.name,
                variant_name=variant.name,
                quantity=quantity,
                unit_price_minor=variant.price_minor,
                line_total_minor=variant.price_minor * quantity,
                currency=variant.currency,
                stock=self._stock(state, variant.id),
            )
            for product, variant, quantity in resolved
        )
        return CartView(
            lines=lines,
            item_count=sum(line.quantity for line in lines),
            subtotal_minor=sum(line.line_total_minor for line in lines),
            currency=currency,
        )

    async def cart(self, session_id: str) -> CartView:
        state, catalog = await self._state_catalog(session_id)
        return self._cart_view(state, catalog)

    async def change_cart(
        self,
        session_id: str,
        sku: str,
        quantity: int,
        *,
        add: bool,
    ) -> CartView:
        initial = await self._sandbox.inspect(session_id)
        master = await self._sandbox.master_catalog(initial.pinned_master_revision)

        def mutation(state: SandboxState) -> SandboxState:
            catalog = merge_catalog(master, state)
            by_sku, _by_id = self._variants(catalog)
            item = by_sku.get(sku)
            if item is None:
                raise CommerceError(404, "sku_not_found", "SKU was not found")
            _product, variant = item
            lines = list(state.cart.lines)
            index = next(
                (
                    position
                    for position, line in enumerate(lines)
                    if line.sku == sku or line.variant_id == variant.id
                ),
                None,
            )
            existing = 0 if index is None else lines[index].quantity
            target = existing + quantity if add else quantity
            if target > self._limits.cart_quantity_max:
                raise CommerceError(422, "quantity_too_large", "Quantity exceeds cart limit")
            if target > self._stock(state, variant.id):
                raise CommerceError(409, "insufficient_stock", "Requested quantity exceeds stock")
            replacement = CartLine(variant_id=variant.id, sku=sku, quantity=target)
            if index is None:
                lines.append(replacement)
            else:
                lines[index] = replacement
            return state.model_copy(update={"cart": state.cart.model_copy(update={"lines": lines})})

        state = await self._sandbox.mutate(session_id, mutation)
        return self._cart_view(state, merge_catalog(master, state))

    async def remove_cart(self, session_id: str, sku: str) -> CartView:
        initial = await self._sandbox.inspect(session_id)
        master = await self._sandbox.master_catalog(initial.pinned_master_revision)

        def mutation(state: SandboxState) -> SandboxState:
            catalog = merge_catalog(master, state)
            by_sku, _by_id = self._variants(catalog)
            item = by_sku.get(sku)
            variant_id = item[1].id if item else None
            lines = [
                line
                for line in state.cart.lines
                if line.sku != sku and (variant_id is None or line.variant_id != variant_id)
            ]
            if len(lines) == len(state.cart.lines):
                raise CommerceError(404, "cart_item_not_found", "Cart item was not found")
            return state.model_copy(update={"cart": state.cart.model_copy(update={"lines": lines})})

        state = await self._sandbox.mutate(session_id, mutation)
        return self._cart_view(state, merge_catalog(master, state))

    async def clear_cart(self, session_id: str) -> CartView:
        state = await self._sandbox.mutate(
            session_id,
            lambda current: current.model_copy(
                update={"cart": current.cart.model_copy(update={"lines": []})}
            ),
        )
        catalog = await self._sandbox.master_catalog(state.pinned_master_revision)
        return self._cart_view(state, merge_catalog(catalog, state))

    async def wishlist(self, session_id: str) -> tuple[ProductView, ...]:
        state, catalog = await self._state_catalog(session_id)
        products = {product.id: product for product in catalog.products}
        return tuple(
            self._product_view(products[item_id], state)
            for item_id in sorted(state.wishlist.product_ids, key=str)
            if item_id in products
        )

    async def change_wishlist(
        self, session_id: str, product_id: UUID, *, remove: bool
    ) -> tuple[ProductView, ...]:
        _initial, catalog = await self._state_catalog(session_id)
        if product_id not in {product.id for product in catalog.products}:
            raise CommerceError(404, "product_not_found", "Product was not found")

        def mutation(state: SandboxState) -> SandboxState:
            ids = set(state.wishlist.product_ids)
            ids.discard(product_id) if remove else ids.add(product_id)
            return state.model_copy(
                update={"wishlist": state.wishlist.model_copy(update={"product_ids": ids})}
            )

        await self._sandbox.mutate(session_id, mutation)
        return await self.wishlist(session_id)

    async def clear_wishlist(self, session_id: str) -> tuple[ProductView, ...]:
        await self._sandbox.mutate(
            session_id,
            lambda state: state.model_copy(
                update={"wishlist": state.wishlist.model_copy(update={"product_ids": set()})}
            ),
        )
        return ()

    async def addresses(self, session_id: str) -> tuple[AddressRecord, ...]:
        state = await self._sandbox.inspect(session_id)
        return tuple(state.addresses.addresses.values())

    async def put_address(
        self, session_id: str, address: AddressRecord
    ) -> tuple[AddressRecord, ...]:
        def mutation(state: SandboxState) -> SandboxState:
            addresses = dict(state.addresses.addresses)
            if address.id not in addresses and len(addresses) >= self._limits.address_max:
                raise CommerceError(422, "address_limit", "Address limit reached")
            addresses[address.id] = address
            return state.model_copy(
                update={"addresses": state.addresses.model_copy(update={"addresses": addresses})}
            )

        await self._sandbox.mutate(session_id, mutation)
        return await self.addresses(session_id)

    async def delete_address(self, session_id: str, address_id: UUID) -> tuple[AddressRecord, ...]:
        def mutation(state: SandboxState) -> SandboxState:
            addresses = dict(state.addresses.addresses)
            if addresses.pop(address_id, None) is None:
                raise CommerceError(404, "address_not_found", "Address was not found")
            return state.model_copy(
                update={"addresses": state.addresses.model_copy(update={"addresses": addresses})}
            )

        await self._sandbox.mutate(session_id, mutation)
        return await self.addresses(session_id)

    async def ledger(self, session_id: str, page: int, page_size: int) -> LedgerPage:
        state = await self._sandbox.inspect(session_id)
        ordered = list(reversed(state.wallet.ledger))
        items, total, pages = _page(ordered, page, page_size)
        return LedgerPage(items=items, page=page, page_size=page_size, total=total, pages=pages)

    async def adjust_wallet(
        self,
        session_id: str,
        amount_minor: int,
        reference: str,
        *,
        operation: Literal["credit", "debit"],
    ) -> SandboxState:
        def mutation(state: SandboxState) -> SandboxState:
            signed = amount_minor if operation == "credit" else -amount_minor
            balance = state.wallet.balance_minor + signed
            if balance < 0:
                raise CommerceError(409, "insufficient_funds", "Wallet has insufficient funds")
            entry = WalletLedgerEntry(
                id=uuid4(),
                amount_minor=signed,
                balance_after_minor=balance,
                kind="admin_credit" if operation == "credit" else "admin_debit",
                reference=reference,
                created_at=datetime.now(UTC),
            )
            wallet = state.wallet.model_copy(
                update={"balance_minor": balance, "ledger": [*state.wallet.ledger, entry]}
            )
            return state.model_copy(update={"wallet": wallet})

        return await self._sandbox.mutate(session_id, mutation)

    async def checkout(
        self,
        session_id: str,
        address_id: UUID,
        coupon_code: str | None,
        idempotency_key: str,
    ) -> OrderRecord:
        initial = await self._sandbox.inspect(session_id)
        master = await self._sandbox.master_catalog(initial.pinned_master_revision)
        result: list[OrderRecord] = []

        def mutation(state: SandboxState) -> SandboxState:
            replay_id = state.orders.idempotency_keys.get(idempotency_key)
            if replay_id is not None:
                result.append(state.orders.orders[replay_id])
                return state
            address = state.addresses.addresses.get(address_id)
            if address is None:
                raise CommerceError(404, "address_not_found", "Address was not found")
            catalog = merge_catalog(master, state)
            resolved, currency = self._resolved_cart(state, catalog)
            if not resolved:
                raise CommerceError(409, "empty_cart", "Cart is empty")
            if currency != state.wallet.currency:
                raise CommerceError(409, "currency_mismatch", "Cart and wallet currencies differ")
            for _product, variant, quantity in resolved:
                if quantity > self._stock(state, variant.id):
                    raise CommerceError(
                        409,
                        "insufficient_stock",
                        f"Insufficient stock for {variant.sku}",
                    )
            subtotal = sum(variant.price_minor * quantity for _, variant, quantity in resolved)
            pricing = self._pricing.calculate(
                currency=currency,
                subtotal_minor=subtotal,
                coupon_code=coupon_code,
                coupons=state.coupons,
            )
            if pricing.total_minor > state.wallet.balance_minor:
                raise CommerceError(409, "insufficient_funds", "Wallet has insufficient funds")
            now = datetime.now(UTC)
            order_id = uuid4()
            lines = tuple(
                OrderLineSnapshot(
                    variant_id=variant.id,
                    sku=variant.sku,
                    product_name=product.name,
                    variant_name=variant.name,
                    quantity=quantity,
                    unit_price_minor=variant.price_minor,
                    line_total_minor=variant.price_minor * quantity,
                )
                for product, variant, quantity in resolved
            )
            order = OrderRecord(
                id=order_id,
                status="placed",
                currency=currency,
                lines=lines,
                subtotal_minor=pricing.subtotal_minor,
                discount_minor=pricing.discount_minor,
                shipping_minor=pricing.shipping_minor,
                tax_minor=pricing.tax_minor,
                total_minor=pricing.total_minor,
                address=address.model_copy(deep=True),
                coupon_code=coupon_code,
                created_at=now,
                updated_at=now,
            )
            balance = state.wallet.balance_minor - pricing.total_minor
            ledger = WalletLedgerEntry(
                id=uuid4(),
                amount_minor=-pricing.total_minor,
                balance_after_minor=balance,
                kind="checkout_debit",
                reference=str(order_id),
                created_at=now,
            )
            orders = dict(state.orders.orders)
            orders[order_id] = order
            keys = dict(state.orders.idempotency_keys)
            keys[idempotency_key] = order_id
            stock = dict(state.stock_overrides)
            inventory_entries: list[InventoryLedgerEntry] = []
            for _product, variant, quantity in resolved:
                stock_after = self._stock(state, variant.id) - quantity
                stock[variant.id] = stock_after
                inventory_entries.append(
                    InventoryLedgerEntry(
                        id=uuid4(),
                        variant_id=variant.id,
                        sku=variant.sku,
                        quantity_delta=-quantity,
                        stock_after=stock_after,
                        kind="checkout_decrement",
                        reference=str(order_id),
                        created_at=now,
                    )
                )
            result.append(order)
            return state.model_copy(
                update={
                    "orders": state.orders.model_copy(
                        update={"orders": orders, "idempotency_keys": keys}
                    ),
                    "wallet": state.wallet.model_copy(
                        update={
                            "balance_minor": balance,
                            "ledger": [*state.wallet.ledger, ledger],
                        }
                    ),
                    "stock_overrides": stock,
                    "inventory_ledger": [
                        *state.inventory_ledger,
                        *inventory_entries,
                    ],
                    "cart": state.cart.model_copy(update={"lines": []}),
                }
            )

        await self._sandbox.mutate(session_id, mutation)
        return result[-1]

    async def orders(
        self, session_id: str, page: int, page_size: int
    ) -> tuple[tuple[OrderRecord, ...], int, int]:
        state = await self._sandbox.inspect(session_id)
        ordered = sorted(
            state.orders.orders.values(), key=lambda order: order.created_at, reverse=True
        )
        return _page(ordered, page, page_size)

    async def order(self, session_id: str, order_id: UUID) -> OrderRecord:
        state = await self._sandbox.inspect(session_id)
        order = state.orders.orders.get(order_id)
        if order is None:
            raise CommerceError(404, "order_not_found", "Order was not found")
        return order

    async def transition_order(
        self, session_id: str, order_id: UUID, action: Literal["cancel", "refund"]
    ) -> OrderRecord:
        result: list[OrderRecord] = []

        def mutation(state: SandboxState) -> SandboxState:
            order = state.orders.orders.get(order_id)
            if order is None:
                raise CommerceError(404, "order_not_found", "Order was not found")
            if order.status != "placed":
                raise CommerceError(409, "invalid_order_transition", "Order is already final")
            now = datetime.now(UTC)
            status: Literal["cancelled", "refunded"] = (
                "cancelled" if action == "cancel" else "refunded"
            )
            updated = order.model_copy(update={"status": status, "updated_at": now})
            balance = state.wallet.balance_minor + order.total_minor
            entry = WalletLedgerEntry(
                id=uuid4(),
                amount_minor=order.total_minor,
                balance_after_minor=balance,
                kind="cancellation_credit" if action == "cancel" else "refund_credit",
                reference=str(order_id),
                created_at=now,
            )
            orders = dict(state.orders.orders)
            orders[order_id] = updated
            stock = dict(state.stock_overrides)
            inventory_entries: list[InventoryLedgerEntry] = []
            for line in order.lines:
                stock_after = self._stock(state, line.variant_id) + line.quantity
                stock[line.variant_id] = stock_after
                inventory_entries.append(
                    InventoryLedgerEntry(
                        id=uuid4(),
                        variant_id=line.variant_id,
                        sku=line.sku,
                        quantity_delta=line.quantity,
                        stock_after=stock_after,
                        kind=("cancellation_restock" if action == "cancel" else "refund_restock"),
                        reference=str(order_id),
                        created_at=now,
                    )
                )
            result.append(updated)
            return state.model_copy(
                update={
                    "orders": state.orders.model_copy(update={"orders": orders}),
                    "wallet": state.wallet.model_copy(
                        update={
                            "balance_minor": balance,
                            "ledger": [*state.wallet.ledger, entry],
                        }
                    ),
                    "stock_overrides": stock,
                    "inventory_ledger": [
                        *state.inventory_ledger,
                        *inventory_entries,
                    ],
                }
            )

        await self._sandbox.mutate(session_id, mutation)
        return result[-1]
