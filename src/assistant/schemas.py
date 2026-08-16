"""HTTP contracts for the Groq RAG assistant."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AssistantModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatMessage(AssistantModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AssistantChatRequest(AssistantModel):
    """Ask a question. History and product_id are optional."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"message": "What is my bestselling product?"},
                {
                    "message": "Tell me about this shoe",
                    "product_id": "00000000-0000-4000-8000-0000000000aa",
                },
            ]
        },
    )

    message: str = Field(min_length=1, max_length=4000)
    history: tuple[ChatMessage, ...] = Field(
        default=(),
        description="Optional prior turns. Omit for a single-shot question.",
    )
    product_id: UUID | None = Field(
        default=None,
        description="Optional product focus. Omit for catalog-wide / analytics questions.",
    )


class AssistantHealth(AssistantModel):
    enabled: bool
    groq_configured: bool
    indexed_chunks: int
    pgvector: bool


class AssistantReindexResponse(AssistantModel):
    documents: int
    written: int
    skipped: int
