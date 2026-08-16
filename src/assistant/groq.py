"""Remote Groq OpenAI-compatible embeddings and chat streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx


class GroqError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


class GroqClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        chat_model: str,
        embed_model: str,
        timeout_seconds: float = 45.0,
    ) -> None:
        self._chat_model = chat_model
        self._embed_model = embed_model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.post(
            "/embeddings",
            json={"model": self._embed_model, "input": texts, "encoding_format": "float"},
        )
        if response.status_code >= 400:
            raise GroqError(
                502,
                "embed_failed",
                f"Groq embeddings failed with HTTP {response.status_code}",
            )
        payload = response.json()
        rows = sorted(payload.get("data") or [], key=lambda item: int(item.get("index", 0)))
        vectors = [list(map(float, item["embedding"])) for item in rows]
        if len(vectors) != len(texts):
            raise GroqError(502, "embed_failed", "Groq returned an unexpected embedding count")
        return vectors

    async def stream_chat(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        async with self._client.stream(
            "POST",
            "/chat/completions",
            json={
                "model": self._chat_model,
                "messages": messages,
                "stream": True,
                "temperature": 0.2,
            },
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                raise GroqError(
                    502,
                    "chat_failed",
                    f"Groq chat failed with HTTP {response.status_code}",
                )
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = payload.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if isinstance(delta, str) and delta:
                    yield delta
