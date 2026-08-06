"""Session-local catalog administration over atomic sandbox mutations."""

from collections.abc import Callable
from uuid import UUID, uuid4

from src.admin.schemas import (
    CategoryInput,
    CouponInput,
    InventoryAdjustment,
    ProductInput,
    VariantInput,
)
from src.catalog.ids import short_uuid
from src.catalog.schemas import (
    CatalogSnapshot,
    CategorySnapshot,
    MediaSnapshot,
    ProductSnapshot,
    VariantSnapshot,
)
from src.sandbox.merge import merge_catalog
from src.sandbox.models import (
    CategoryOverlay,
    CouponRecord,
    CustomVariant,
    ProductOverlay,
    SandboxState,
    VariantOverlay,
)
from src.sandbox.service import SandboxService


class AdminError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class AdminService:
    def __init__(self, sandbox: SandboxService, *, default_stock: int) -> None:
        self._sandbox = sandbox
        self._default_stock = default_stock

    async def _master(self, session_id: str) -> CatalogSnapshot:
        state = await self._sandbox.inspect(session_id)
        return await self._sandbox.master_catalog(state.pinned_master_revision)

    @staticmethod
    def _category(catalog: CatalogSnapshot, category_id: UUID) -> CategorySnapshot:
        item = next((item for item in catalog.categories if item.id == category_id), None)
        if item is None:
            raise AdminError(404, "category_not_found", "Category was not found")
        return item

    @staticmethod
    def _product(catalog: CatalogSnapshot, product_id: UUID) -> ProductSnapshot:
        item = next((item for item in catalog.products if item.id == product_id), None)
        if item is None:
            raise AdminError(404, "product_not_found", "Product was not found")
        return item

    @staticmethod
    def _variant(
        catalog: CatalogSnapshot, variant_id: UUID
    ) -> tuple[ProductSnapshot, VariantSnapshot]:
        for product in catalog.products:
            for variant in product.variants:
                if variant.id == variant_id:
                    return product, variant
        raise AdminError(404, "variant_not_found", "Variant was not found")

    @staticmethod
    def _validate_catalog(catalog: CatalogSnapshot) -> None:
        category_ids = {item.id for item in catalog.categories}
        category_slugs = [item.slug.casefold() for item in catalog.categories]
        if len(category_slugs) != len(set(category_slugs)):
            raise AdminError(409, "duplicate_category_slug", "Category slug already exists")
        product_slugs = [item.slug.casefold() for item in catalog.products]
        if len(product_slugs) != len(set(product_slugs)):
            raise AdminError(409, "duplicate_product_slug", "Product slug already exists")
        for category in catalog.categories:
            if category.parent_id is not None and category.parent_id not in category_ids:
                raise AdminError(422, "invalid_parent", "Parent category does not exist")
            if category.parent_id == category.id:
                raise AdminError(422, "category_cycle", "Category cannot parent itself")
        parents = {item.id: item.parent_id for item in catalog.categories}
        for category_id in parents:
            seen: set[UUID] = set()
            current: UUID | None = category_id
            while current is not None:
                if current in seen:
                    raise AdminError(422, "category_cycle", "Category hierarchy contains a cycle")
                seen.add(current)
                current = parents.get(current)
        if any(product.category_id not in category_ids for product in catalog.products):
            raise AdminError(422, "invalid_category", "Product category does not exist")

    async def catalog(self, session_id: str) -> tuple[SandboxState, CatalogSnapshot]:
        state = await self._sandbox.inspect(session_id)
        master = await self._sandbox.master_catalog(state.pinned_master_revision)
        return state, merge_catalog(master, state)

    async def _mutate_catalog(
        self,
        session_id: str,
        change: Callable[[SandboxState, CatalogSnapshot], SandboxState],
    ) -> tuple[SandboxState, CatalogSnapshot]:
        master = await self._master(session_id)

        def mutation(state: SandboxState) -> SandboxState:
            updated = change(state, merge_catalog(master, state))
            self._validate_catalog(merge_catalog(master, updated))
            return updated

        state = await self._sandbox.mutate(session_id, mutation)
        return state, merge_catalog(master, state)

    def _allocate_short_id(self, catalog: CatalogSnapshot) -> str:
        taken = {item.slug.casefold() for item in (*catalog.categories, *catalog.products)}
        taken.update(
            variant.sku.casefold()
            for product in catalog.products
            for variant in product.variants
        )
        for _ in range(8):
            candidate = short_uuid()
            if candidate not in taken:
                return candidate
        raise AdminError(500, "short_id_allocate_failed", "Could not allocate a unique short id")

    async def create_category(
        self, session_id: str, body: CategoryInput
    ) -> tuple[SandboxState, CategorySnapshot]:
        category_id = uuid4()

        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            category = CategorySnapshot(
                id=category_id,
                slug=self._allocate_short_id(catalog),
                **body.model_dump(),
            )
            custom = dict(state.custom_categories)
            custom[category_id] = category
            return state.model_copy(update={"custom_categories": custom})

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._category(catalog, category_id)

    async def update_category(
        self, session_id: str, category_id: UUID, body: CategoryInput
    ) -> tuple[SandboxState, CategorySnapshot]:
        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            current = self._category(catalog, category_id)
            data = body.model_dump()
            if category_id in state.custom_categories:
                custom = dict(state.custom_categories)
                custom[category_id] = current.model_copy(update=data)
                return state.model_copy(update={"custom_categories": custom})
            overlays = dict(state.category_overlays)
            overlays[category_id] = CategoryOverlay(**data)
            return state.model_copy(update={"category_overlays": overlays})

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._category(catalog, category_id)

    async def delete_category(self, session_id: str, category_id: UUID) -> SandboxState:
        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            self._category(catalog, category_id)
            if any(item.parent_id == category_id for item in catalog.categories):
                raise AdminError(409, "category_in_use", "Category has child categories")
            if any(item.category_id == category_id for item in catalog.products):
                raise AdminError(409, "category_in_use", "Category has products")
            if category_id in state.custom_categories:
                custom = dict(state.custom_categories)
                del custom[category_id]
                overlays = dict(state.category_overlays)
                overlays.pop(category_id, None)
                return state.model_copy(
                    update={"custom_categories": custom, "category_overlays": overlays}
                )
            tombstones = set(state.category_tombstones)
            tombstones.add(category_id)
            return state.model_copy(update={"category_tombstones": tombstones})

        state, _catalog = await self._mutate_catalog(session_id, change)
        return state

    async def restore_category(
        self, session_id: str, category_id: UUID
    ) -> tuple[SandboxState, CategorySnapshot]:
        master = await self._master(session_id)
        if not any(item.id == category_id for item in master.categories):
            raise AdminError(404, "master_category_not_found", "Master category was not found")

        def change(state: SandboxState, _catalog: CatalogSnapshot) -> SandboxState:
            overlays = dict(state.category_overlays)
            overlays.pop(category_id, None)
            tombstones = set(state.category_tombstones)
            tombstones.discard(category_id)
            return state.model_copy(
                update={"category_overlays": overlays, "category_tombstones": tombstones}
            )

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._category(catalog, category_id)

    async def create_product(
        self, session_id: str, body: ProductInput
    ) -> tuple[SandboxState, ProductSnapshot]:
        product_id = uuid4()

        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            product = ProductSnapshot(
                id=product_id,
                slug=self._allocate_short_id(catalog),
                variants=(),
                media=(),
                **body.model_dump(),
            )
            custom = dict(state.custom_products)
            custom[product_id] = product
            return state.model_copy(update={"custom_products": custom})

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._product(catalog, product_id)

    async def update_product(
        self, session_id: str, product_id: UUID, body: ProductInput
    ) -> tuple[SandboxState, ProductSnapshot]:
        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            current = self._product(catalog, product_id)
            if product_id in state.custom_products:
                custom = dict(state.custom_products)
                custom[product_id] = current.model_copy(update=body.model_dump())
                return state.model_copy(update={"custom_products": custom})
            overlays = dict(state.product_overlays)
            overlays[product_id] = ProductOverlay(**body.model_dump(), media=current.media)
            return state.model_copy(update={"product_overlays": overlays})

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._product(catalog, product_id)

    async def delete_product(self, session_id: str, product_id: UUID) -> SandboxState:
        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            product = self._product(catalog, product_id)
            variant_ids = {item.id for item in product.variants}
            if any(line.variant_id in variant_ids for line in state.cart.lines):
                raise AdminError(409, "product_in_cart", "Remove product from cart first")
            if product_id in state.custom_products:
                custom_products = dict(state.custom_products)
                del custom_products[product_id]
                custom_variants = {
                    key: value
                    for key, value in state.custom_variants.items()
                    if value.product_id != product_id
                }
                return state.model_copy(
                    update={
                        "custom_products": custom_products,
                        "custom_variants": custom_variants,
                    }
                )
            tombstones = set(state.product_tombstones)
            tombstones.add(product_id)
            return state.model_copy(update={"product_tombstones": tombstones})

        state, _catalog = await self._mutate_catalog(session_id, change)
        return state

    async def restore_product(
        self, session_id: str, product_id: UUID
    ) -> tuple[SandboxState, ProductSnapshot]:
        master = await self._master(session_id)
        if not any(item.id == product_id for item in master.products):
            raise AdminError(404, "master_product_not_found", "Master product was not found")

        def change(state: SandboxState, _catalog: CatalogSnapshot) -> SandboxState:
            overlays = dict(state.product_overlays)
            overlays.pop(product_id, None)
            tombstones = set(state.product_tombstones)
            tombstones.discard(product_id)
            master_product = self._product(master, product_id)
            variant_ids = {item.id for item in master_product.variants}
            variant_overlays = {
                key: value
                for key, value in state.variant_overlays.items()
                if key not in variant_ids
            }
            variant_tombstones = state.variant_tombstones - variant_ids
            custom_variants = {
                key: value
                for key, value in state.custom_variants.items()
                if value.product_id != product_id
            }
            stock = {
                key: value for key, value in state.stock_overrides.items() if key not in variant_ids
            }
            return state.model_copy(
                update={
                    "product_overlays": overlays,
                    "product_tombstones": tombstones,
                    "variant_overlays": variant_overlays,
                    "variant_tombstones": variant_tombstones,
                    "custom_variants": custom_variants,
                    "stock_overrides": stock,
                }
            )

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._product(catalog, product_id)

    async def create_variant(
        self, session_id: str, product_id: UUID, body: VariantInput
    ) -> tuple[SandboxState, VariantSnapshot]:
        variant_id = uuid4()

        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            self._product(catalog, product_id)
            variant = VariantSnapshot(
                id=variant_id,
                sku=self._allocate_short_id(catalog),
                **body.model_dump(),
            )
            custom = dict(state.custom_variants)
            custom[variant_id] = CustomVariant(product_id=product_id, variant=variant)
            return state.model_copy(update={"custom_variants": custom})

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._variant(catalog, variant_id)[1]

    async def update_variant(
        self, session_id: str, variant_id: UUID, body: VariantInput
    ) -> tuple[SandboxState, VariantSnapshot]:
        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            _product, current = self._variant(catalog, variant_id)
            data = body.model_dump()
            if variant_id in state.custom_variants:
                custom = dict(state.custom_variants)
                record = custom[variant_id]
                custom[variant_id] = record.model_copy(
                    update={"variant": current.model_copy(update=data)}
                )
                return state.model_copy(update={"custom_variants": custom})
            overlays = dict(state.variant_overlays)
            overlays[variant_id] = VariantOverlay(**data)
            return state.model_copy(update={"variant_overlays": overlays})

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._variant(catalog, variant_id)[1]

    async def delete_variant(self, session_id: str, variant_id: UUID) -> SandboxState:
        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            product, _variant = self._variant(catalog, variant_id)
            if any(line.variant_id == variant_id for line in state.cart.lines):
                raise AdminError(409, "variant_in_cart", "Remove variant from cart first")
            if len(product.variants) == 1:
                raise AdminError(409, "last_variant", "A product must retain one variant")
            if variant_id in state.custom_variants:
                custom = dict(state.custom_variants)
                del custom[variant_id]
                return state.model_copy(update={"custom_variants": custom})
            tombstones = set(state.variant_tombstones)
            tombstones.add(variant_id)
            return state.model_copy(update={"variant_tombstones": tombstones})

        state, _catalog = await self._mutate_catalog(session_id, change)
        return state

    async def restore_variant(
        self, session_id: str, variant_id: UUID
    ) -> tuple[SandboxState, VariantSnapshot]:
        master = await self._master(session_id)
        self._variant(master, variant_id)

        def change(state: SandboxState, _catalog: CatalogSnapshot) -> SandboxState:
            overlays = dict(state.variant_overlays)
            overlays.pop(variant_id, None)
            tombstones = set(state.variant_tombstones)
            tombstones.discard(variant_id)
            stock = dict(state.stock_overrides)
            stock.pop(variant_id, None)
            return state.model_copy(
                update={
                    "variant_overlays": overlays,
                    "variant_tombstones": tombstones,
                    "stock_overrides": stock,
                }
            )

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._variant(catalog, variant_id)[1]

    async def adjust_price(
        self, session_id: str, variant_id: UUID, price_minor: int, currency: str | None
    ) -> tuple[SandboxState, VariantSnapshot]:
        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            _product, current = self._variant(catalog, variant_id)
            update = {
                "price_minor": price_minor,
                "currency": currency or current.currency,
            }
            if variant_id in state.custom_variants:
                custom = dict(state.custom_variants)
                record = custom[variant_id]
                custom[variant_id] = record.model_copy(
                    update={"variant": current.model_copy(update=update)}
                )
                return state.model_copy(update={"custom_variants": custom})
            overlays = dict(state.variant_overlays)
            existing = overlays.get(variant_id, VariantOverlay())
            overlays[variant_id] = existing.model_copy(update=update)
            return state.model_copy(update={"variant_overlays": overlays})

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._variant(catalog, variant_id)[1]

    async def adjust_inventory(
        self, session_id: str, variant_id: UUID, body: InventoryAdjustment
    ) -> SandboxState:
        master = await self._master(session_id)

        def mutation(state: SandboxState) -> SandboxState:
            self._variant(merge_catalog(master, state), variant_id)
            current = state.stock_overrides.get(variant_id, self._default_stock)
            target = body.quantity if body.operation == "set" else current + body.quantity
            if target < 0:
                raise AdminError(422, "negative_inventory", "Inventory cannot be negative")
            stock = dict(state.stock_overrides)
            stock[variant_id] = target
            return state.model_copy(update={"stock_overrides": stock})

        return await self._sandbox.mutate(session_id, mutation)

    async def set_active(
        self, session_id: str, entity: str, entity_id: UUID, *, active: bool
    ) -> SandboxState:
        master = await self._master(session_id)

        def mutation(state: SandboxState) -> SandboxState:
            catalog = merge_catalog(master, state)
            field: str
            if entity == "categories":
                if active:
                    known = {item.id for item in master.categories} | set(state.custom_categories)
                    if entity_id not in known:
                        raise AdminError(404, "category_not_found", "Category was not found")
                else:
                    self._category(catalog, entity_id)
                field = "category_tombstones"
            elif entity == "products":
                if active:
                    known = {item.id for item in master.products} | set(state.custom_products)
                    if entity_id not in known:
                        raise AdminError(404, "product_not_found", "Product was not found")
                else:
                    product = self._product(catalog, entity_id)
                    variant_ids = {item.id for item in product.variants}
                    if any(line.variant_id in variant_ids for line in state.cart.lines):
                        raise AdminError(409, "product_in_cart", "Remove product from cart first")
                field = "product_tombstones"
            elif entity == "variants":
                if active:
                    master_variants = {
                        item.id for product in master.products for item in product.variants
                    }
                    if entity_id not in master_variants | set(state.custom_variants):
                        raise AdminError(404, "variant_not_found", "Variant was not found")
                else:
                    product, _variant = self._variant(catalog, entity_id)
                    if any(line.variant_id == entity_id for line in state.cart.lines):
                        raise AdminError(409, "variant_in_cart", "Remove variant from cart first")
                    if len(product.variants) == 1:
                        raise AdminError(409, "last_variant", "A product must retain one variant")
                field = "variant_tombstones"
            else:
                raise AdminError(404, "entity_not_found", "Entity type was not found")
            tombstones = set(getattr(state, field))
            tombstones.discard(entity_id) if active else tombstones.add(entity_id)
            updated = state.model_copy(update={field: tombstones})
            self._validate_catalog(merge_catalog(master, updated))
            return updated

        return await self._sandbox.mutate(session_id, mutation)

    async def coupons(self, session_id: str) -> tuple[CouponRecord, ...]:
        state = await self._sandbox.inspect(session_id)
        return tuple(sorted(state.coupons.values(), key=lambda item: item.code))

    async def put_coupon(
        self, session_id: str, body: CouponInput, *, create: bool
    ) -> tuple[SandboxState, CouponRecord]:
        code = body.code.upper()
        if body.kind == "percent" and body.value > 100:
            raise AdminError(422, "invalid_coupon_value", "Percent coupon cannot exceed 100")
        coupon = CouponRecord(**body.model_dump(exclude={"code"}), code=code)

        def mutation(state: SandboxState) -> SandboxState:
            exists = code in state.coupons
            if create and exists:
                raise AdminError(409, "coupon_exists", "Coupon already exists")
            if not create and not exists:
                raise AdminError(404, "coupon_not_found", "Coupon was not found")
            coupons = dict(state.coupons)
            coupons[code] = coupon
            return state.model_copy(update={"coupons": coupons})

        state = await self._sandbox.mutate(session_id, mutation)
        return state, coupon

    async def delete_coupon(self, session_id: str, code: str) -> SandboxState:
        normalized = code.upper()

        def mutation(state: SandboxState) -> SandboxState:
            coupons = dict(state.coupons)
            if coupons.pop(normalized, None) is None:
                raise AdminError(404, "coupon_not_found", "Coupon was not found")
            return state.model_copy(update={"coupons": coupons})

        return await self._sandbox.mutate(session_id, mutation)

    async def add_media(
        self, session_id: str, product_id: UUID, media: MediaSnapshot
    ) -> tuple[SandboxState, ProductSnapshot]:
        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            product = self._product(catalog, product_id)
            owned = dict(state.owned_media)
            owned[media.id] = media
            updated_media = tuple(
                sorted((*product.media, media), key=lambda item: (item.sort_order, str(item.id)))
            )
            if product_id in state.custom_products:
                custom = dict(state.custom_products)
                custom[product_id] = product.model_copy(update={"media": updated_media})
                return state.model_copy(update={"custom_products": custom, "owned_media": owned})
            overlays = dict(state.product_overlays)
            existing = overlays.get(product_id, ProductOverlay())
            overlays[product_id] = existing.model_copy(update={"media": updated_media})
            return state.model_copy(update={"product_overlays": overlays, "owned_media": owned})

        state, catalog = await self._mutate_catalog(session_id, change)
        return state, self._product(catalog, product_id)

    async def remove_media(
        self, session_id: str, media_id: UUID
    ) -> tuple[SandboxState, MediaSnapshot]:
        removed: list[MediaSnapshot] = []

        def change(state: SandboxState, catalog: CatalogSnapshot) -> SandboxState:
            media = state.owned_media.get(media_id)
            if media is None:
                raise AdminError(404, "media_not_found", "Owned media was not found")
            product = next(
                (item for item in catalog.products if any(m.id == media_id for m in item.media)),
                None,
            )
            owned = dict(state.owned_media)
            del owned[media_id]
            removed.append(media)
            if product is None:
                return state.model_copy(update={"owned_media": owned})
            updated_media = tuple(item for item in product.media if item.id != media_id)
            if product.id in state.custom_products:
                custom = dict(state.custom_products)
                custom[product.id] = product.model_copy(update={"media": updated_media})
                return state.model_copy(update={"custom_products": custom, "owned_media": owned})
            overlays = dict(state.product_overlays)
            existing = overlays.get(product.id, ProductOverlay())
            overlays[product.id] = existing.model_copy(update={"media": updated_media})
            return state.model_copy(update={"product_overlays": overlays, "owned_media": owned})

        state, _catalog = await self._mutate_catalog(session_id, change)
        return state, removed[-1]

    async def restore_all(self, session_id: str) -> SandboxState:
        def mutation(state: SandboxState) -> SandboxState:
            return state.model_copy(
                update={
                    "category_overlays": {},
                    "product_overlays": {},
                    "variant_overlays": {},
                    "custom_categories": {},
                    "custom_products": {},
                    "custom_variants": {},
                    "category_tombstones": set(),
                    "product_tombstones": set(),
                    "variant_tombstones": set(),
                    "stock_overrides": {},
                    "coupons": {},
                    "owned_media": {},
                }
            )

        return await self._sandbox.mutate(session_id, mutation)
