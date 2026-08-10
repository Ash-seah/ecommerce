"""Master product reviews backed by Postgres."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.reviews.capture import apply_review_update, review_from_create
from src.reviews.models import ProductReviewRow
from src.reviews.schemas import ProductReview, ReviewCreate, ReviewUpdate


class MasterReviewsError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def _to_review(row: ProductReviewRow) -> ProductReview:
    return ProductReview.model_validate(row, from_attributes=True)


def _apply_row(row: ProductReviewRow, review: ProductReview) -> None:
    for field in ProductReview.model_fields:
        setattr(row, field, getattr(review, field))


class MasterReviewsRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(self) -> list[ProductReview]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(ProductReviewRow).order_by(ProductReviewRow.created_at.desc())
                )
            ).all()
        return [_to_review(row) for row in rows]

    async def list_for_product(
        self, product_id: UUID, *, include_hidden: bool = False
    ) -> list[ProductReview]:
        async with self._sessions() as session:
            statement = select(ProductReviewRow).where(
                ProductReviewRow.product_id == product_id
            )
            if not include_hidden:
                statement = statement.where(ProductReviewRow.status == "published")
            rows = (
                await session.scalars(
                    statement.order_by(ProductReviewRow.created_at.desc())
                )
            ).all()
        return [_to_review(row) for row in rows]

    async def get(self, review_id: UUID) -> ProductReview:
        async with self._sessions() as session:
            row = await session.get(ProductReviewRow, review_id)
            if row is None:
                raise MasterReviewsError(404, "review_not_found", "Review was not found")
            return _to_review(row)

    async def upsert(self, review: ProductReview) -> None:
        async with self._sessions.begin() as session:
            row = await session.get(ProductReviewRow, review.id)
            if row is None:
                row = ProductReviewRow(id=review.id)
                session.add(row)
            _apply_row(row, review)

    async def create(self, body: ReviewCreate) -> ProductReview:
        review = review_from_create(body)
        await self.upsert(review)
        return review

    async def update(self, review_id: UUID, body: ReviewUpdate) -> ProductReview:
        async with self._sessions.begin() as session:
            row = await session.get(ProductReviewRow, review_id)
            if row is None:
                raise MasterReviewsError(404, "review_not_found", "Review was not found")
            current = _to_review(row)
            updated = apply_review_update(current, body.model_dump(exclude_unset=True))
            _apply_row(row, updated)
            await session.flush()
            return updated

    async def delete(self, review_id: UUID) -> None:
        async with self._sessions.begin() as session:
            row = await session.get(ProductReviewRow, review_id)
            if row is None:
                raise MasterReviewsError(404, "review_not_found", "Review was not found")
            await session.delete(row)
