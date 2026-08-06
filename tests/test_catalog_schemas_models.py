from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.catalog.models import CatalogRevision, ProductVariant
from src.catalog.schemas import CatalogSnapshot, VariantSnapshot
from src.commerce.service import sale_price_minor


def test_sale_price_minor_applies_percent_off() -> None:
    assert sale_price_minor(100, 0) == 100
    assert sale_price_minor(100, 35) == 65
    assert sale_price_minor(99, 10) == 89


def test_catalog_constraints_and_indexes_are_declared() -> None:
    from src.catalog.models import Product

    revision_indexes = {index.name for index in CatalogRevision.__table__.indexes}
    variant_constraints = {constraint.name for constraint in ProductVariant.__table__.constraints}
    product_constraints = {constraint.name for constraint in Product.__table__.constraints}

    assert "uq_catalog_revisions_one_active" in revision_indexes
    assert "ck_catalog_variants_price_nonnegative" in variant_constraints
    assert "ck_catalog_variants_currency_length" in variant_constraints
    assert "ck_catalog_products_discount_percent" in product_constraints


def test_media_main_helpers_enforce_single_main() -> None:
    from src.catalog.schemas import MediaSnapshot, with_main_media, with_media_appended

    first = MediaSnapshot(
        id=uuid4(),
        object_key="a.jpg",
        content_type="image/jpeg",
        alt_text="a",
        byte_size=1,
        sort_order=1,
        is_main=True,
    )
    second = MediaSnapshot(
        id=uuid4(),
        object_key="b.jpg",
        content_type="image/jpeg",
        alt_text="b",
        byte_size=1,
        sort_order=0,
        is_main=True,
    )
    appended = with_media_appended((first,), second)
    assert appended[0].id == second.id
    assert appended[0].is_main is True
    assert appended[1].is_main is False

    promoted = with_main_media(appended, first.id)
    assert promoted is not None
    assert promoted[0].id == first.id
    assert promoted[0].is_main is True
    assert promoted[1].is_main is False
    assert with_main_media(appended, uuid4()) is None


def test_snapshot_schema_is_strict_and_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        VariantSnapshot(
            id=uuid4(),
            sku="SKU",
            name="Variant",
            price_minor="100",  # type: ignore[arg-type]
            currency="USD",
        )

    with pytest.raises(ValidationError):
        CatalogSnapshot(
            revision_id=uuid4(),
            revision_number=1,
            revision_label="v1",
            generated_at=datetime.now(UTC),
            categories=(),
            products=(),
            unexpected=True,  # type: ignore[call-arg]
        )


def test_currency_and_minor_unit_invariants() -> None:
    with pytest.raises(ValidationError):
        VariantSnapshot(id=uuid4(), sku="SKU", name="Variant", price_minor=-1, currency="usd")
