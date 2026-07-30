"""Copy-on-write catalog projection for a pinned master revision."""

from src.catalog.schemas import CatalogSnapshot, CategorySnapshot, ProductSnapshot
from src.sandbox.models import SandboxState


def merge_catalog(master: CatalogSnapshot, state: SandboxState) -> CatalogSnapshot:
    if master.revision_number != state.pinned_master_revision:
        raise ValueError("master snapshot does not match the sandbox pinned revision")

    categories: list[CategorySnapshot] = []
    for category in master.categories:
        if category.id in state.category_tombstones:
            continue
        category_overlay = state.category_overlays.get(category.id)
        categories.append(
            category
            if category_overlay is None
            else category.model_copy(update=category_overlay.model_dump(exclude_unset=True))
        )
    categories.extend(
        category
        for category_id, category in state.custom_categories.items()
        if category_id not in state.category_tombstones
    )

    products: list[ProductSnapshot] = []
    all_products = {
        product.id: product
        for product in (*master.products, *state.custom_products.values())
        if product.id not in state.product_tombstones
    }
    for product in all_products.values():
        product_overlay = state.product_overlays.get(product.id)
        variants = []
        for variant in product.variants:
            if variant.id in state.variant_tombstones:
                continue
            variant_overlay = state.variant_overlays.get(variant.id)
            variants.append(
                variant
                if variant_overlay is None
                else variant.model_copy(update=variant_overlay.model_dump(exclude_unset=True))
            )
        variants.extend(
            custom.variant
            for variant_id, custom in state.custom_variants.items()
            if custom.product_id == product.id and variant_id not in state.variant_tombstones
        )
        update: dict[str, object] = {"variants": tuple(variants)}
        if product_overlay is not None:
            update.update(product_overlay.model_dump(exclude_unset=True))
        products.append(product.model_copy(update=update))

    return master.model_copy(update={"categories": tuple(categories), "products": tuple(products)})
