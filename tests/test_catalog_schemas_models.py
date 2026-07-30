from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.catalog.models import CatalogRevision, ProductVariant
from src.catalog.schemas import CatalogSnapshot, VariantSnapshot


def test_catalog_constraints_and_indexes_are_declared() -> None:
    revision_indexes = {index.name for index in CatalogRevision.__table__.indexes}
    variant_constraints = {constraint.name for constraint in ProductVariant.__table__.constraints}

    assert "uq_catalog_revisions_one_active" in revision_indexes
    assert "ck_catalog_variants_price_nonnegative" in variant_constraints
    assert "ck_catalog_variants_currency_length" in variant_constraints


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
