"""Tests for Groq RAG chunking, ranking, and SSE helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.assistant.chunker import documents_from_catalog, documents_from_reviews
from src.assistant.router import _sse
from src.assistant.store import cosine_similarity
from src.catalog.schemas import (
    CatalogSnapshot,
    CategorySnapshot,
    ProductSnapshot,
    VariantSnapshot,
)
from src.reviews.schemas import ProductReview


def test_cosine_identical_is_one() -> None:
    vector = [0.1, 0.2, 0.3]
    assert abs(cosine_similarity(vector, vector) - 1.0) < 1e-9


def test_catalog_documents_include_product_and_category() -> None:
    category_id = uuid4()
    product_id = uuid4()
    catalog = CatalogSnapshot(
        revision_id=uuid4(),
        revision_number=1,
        revision_label="v1",
        generated_at=datetime.now(UTC),
        categories=(
            CategorySnapshot(
                id=category_id,
                parent_id=None,
                slug="shoes",
                name="Shoes",
                description="Footwear",
                sort_order=0,
            ),
        ),
        products=(
            ProductSnapshot(
                id=product_id,
                category_id=category_id,
                brand="Acme",
                slug="runner",
                name="Runner",
                description="A fast shoe",
                details="Mesh upper",
                specifics=("mesh",),
                variants=(
                    VariantSnapshot(
                        id=uuid4(),
                        sku="RUN-1",
                        name="Default",
                        price_minor=12000,
                        currency="USD",
                    ),
                ),
                media=(),
            ),
        ),
    )
    docs = documents_from_catalog(catalog)
    sources = {doc.source for doc in docs}
    assert sources == {"category", "product"}
    product_doc = next(doc for doc in docs if doc.source == "product")
    assert "Runner" in product_doc.content
    assert "Acme" in product_doc.content
    assert product_doc.product_id == product_id


def test_hidden_reviews_are_skipped() -> None:
    hidden = ProductReview(
        id=uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        status="hidden",
        product_id=uuid4(),
        product_slug="x",
        product_name="X",
        rating=5,
        body="secret",
    )
    assert documents_from_reviews([hidden]) == []


def test_sse_format() -> None:
    line = _sse("delta", "Hello")
    assert line.startswith("event: delta\n")
    assert '"Hello"' in line
    assert line.endswith("\n\n")


def test_text_search_boosts_analytics_for_bestsellers() -> None:
    from src.assistant.store import _ANALYTICS_HINTS, _tokens

    tokens = set(_tokens("what is my best selling product"))
    assert tokens.intersection(_ANALYTICS_HINTS)
