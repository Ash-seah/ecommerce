from scripts.seed_shoes_via_master_api import CATALOG, catalog_counts


def test_shoes_catalog_tree_shape() -> None:
    counts = catalog_counts(CATALOG)
    assert counts == {
        "mothers": 2,
        "children": 6,
        "products": 18,
        "variants": 54,
    }
    skus = [
        f"{product.sku_prefix}-{size}"
        for mother in CATALOG
        for child in mother.children
        for product in child.products
        for size in ("S", "M", "L")
    ]
    assert len(skus) == len(set(skus))
