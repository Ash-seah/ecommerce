"""JWT operator endpoints for RAG reindex and streaming chat."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.assistant.groq import GroqError
from src.assistant.schemas import AssistantChatRequest, AssistantHealth, AssistantReindexResponse
from src.assistant.service import AssistantError, AssistantService
from src.master.router import AdminUser

router = APIRouter(prefix="/v1/master/assistant", tags=["master-assistant"])


def _service(request: Request) -> AssistantService:
    service: AssistantService = request.app.state.assistant_service
    return service


def _sse(event: str, payload: object) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.get("/health", response_model=AssistantHealth)
async def master_assistant_health(request: Request, _admin: AdminUser) -> AssistantHealth:
    body = await _service(request).health()
    return AssistantHealth.model_validate(body)


@router.post("/reindex", response_model=AssistantReindexResponse)
async def reindex(request: Request, _admin: AdminUser) -> AssistantReindexResponse:
    stats = await _service(request).reindex()
    return AssistantReindexResponse.model_validate(stats)


@router.post("/chat")
async def master_assistant_chat(
    body: AssistantChatRequest,
    request: Request,
    _admin: AdminUser,
) -> StreamingResponse:
    history = [{"role": item.role, "content": item.content} for item in body.history]

    async def events() -> AsyncIterator[str]:
        try:
            async for kind, payload in _service(request).stream_answer(
                question=body.message,
                history=history,
                product_id=body.product_id,
            ):
                yield _sse(kind, payload)
            yield _sse("done", {"ok": True})
        except AssistantError as exc:
            yield _sse("error", {"code": exc.code, "detail": exc.message})
        except GroqError as exc:
            yield _sse("error", {"code": exc.code, "detail": exc.message})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
