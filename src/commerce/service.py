"""Commerce domain services over the atomic sandbox document."""

from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

from src.catalog.schemas import (
    CatalogSnapshot,
    ProductSnapshot,
    VariantSnapshot,
)
from src.commerce.category_tree import (
    build_category_forest,
    category_and_descendant_ids,
    category_subtree,
)
from src.commerce.delivery import DeliveryOptionsCatalog
from src.commerce.schemas import (
    CartLineView,
    CartView,
    CategoryNode,
    CategoryPage,
    DeliveryOptionList,
    DeliveryOptionView,
    LedgerPage,
    PricingBreakdown,
    ProductPage,
    ProductView,
    VariantView,
)
from src.reviews.capture import apply_review_update, review_from_create
from src.reviews.eligibility import (
    existing_session_review,
    purchased_order_id,
    star_summary,
)
from src.reviews.repository import MasterReviewsRepository
from src.reviews.schemas import (
    ProductReview,
    ReviewCreate,
    ReviewCreateRequest,
    ReviewList,
    ReviewUpdate,
)
from src.sales.capture import build_checkout_sales, void_sales_for_order
from src.sales.repository import MasterSalesRepository
from src.sales.schemas import SaleEvent
from src.sandbox.merge import merge_catalog, storefront_catalog
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
from src.views.capture import (
    auto_category_view,
    auto_product_view,
    enrich_record_request,
)
from src.views.repository import MasterViewsRepository
from src.views.schemas import ViewEvent, ViewRecordRequest


class CommerceError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def sale_price_minor(list_price_minor: int, discount_percent: int) -> int:
    """Apply a product percent-off to a list price in minor units."""

    if discount_percent <= 0:
        return list_price_minor
    return list_price_minor * (100 - discount_percent) // 100


