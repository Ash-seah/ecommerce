"""HTTP surface for sandbox product-review administration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from src.reviews.schemas import (
    ReviewCreate,
    ReviewList,
    ReviewResponse,
    ReviewUpdate,
)
from src.reviews.service import SandboxReviewsService
from src.sandbox.router import SessionContext, _existing_context, _require_csrf

router = APIRouter(prefix="/v1/admin/reviews", tags=["admin-reviews"])


def _service(request: Request) -> SandboxReviewsService:
    service: SandboxReviewsService = request.app.state.sandbox_reviews_service
    return service


async def _write(request: Request, token: str | None) -> SessionContext:
    return await _require_csrf(request, token)


async def _read(request: Request) -> SessionContext:
    return await _existing_context(request)


@router.get("", response_model=ReviewList)
async def list_reviews(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    product_id: UUID | None = None,
    status: str | None = None,
) -> ReviewList:
    context = await _read(request)
    return await _service(request).list_reviews(
        context.session_id,
        page=page,
        page_size=page_size,
        product_id=product_id,
        status=status,
    )


@router.get("/{review_id}", response_model=ReviewResponse)
async def get_review(review_id: UUID, request: Request) -> ReviewResponse:
    context = await _read(request)
    return ReviewResponse(review=await _service(request).get(context.session_id, review_id))


@router.post("", response_model=ReviewResponse, status_code=201)
async def create_review(
    body: ReviewCreate,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ReviewResponse:
    context = await _write(request, x_csrf_token)
    return ReviewResponse(review=await _service(request).create(context.session_id, body))


@router.patch("/{review_id}", response_model=ReviewResponse)
async def update_review(
    review_id: UUID,
    body: ReviewUpdate,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ReviewResponse:
    context = await _write(request, x_csrf_token)
    return ReviewResponse(
        review=await _service(request).update(context.session_id, review_id, body)
    )


@router.delete("/{review_id}", status_code=204)
async def delete_review(
    review_id: UUID,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> None:
    context = await _write(request, x_csrf_token)
    await _service(request).delete(context.session_id, review_id)
