"""Purchase-gated product comments and ratings."""

import pytest
from test_commerce_services import address, commerce
from test_sandbox_engine import service_fixture

from src.commerce.service import CommerceError
from src.reviews.eligibility import purchased_order_id, star_summary
from src.reviews.schemas import ProductReview, ReviewCreateRequest, ReviewUpdate
from src.sandbox.merge import merge_catalog, storefront_catalog
from src.sandbox.models import SandboxState


@pytest.mark.asyncio
async def test_review_requires_purchase_then_allows_verified_buyer() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    service = commerce(sandbox, stock=5)
    session_id, _nonce, _state = await sandbox.create()
    product = master.products[0]
    variant = product.variants[0]

    with pytest.raises(CommerceError) as blocked:
        await service.create_product_review(
            session_id,
            str(product.id),
            ReviewCreateRequest(rating=5, body="Great shoes"),
        )
    assert blocked.value.code == "purchase_required"

    shipping = address()
    await service.put_address(session_id, shipping)
    await service.adjust_wallet(session_id, 1_000, "credit", operation="credit")
    await service.change_cart(session_id, variant.id, 1, add=False)
    order = await service.checkout(session_id, shipping.id, None, "review-1", delivery_option_id="standard")

    product_view = await service.product(session_id, str(product.id))
    assert product_view.can_review is True
    assert product_view.my_review_id is None

    review = await service.create_product_review(
        session_id,
        product.slug,
        ReviewCreateRequest(rating=4, title="Solid", body="Comfortable and light"),
    )
    assert review.order_id == order.id
    assert review.rating == 4
    assert review.source == "checkout"
    assert review.author_label == "Verified buyer"

    listed = await service.list_product_reviews(session_id, product.slug, page=1, page_size=10)
    assert listed.total == 1
    assert listed.average_rating == 4.0
    assert listed.items[0].body == "Comfortable and light"

    detail = await service.product(session_id, product.slug)
    assert detail.can_review is False
    assert detail.my_review_id == review.id
    assert detail.average_rating == 4.0
    assert detail.rating_count == 1
    assert detail.rounded_stars == 4
    assert detail.star_counts.four == 1
    assert detail.star_counts.five == 0

    with pytest.raises(CommerceError) as duplicate:
        await service.create_product_review(
            session_id,
            str(product.id),
            ReviewCreateRequest(rating=1, body="Changed my mind"),
        )
    assert duplicate.value.code == "review_already_exists"

    updated = await service.update_product_review(
        session_id, review.id, ReviewUpdate(rating=5, body="Even better after a week")
    )
    assert updated.rating == 5
    assert updated.body.startswith("Even better")

    await service.delete_product_review(session_id, review.id)
    empty = await service.list_product_reviews(session_id, str(product.id), page=1, page_size=10)
    assert empty.total == 0
    reopen = await service.product(session_id, str(product.id))
    assert reopen.can_review is True


@pytest.mark.asyncio
async def test_cancelled_order_revokes_review_eligibility() -> None:
    sandbox, _redis, _secrets, master = await service_fixture()
    service = commerce(sandbox, stock=5)
    session_id, _nonce, _state = await sandbox.create()
    product = master.products[0]
    variant = product.variants[0]
    shipping = address()
    await service.put_address(session_id, shipping)
    await service.adjust_wallet(session_id, 1_000, "credit", operation="credit")
    await service.change_cart(session_id, variant.id, 1, add=False)
    order = await service.checkout(session_id, shipping.id, None, "review-cancel", delivery_option_id="standard")
    await service.transition_order(session_id, order.id, "cancel")

    state = await sandbox.inspect(session_id)
    catalog = storefront_catalog(merge_catalog(master, state))
    assert purchased_order_id(state, catalog, product.id) is None

    with pytest.raises(CommerceError) as blocked:
        await service.create_product_review(
            session_id,
            str(product.id),
            ReviewCreateRequest(rating=3, body="Still want to comment"),
        )
    assert blocked.value.code == "purchase_required"


def test_star_summary_histogram_and_rounding() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    now = datetime.now(UTC)
    product_id = uuid4()
    reviews = [
        ProductReview(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            product_id=product_id,
            product_slug="a",
            product_name="A",
            rating=5,
            body="a",
            status="published",
        ),
        ProductReview(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            product_id=product_id,
            product_slug="a",
            product_name="A",
            rating=1,
            body="b",
            status="hidden",
        ),
        ProductReview(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            product_id=product_id,
            product_slug="a",
            product_name="A",
            rating=3,
            body="c",
            status="published",
        ),
    ]
    summary = star_summary(reviews)
    assert summary.rating_count == 2
    assert summary.average_rating == 4.0
    assert summary.rounded_stars == 4
    assert summary.star_counts.five == 1
    assert summary.star_counts.three == 1
    assert summary.star_counts.one == 0


@pytest.mark.asyncio
async def test_product_catalog_filters_and_sorts_by_stars() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from src.admin.schemas import ProductInput, VariantInput
    from src.admin.service import AdminService

    sandbox, _redis, _secrets, master = await service_fixture()
    admin = AdminService(sandbox, default_stock=5)
    service = commerce(sandbox, stock=5)
    session_id, _nonce, _state = await sandbox.create()
    high = master.products[0]
    _state, low = await admin.create_product(
        session_id,
        ProductInput(
            category_id=high.category_id,
            name="Low rated product",
            description=None,
        ),
    )
    await admin.create_variant(
        session_id,
        low.id,
        VariantInput(name="Default", price_minor=80, currency="USD"),
    )
    now = datetime.now(UTC)

    def seed(state: SandboxState) -> SandboxState:
        reviews = dict(state.reviews)
        high_id = uuid4()
        low_id = uuid4()
        reviews[high_id] = ProductReview(
            id=high_id,
            created_at=now,
            updated_at=now,
            product_id=high.id,
            product_slug=high.slug,
            product_name=high.name,
            rating=5,
            body="excellent",
            status="published",
            sandbox_session_id="seed-a",
        )
        reviews[low_id] = ProductReview(
            id=low_id,
            created_at=now,
            updated_at=now,
            product_id=low.id,
            product_slug=low.slug,
            product_name=low.name,
            rating=2,
            body="meh",
            status="published",
            sandbox_session_id="seed-b",
        )
        return state.model_copy(update={"reviews": reviews})

    await sandbox.mutate(session_id, seed)

    min_four = await service.products(
        session_id,
        page=1,
        page_size=20,
        search=None,
        category=None,
        min_price_minor=None,
        max_price_minor=None,
        available=None,
        min_stars=4,
        sort="name",
    )
    assert {item.id for item in min_four.items} == {high.id}
    assert min_four.items[0].rounded_stars == 5
    assert min_four.items[0].star_counts.five == 1

    exact_two = await service.products(
        session_id,
        page=1,
        page_size=20,
        search=None,
        category=None,
        min_price_minor=None,
        max_price_minor=None,
        available=None,
        stars=2,
        sort="-rating",
    )
    assert [item.id for item in exact_two.items] == [low.id]

    ranked = await service.products(
        session_id,
        page=1,
        page_size=20,
        search=None,
        category=None,
        min_price_minor=None,
        max_price_minor=None,
        available=None,
        min_stars=1,
        sort="-rating",
    )
    assert [item.id for item in ranked.items] == [high.id, low.id]

    only_fives = await service.list_product_reviews(
        session_id, str(high.id), page=1, page_size=10, stars=5
    )
    assert only_fives.total == 1
    assert only_fives.star_counts.five == 1
    assert only_fives.rounded_stars == 5