class CouponPolicy(Protocol):
    def discount(self, code: str | None, subtotal_minor: int) -> int: ...


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
    """Legacy single-rate shipping; prefer DeliveryOptionsCatalog for checkout."""

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
    delivery: DeliveryOptionsCatalog
    tax_policy: TaxPolicy

    def _discount(
        self,
        *,
        subtotal_minor: int,
        coupon_code: str | None,
        coupons: dict[str, CouponRecord] | None,
    ) -> int:
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
        return min(discount, subtotal_minor)

    def calculate(
        self,
        *,
        currency: str,
        subtotal_minor: int,
        coupon_code: str | None,
        delivery_option_id: str,
        coupons: dict[str, CouponRecord] | None = None,
    ) -> PricingBreakdown:
        discount = self._discount(
            subtotal_minor=subtotal_minor, coupon_code=coupon_code, coupons=coupons
        )
        discounted = subtotal_minor - discount
        try:
            option = self.delivery.get(delivery_option_id)
        except KeyError as exc:
            raise CommerceError(
                422, "invalid_delivery_option", "Delivery option was not found"
            ) from exc
        shipping = self.delivery.priced_cost(delivery_option_id, discounted)
        tax = self.tax_policy.tax(discounted + shipping)
        return PricingBreakdown(
            currency=currency,
            subtotal_minor=subtotal_minor,
            discount_minor=discount,
            shipping_minor=shipping,
            tax_minor=tax,
            total_minor=discounted + shipping + tax,
            delivery_option_id=option.id,
            delivery_option_label=option.label,
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
        master_sales: MasterSalesRepository | None = None,
        master_views: MasterViewsRepository | None = None,
        master_reviews: MasterReviewsRepository | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._pricing = pricing
        self._limits = limits
        self._master_sales = master_sales
        self._master_views = master_views
        self._master_reviews = master_reviews

    async def _persist_view(self, session_id: str, event: ViewEvent) -> ViewEvent:
        def mutation(state: SandboxState) -> SandboxState:
            views = dict(state.views)
            views[event.id] = event
            return state.model_copy(update={"views": views})

        await self._sandbox.mutate(session_id, mutation)
        if self._master_views is not None:
            with suppress(Exception):
                await self._master_views.insert_many([event])
        return event

    async def record_view(
        self,
        session_id: str,
        body: ViewRecordRequest,
        *,
        user_agent: str | None = None,
    ) -> ViewEvent:
        _state, catalog = await self._state_catalog(session_id)
        if body.product_id is not None and not any(
            item.id == body.product_id for item in catalog.products
        ):
            raise CommerceError(404, "product_not_found", "Product was not found")
        if body.category_id is not None and not any(
            item.id == body.category_id for item in catalog.categories
        ):
            raise CommerceError(404, "category_not_found", "Category was not found")
        event = enrich_record_request(
            body,
            catalog,
            sandbox_session_id=session_id,
            user_agent=user_agent,
        )
        return await self._persist_view(session_id, event)

    @staticmethod
    def _variants(
        catalog: CatalogSnapshot,
    ) -> dict[UUID, tuple[ProductSnapshot, VariantSnapshot]]:
        by_id: dict[UUID, tuple[ProductSnapshot, VariantSnapshot]] = {}
        for product in catalog.products:
            for variant in product.variants:
                if variant.id in by_id:
                    raise CommerceError(
                        409, "duplicate_variant", "Catalog contains duplicate variant IDs"
                    )
                by_id[variant.id] = (product, variant)
        return by_id

    def _stock(self, state: SandboxState, variant_id: UUID) -> int:
        return state.stock_overrides.get(variant_id, self._limits.default_stock)

    @staticmethod
    def _units_sold_map(
        sales: dict[UUID, SaleEvent] | list[SaleEvent],
        *,
        since: datetime | None = None,
    ) -> dict[UUID, int]:
        counts: dict[UUID, int] = {}
        items = sales.values() if isinstance(sales, dict) else sales
        for sale in items:
            if sale.status != "recorded":
                continue
            if since is not None and sale.occurred_at < since:
                continue
            counts[sale.product_id] = counts.get(sale.product_id, 0) + sale.quantity
        return counts

    async def _merged_units_sold(
        self, state: SandboxState, *, since: datetime | None = None
    ) -> dict[UUID, int]:
        by_id = dict(state.sales)
        if self._master_sales is not None:
            with suppress(Exception):
                for sale in await self._master_sales.list_all():
                    by_id.setdefault(sale.id, sale)
        return self._units_sold_map(by_id, since=since)

    def _product_view(
        self,
        product: ProductSnapshot,
        state: SandboxState,
        *,
        catalog: CatalogSnapshot,
        reviews: list[ProductReview] | None = None,
        units_sold: int = 0,
    ) -> ProductView:
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
                list_price_minor=variant.price_minor,
                price_minor=sale_price_minor(variant.price_minor, product.discount_percent),
                currency=variant.currency,
                stock=self._stock(state, variant.id),
                available=self._stock(state, variant.id) > 0,
                media=variant.media,
            )
            for variant in product.variants
        )
        prices = [variant.price_minor for variant in variants]
        stock = sum(variant.stock for variant in variants)
        product_reviews = reviews if reviews is not None else self._product_reviews_local(
            state, product.id
        )
        stars = star_summary(product_reviews)
        mine = existing_session_review(state, product.id)
        can_review = (
            mine is None
            and purchased_order_id(state, catalog, product.id) is not None
        )
        return ProductView(
            id=product.id,
            category_id=product.category_id,
            brand=product.brand,
            slug=product.slug,
            name=product.name,
            description=product.description,
            details=product.details,
            specifics=product.specifics,
            discount_percent=product.discount_percent,
            variants=variants,
            media=product.media,
            available=any(variant.available for variant in variants),
            stock=stock,
            price_min_minor=min(prices),
            price_max_minor=max(prices),
            currency=next(iter(currencies)),
            average_rating=stars.average_rating,
            rating_count=stars.rating_count,
            rounded_stars=stars.rounded_stars,
            star_counts=stars.star_counts,
            can_review=can_review,
            my_review_id=None if mine is None else mine.id,
            units_sold=units_sold,
        )

    @staticmethod
    def _product_reviews_local(state: SandboxState, product_id: UUID) -> list[ProductReview]:
        return [
            item
            for item in state.reviews.values()
            if item.product_id == product_id and item.status == "published"
        ]

    async def _merged_product_reviews(
        self, state: SandboxState, product_id: UUID
    ) -> list[ProductReview]:
        by_id = {item.id: item for item in self._product_reviews_local(state, product_id)}
        if self._master_reviews is not None:
            with suppress(Exception):
                for item in await self._master_reviews.list_for_product(product_id):
                    by_id.setdefault(item.id, item)
        items = list(by_id.values())
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items

    async def _state_catalog(self, session_id: str) -> tuple[SandboxState, CatalogSnapshot]:
        state = await self._sandbox.inspect(session_id)
        master = await self._sandbox.master_catalog(state.pinned_master_revision)
        return state, storefront_catalog(merge_catalog(master, state))

    async def categories(self, session_id: str, page: int, page_size: int) -> CategoryPage:
        _state, catalog = await self._state_catalog(session_id)
        # Page root categories; each item includes its full nested child tree.
        roots = build_category_forest(catalog.categories)
        items, total, pages = _page(roots, page, page_size)
        return CategoryPage(items=items, page=page, page_size=page_size, total=total, pages=pages)

    async def category(self, session_id: str, identifier: str) -> CategoryNode:
        _state, catalog = await self._state_catalog(session_id)
        node = category_subtree(catalog.categories, identifier)
        if node is None:
            raise CommerceError(404, "category_not_found", "Category was not found")
        snapshot = next(
            (
                item
                for item in catalog.categories
                if str(item.id) == identifier or item.slug == identifier
            ),
            None,
        )
        if snapshot is not None:
            await self._persist_view(
                session_id,
                auto_category_view(snapshot, sandbox_session_id=session_id),
            )
        return node

    async def products(
        self,
        session_id: str,
        *,
        page: int,
        page_size: int,
        search: str | None,
        category: str | None,
        brand: str | None = None,
        min_price_minor: int | None,
        max_price_minor: int | None,
        available: bool | None,
        min_stars: int | None = None,
        max_stars: int | None = None,
        stars: int | None = None,
        sort: Literal[
            "name", "-name", "price", "-price", "rating", "-rating", "sold", "-sold"
        ] = "name",
    ) -> ProductPage:
        state, catalog = await self._state_catalog(session_id)
        category_ids: set[UUID] | None = None
        if category is not None:
            category_ids = category_and_descendant_ids(catalog.categories, category)
            if category_ids is None:
                raise CommerceError(404, "category_not_found", "Category was not found")
        units_sold = await self._merged_units_sold(state)
        views = [
            self._product_view(
                product,
                state,
                catalog=catalog,
                units_sold=units_sold.get(product.id, 0),
            )
            for product in catalog.products
            if product.variants
        ]
        if search:
            needle = search.casefold()
            views = [
                item
                for item in views
                if needle in item.name.casefold()
                or needle in (item.description or "").casefold()
                or (item.brand is not None and needle in item.brand.casefold())
                or any(needle in variant.sku.casefold() for variant in item.variants)
            ]
        if category_ids is not None:
            views = [item for item in views if item.category_id in category_ids]
        if brand is not None:
            brand_needle = brand.casefold()
            views = [
                item
                for item in views
                if item.brand is not None and item.brand.casefold() == brand_needle
            ]
        if min_price_minor is not None:
            views = [item for item in views if item.price_max_minor >= min_price_minor]
        if max_price_minor is not None:
            views = [item for item in views if item.price_min_minor <= max_price_minor]
        if available is not None:
            views = [item for item in views if item.available is available]
        if min_stars is not None:
            views = [
                item
                for item in views
                if item.average_rating is not None and item.average_rating >= min_stars
            ]
        if max_stars is not None:
            views = [
                item
                for item in views
                if item.average_rating is not None and item.average_rating <= max_stars
            ]
        if stars is not None:
            views = [item for item in views if item.rounded_stars == stars]
        sort_field = sort.lstrip("-")
        if sort_field == "name":
            key = lambda item: item.name.casefold()
        elif sort_field == "rating":
            # Unrated products sort last whether ascending or descending.
            key = lambda item: (
                item.average_rating is None,
                -(item.average_rating or 0.0)
                if sort.startswith("-")
                else (item.average_rating or 0.0),
            )
        elif sort_field == "sold":
            key = lambda item: item.units_sold
        else:
            key = lambda item: item.price_min_minor
        if sort_field == "rating":
            views.sort(key=key)
        else:
            views.sort(key=key, reverse=sort.startswith("-"))
        items, total, pages = _page(views, page, page_size)
        return ProductPage(items=items, page=page, page_size=page_size, total=total, pages=pages)

    async def trending_products(
        self,
        session_id: str,
        *,
        page: int,
        page_size: int,
        window_days: int = 7,
    ) -> ProductPage:
        """Products ranked by recorded units sold in the recent sales window."""

        state, catalog = await self._state_catalog(session_id)
        since = datetime.now(UTC) - timedelta(days=window_days)
        units_sold = await self._merged_units_sold(state, since=since)
        ranked = sorted(
            (
                product
                for product in catalog.products
                if product.variants and units_sold.get(product.id, 0) > 0
            ),
            key=lambda product: (
                -units_sold.get(product.id, 0),
                product.name.casefold(),
                str(product.id),
            ),
        )
        views = [
            self._product_view(
                product,
                state,
                catalog=catalog,
                units_sold=units_sold.get(product.id, 0),
            )
            for product in ranked
        ]
        items, total, pages = _page(views, page, page_size)
        return ProductPage(items=items, page=page, page_size=page_size, total=total, pages=pages)

    async def product(self, session_id: str, identifier: str) -> ProductView:
        state, catalog = await self._state_catalog(session_id)
        units_sold = await self._merged_units_sold(state)
        for product in catalog.products:
            if str(product.id) == identifier or product.slug == identifier:
                if not product.variants:
                    break
                reviews = await self._merged_product_reviews(state, product.id)
                view = self._product_view(
                    product,
                    state,
                    catalog=catalog,
                    reviews=reviews,
                    units_sold=units_sold.get(product.id, 0),
                )
                await self._persist_view(
                    session_id,
                    auto_product_view(product, catalog, sandbox_session_id=session_id),
                )
                return view
        raise CommerceError(404, "product_not_found", "Product was not found")

    def _resolve_product(
        self, catalog: CatalogSnapshot, identifier: str
    ) -> ProductSnapshot:
        for product in catalog.products:
            if str(product.id) == identifier or product.slug == identifier:
                return product
        raise CommerceError(404, "product_not_found", "Product was not found")

    async def list_product_reviews(
        self,
        session_id: str,
        identifier: str,
        *,
        page: int,
        page_size: int,
        stars: int | None = None,
    ) -> ReviewList:
        state, catalog = await self._state_catalog(session_id)
        product = self._resolve_product(catalog, identifier)
        items = await self._merged_product_reviews(state, product.id)
        summary = star_summary(items)
        if stars is not None:
            items = [item for item in items if item.rating == stars]
        total = len(items)
        pages = max(1, (total + page_size - 1) // page_size) if total else 0
        start = (page - 1) * page_size
        page_items = items[start : start + page_size]
        return ReviewList(
            items=tuple(page_items),
            page=page,
            page_size=page_size,
            total=total,
            pages=pages,
            average_rating=summary.average_rating,
            rating_count=summary.rating_count,
            rounded_stars=summary.rounded_stars,
            star_counts=summary.star_counts,
        )

    async def create_product_review(
        self,
        session_id: str,
        identifier: str,
        body: ReviewCreateRequest,
    ) -> ProductReview:
        state, catalog = await self._state_catalog(session_id)
        product = self._resolve_product(catalog, identifier)
        if existing_session_review(state, product.id) is not None:
            raise CommerceError(
                409, "review_already_exists", "This session already reviewed this product"
            )
        order_id = purchased_order_id(state, catalog, product.id)
        if order_id is None:
            raise CommerceError(
                403,
                "purchase_required",
                "Only buyers with a placed order for this product can review it",
            )
        review = review_from_create(
            ReviewCreate(
                product_id=product.id,
                product_slug=product.slug,
                product_name=product.name,
                rating=body.rating,
                title=body.title,
                body=body.body,
                order_id=order_id,
                source="checkout",
            ),
            sandbox_session_id=session_id,
            order_id=order_id,
        )

        def mutation(current: SandboxState) -> SandboxState:
            if existing_session_review(current, product.id) is not None:
                raise CommerceError(
                    409,
                    "review_already_exists",
                    "This session already reviewed this product",
                )
            if purchased_order_id(current, catalog, product.id) is None:
                raise CommerceError(
                    403,
                    "purchase_required",
                    "Only buyers with a placed order for this product can review it",
                )
            reviews = dict(current.reviews)
            reviews[review.id] = review
            return current.model_copy(update={"reviews": reviews})

        await self._sandbox.mutate(session_id, mutation)
        if self._master_reviews is not None:
            with suppress(Exception):
                await self._master_reviews.upsert(review)
        return review

    async def update_product_review(
        self,
        session_id: str,
        review_id: UUID,
        body: ReviewUpdate,
    ) -> ProductReview:
        result: list[ProductReview] = []

        def mutation(state: SandboxState) -> SandboxState:
            current = state.reviews.get(review_id)
            if current is None or current.sandbox_session_id != session_id:
                raise CommerceError(404, "review_not_found", "Review was not found")
            updated = apply_review_update(
                current,
                body.model_dump(exclude_unset=True, exclude={"status", "author_label"}),
            )
            reviews = dict(state.reviews)
            reviews[review_id] = updated
            result.append(updated)
            return state.model_copy(update={"reviews": reviews})

        await self._sandbox.mutate(session_id, mutation)
        if self._master_reviews is not None:
            with suppress(Exception):
                await self._master_reviews.upsert(result[-1])
        return result[-1]

    async def delete_product_review(self, session_id: str, review_id: UUID) -> None:
        def mutation(state: SandboxState) -> SandboxState:
            current = state.reviews.get(review_id)
            if current is None or current.sandbox_session_id != session_id:
                raise CommerceError(404, "review_not_found", "Review was not found")
            reviews = dict(state.reviews)
            reviews.pop(review_id)
            return state.model_copy(update={"reviews": reviews})

        await self._sandbox.mutate(session_id, mutation)
        if self._master_reviews is not None:
            with suppress(Exception):
                await self._master_reviews.delete(review_id)

    def _resolved_cart(
        self, state: SandboxState, catalog: CatalogSnapshot
    ) -> tuple[list[tuple[ProductSnapshot, VariantSnapshot, int]], str]:
        by_id = self._variants(catalog)
        resolved: list[tuple[ProductSnapshot, VariantSnapshot, int]] = []
        currencies: set[str] = set()
        for line in state.cart.lines:
            item = by_id.get(line.variant_id)
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
                product_name=product.name,
                variant_name=variant.name,
                quantity=quantity,
                list_price_minor=variant.price_minor,
                unit_price_minor=sale_price_minor(variant.price_minor, product.discount_percent),
                line_total_minor=sale_price_minor(variant.price_minor, product.discount_percent)
                * quantity,
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
        variant_id: UUID,
        quantity: int,
        *,
        add: bool,
    ) -> CartView:
        initial = await self._sandbox.inspect(session_id)
        master = await self._sandbox.master_catalog(initial.pinned_master_revision)

        def mutation(state: SandboxState) -> SandboxState:
            catalog = storefront_catalog(merge_catalog(master, state))
            item = self._variants(catalog).get(variant_id)
            if item is None:
                raise CommerceError(404, "variant_not_found", "Variant was not found")
            _product, variant = item
            lines = list(state.cart.lines)
            index = next(
                (position for position, line in enumerate(lines) if line.variant_id == variant.id),
                None,
            )
            existing = 0 if index is None else lines[index].quantity
            target = existing + quantity if add else quantity
            if target > self._limits.cart_quantity_max:
                raise CommerceError(422, "quantity_too_large", "Quantity exceeds cart limit")
            if target > self._stock(state, variant.id):
                raise CommerceError(409, "insufficient_stock", "Requested quantity exceeds stock")
            replacement = CartLine(variant_id=variant.id, quantity=target)
            if index is None:
                lines.append(replacement)
            else:
                lines[index] = replacement
            return state.model_copy(update={"cart": state.cart.model_copy(update={"lines": lines})})

        state = await self._sandbox.mutate(session_id, mutation)
        return self._cart_view(state, storefront_catalog(merge_catalog(master, state)))

    async def remove_cart(self, session_id: str, variant_id: UUID) -> CartView:
        initial = await self._sandbox.inspect(session_id)
        master = await self._sandbox.master_catalog(initial.pinned_master_revision)

        def mutation(state: SandboxState) -> SandboxState:
            lines = [line for line in state.cart.lines if line.variant_id != variant_id]
            if len(lines) == len(state.cart.lines):
                raise CommerceError(404, "cart_item_not_found", "Cart item was not found")
            return state.model_copy(update={"cart": state.cart.model_copy(update={"lines": lines})})

        state = await self._sandbox.mutate(session_id, mutation)
        return self._cart_view(state, storefront_catalog(merge_catalog(master, state)))

    async def clear_cart(self, session_id: str) -> CartView:
        state = await self._sandbox.mutate(
            session_id,
            lambda current: current.model_copy(
                update={"cart": current.cart.model_copy(update={"lines": []})}
            ),
        )
        catalog = await self._sandbox.master_catalog(state.pinned_master_revision)
        return self._cart_view(state, storefront_catalog(merge_catalog(catalog, state)))

    async def wishlist(self, session_id: str) -> tuple[ProductView, ...]:
        state, catalog = await self._state_catalog(session_id)
        products = {product.id: product for product in catalog.products if product.variants}
        units_sold = await self._merged_units_sold(state)
        return tuple(
            self._product_view(
                products[item_id],
                state,
                catalog=catalog,
                units_sold=units_sold.get(item_id, 0),
            )
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

    async def delivery_options(
        self, session_id: str, *, coupon_code: str | None = None
    ) -> DeliveryOptionList:
        """Quote delivery choices for the current cart (end of payment flow)."""

        state, catalog = await self._state_catalog(session_id)
        resolved, currency = self._resolved_cart(state, catalog)
        if not resolved:
            raise CommerceError(409, "empty_cart", "Cart is empty")
        subtotal = sum(
            sale_price_minor(variant.price_minor, product.discount_percent) * quantity
            for product, variant, quantity in resolved
        )
        discount = self._pricing._discount(
            subtotal_minor=subtotal, coupon_code=coupon_code, coupons=state.coupons
        )
        discounted = subtotal - discount
        items = tuple(
            DeliveryOptionView(
                id=option.id,
                label=option.label,
                description=option.description,
                cost_minor=cost,
                eta_min_days=option.eta_min_days,
                eta_max_days=option.eta_max_days,
                free_shipping_applied=option.free_shipping_eligible
                and option.cost_minor > 0
                and cost == 0,
            )
            for option, cost in self._pricing.delivery.quoted(discounted)
        )
        return DeliveryOptionList(
            items=items,
            currency=currency,
            subtotal_minor=subtotal,
            discount_minor=discount,
            free_shipping_threshold_minor=self._pricing.delivery.free_threshold_minor,
        )

    async def checkout(
        self,
        session_id: str,
        address_id: UUID,
        coupon_code: str | None,
        idempotency_key: str,
        *,
        delivery_option_id: str,
    ) -> OrderRecord:
        initial = await self._sandbox.inspect(session_id)
        master = await self._sandbox.master_catalog(initial.pinned_master_revision)
        result: list[OrderRecord] = []
        captured: list[SaleEvent] = []

        def mutation(state: SandboxState) -> SandboxState:
            replay_id = state.orders.idempotency_keys.get(idempotency_key)
            if replay_id is not None:
                result.append(state.orders.orders[replay_id])
                return state
            address = state.addresses.addresses.get(address_id)
            if address is None:
                raise CommerceError(404, "address_not_found", "Address was not found")
            catalog = storefront_catalog(merge_catalog(master, state))
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
            subtotal = sum(
                sale_price_minor(variant.price_minor, product.discount_percent) * quantity
                for product, variant, quantity in resolved
            )
            pricing = self._pricing.calculate(
                currency=currency,
                subtotal_minor=subtotal,
                coupon_code=coupon_code,
                delivery_option_id=delivery_option_id,
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
                    unit_price_minor=sale_price_minor(
                        variant.price_minor, product.discount_percent
                    ),
                    line_total_minor=sale_price_minor(
                        variant.price_minor, product.discount_percent
                    )
                    * quantity,
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
                delivery_option_id=pricing.delivery_option_id,
                delivery_option_label=pricing.delivery_option_label,
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
            sale_events = build_checkout_sales(
                order=order,
                resolved=resolved,
                catalog=catalog,
                sandbox_session_id=session_id,
            )
            sales = dict(state.sales)
            for event in sale_events:
                sales[event.id] = event
            captured.extend(sale_events)
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
                    "sales": sales,
                }
            )

        await self._sandbox.mutate(session_id, mutation)
        if self._master_sales is not None and captured:
            # Sandbox ledger wins; master fan-out is best-effort for the demo.
            with suppress(Exception):
                await self._master_sales.insert_many(captured)
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
            sales = void_sales_for_order(
                state.sales,
                order_id,
                reason=f"order_{status}",
                at=now,
            )
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
                    "sales": sales,
                }
            )

        await self._sandbox.mutate(session_id, mutation)
        if self._master_sales is not None:
            with suppress(Exception):
                await self._master_sales.void_order(order_id, reason=f"order_{action}")
        return result[-1]
