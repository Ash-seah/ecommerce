"""JWT-protected master product-review administration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from src.master.router import AdminUser
from src.reviews.schemas import (
    ReviewCreate,
    ReviewList,
    ReviewResponse,
    ReviewUpdate,
)
from src.reviews.service import MasterReviewsService

router = APIRouter(prefix="/v1/master/reviews", tags=["master-reviews"])


def _service(request: Request) -> MasterReviewsService:
    service: MasterReviewsService = request.app.state.master_reviews_service
    return service


@router.get("", response_model=ReviewList)
async def list_reviews(
    request: Request,
    _admin: AdminUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    product_id: UUID | None = None,
    status: str | None = None,
) -> ReviewList:
    return await _service(request).list_reviews(
        page=page,
        page_size=page_size,
        product_id=product_id,
        status=status,
    )


@router.get("/{review_id}", response_model=ReviewResponse)
async def get_review(review_id: UUID, request: Request, _admin: AdminUser) -> ReviewResponse:
    return ReviewResponse(review=await _service(request).get(review_id))


@router.post("", response_model=ReviewResponse, status_code=201)
async def create_review(
    body: ReviewCreate, request: Request, _admin: AdminUser
) -> ReviewResponse:
    return ReviewResponse(review=await _service(request).create(body))


@router.patch("/{review_id}", response_model=ReviewResponse)
async def update_review(
    review_id: UUID, body: ReviewUpdate, request: Request, _admin: AdminUser
) -> ReviewResponse:
    return ReviewResponse(review=await _service(request).update(review_id, body))


@router.delete("/{review_id}", status_code=204)
async def delete_review(review_id: UUID, request: Request, _admin: AdminUser) -> None:
    await _service(request).delete(review_id)
