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
    message: str = Field(min_length=1, max_length=4000)
    history: tuple[ChatMessage, ...] = ()
    product_id: UUID | None = None


class AssistantHealth(AssistantModel):
    enabled: bool
    groq_configured: bool
    indexed_chunks: int
    pgvector: bool


class AssistantReindexResponse(AssistantModel):
    documents: int
    written: int
    skipped: int
