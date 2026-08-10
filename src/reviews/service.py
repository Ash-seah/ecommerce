"""Sandbox and master product review administration."""

from __future__ import annotations

from uuid import UUID

from src.reviews.capture import apply_review_update, review_from_create
from src.reviews.eligibility import star_summary
from src.reviews.repository import MasterReviewsRepository
from src.reviews.schemas import (
    ProductReview,
    ReviewCreate,
    ReviewList,
    ReviewUpdate,
)
from src.sandbox.models import SandboxState
from src.sandbox.service import SandboxService


class ReviewsAdminError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def _page_items(items: list[ProductReview], page: int, page_size: int) -> tuple[list, int, int]:
    total = len(items)
    pages = max(1, (total + page_size - 1) // page_size) if total else 0
    start = (page - 1) * page_size
    return items[start : start + page_size], total, pages


def _list_response(items: list[ProductReview], page: int, page_size: int) -> ReviewList:
    summary = star_summary(items)
    page_items, total, pages = _page_items(items, page, page_size)
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


class SandboxReviewsService:
    def __init__(self, sandbox: SandboxService) -> None:
        self._sandbox = sandbox

    async def list_reviews(
        self,
        session_id: str,
        *,
        page: int,
        page_size: int,
        product_id: UUID | None = None,
        status: str | None = None,
    ) -> ReviewList:
        state = await self._sandbox.inspect(session_id)
        items = list(state.reviews.values())
        if product_id is not None:
            items = [item for item in items if item.product_id == product_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return _list_response(items, page, page_size)

    async def get(self, session_id: str, review_id: UUID) -> ProductReview:
        state = await self._sandbox.inspect(session_id)
        review = state.reviews.get(review_id)
        if review is None:
            raise ReviewsAdminError(404, "review_not_found", "Review was not found")
        return review

    async def create(self, session_id: str, body: ReviewCreate) -> ProductReview:
        review = review_from_create(body, sandbox_session_id=session_id)

        def mutation(state: SandboxState) -> SandboxState:
            reviews = dict(state.reviews)
            reviews[review.id] = review
            return state.model_copy(update={"reviews": reviews})

        await self._sandbox.mutate(session_id, mutation)
        return review

    async def update(
        self, session_id: str, review_id: UUID, body: ReviewUpdate
    ) -> ProductReview:
        result: list[ProductReview] = []

        def mutation(state: SandboxState) -> SandboxState:
            current = state.reviews.get(review_id)
            if current is None:
                raise ReviewsAdminError(404, "review_not_found", "Review was not found")
            updated = apply_review_update(current, body.model_dump(exclude_unset=True))
            reviews = dict(state.reviews)
            reviews[review_id] = updated
            result.append(updated)
            return state.model_copy(update={"reviews": reviews})

        await self._sandbox.mutate(session_id, mutation)
        return result[-1]

    async def delete(self, session_id: str, review_id: UUID) -> None:
        def mutation(state: SandboxState) -> SandboxState:
            reviews = dict(state.reviews)
            if reviews.pop(review_id, None) is None:
                raise ReviewsAdminError(404, "review_not_found", "Review was not found")
            return state.model_copy(update={"reviews": reviews})

        await self._sandbox.mutate(session_id, mutation)


class MasterReviewsService:
    def __init__(self, repository: MasterReviewsRepository) -> None:
        self._repo = repository

    async def list_reviews(
        self,
        *,
        page: int,
        page_size: int,
        product_id: UUID | None = None,
        status: str | None = None,
    ) -> ReviewList:
        items = await self._repo.list_all()
        if product_id is not None:
            items = [item for item in items if item.product_id == product_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        return _list_response(items, page, page_size)

    async def get(self, review_id: UUID) -> ProductReview:
        return await self._repo.get(review_id)

    async def create(self, body: ReviewCreate) -> ProductReview:
        return await self._repo.create(body)

    async def update(self, review_id: UUID, body: ReviewUpdate) -> ProductReview:
        return await self._repo.update(review_id, body)

    async def delete(self, review_id: UUID) -> None:
        await self._repo.delete(review_id)
