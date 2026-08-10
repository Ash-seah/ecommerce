from uuid import UUID

from src.admin.schemas import CategoryInput, ProductInput
from src.commerce.schemas import CartQuantityRequest, CheckoutRequest, WishlistRequest
from src.master.schemas import CategoryCreate, ProductCreate, VariantCreate


def test_request_models_coerce_uuid_strings() -> None:
    variant_id = "00000000-0000-4000-8000-0000000000aa"
    product_id = "00000000-0000-4000-8000-0000000000bb"
    category_id = "00000000-0000-4000-8000-0000000000cc"
    address_id = "00000000-0000-4000-8000-0000000000dd"
    parent_id = "00000000-0000-4000-8000-0000000000ee"

    cart = CartQuantityRequest.model_validate({"variant_id": variant_id, "quantity": 1})
    assert cart.variant_id == UUID(variant_id)

    wishlist = WishlistRequest.model_validate({"product_id": product_id})
    assert wishlist.product_id == UUID(product_id)

    checkout = CheckoutRequest.model_validate(
        {"address_id": address_id, "delivery_option_id": "standard"}
    )
    assert checkout.address_id == UUID(address_id)
    assert checkout.delivery_option_id == "standard"

    admin_product = ProductInput.model_validate(
        {"category_id": category_id, "name": "Shirt", "description": None}
    )
    assert admin_product.category_id == UUID(category_id)

    admin_category = CategoryInput.model_validate(
        {"parent_id": parent_id, "name": "Child", "description": None, "sort_order": 1}
    )
    assert admin_category.parent_id == UUID(parent_id)

    master_product = ProductCreate.model_validate(
        {"category_id": category_id, "name": "Boot", "description": None}
    )
    assert master_product.category_id == UUID(category_id)

    master_category = CategoryCreate.model_validate(
        {"name": "Men", "parent_id": parent_id, "description": None}
    )
    assert master_category.parent_id == UUID(parent_id)

    master_variant = VariantCreate.model_validate(
        {
            "product_id": product_id,
            "name": "Boot / M",
            "price_minor": 1000,
            "currency": "IRR",
        }
    )
    assert master_variant.product_id == UUID(product_id)
