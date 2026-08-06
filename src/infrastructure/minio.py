"""Bounded asynchronous access to the synchronous MinIO SDK."""

import asyncio
from collections.abc import Iterable
from datetime import timedelta
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

from src.catalog.schemas import MediaSnapshot


class MediaError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class MinioProtocol(Protocol):
    def bucket_exists(self, bucket_name: str) -> bool: ...

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BytesIO,
        length: int,
        *,
        content_type: str,
    ) -> object: ...

    def remove_object(self, bucket_name: str, object_name: str) -> object: ...

    def list_objects(self, bucket_name: str, *, prefix: str, recursive: bool) -> Iterable[Any]: ...

    def presigned_get_object(
        self, bucket_name: str, object_name: str, *, expires: timedelta
    ) -> str: ...


_IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


def detect_image_type(data: bytes) -> str | None:
    """Detect supported image formats from signatures, never filenames."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class MediaService:
    def __init__(
        self,
        client: MinioProtocol,
        *,
        master_bucket: str,
        sandbox_bucket: str,
        max_upload_bytes: int,
        media_base_url: str | None = None,
        max_concurrency: int = 4,
    ) -> None:
        if master_bucket == sandbox_bucket:
            raise ValueError("master and sandbox media buckets must differ")
        self._client = client
        self.master_bucket = master_bucket
        self.sandbox_bucket = sandbox_bucket
        self.max_upload_bytes = max_upload_bytes
        self._base_url = media_base_url.rstrip("/") if media_base_url else None
        self._semaphore = asyncio.Semaphore(max_concurrency)

    @staticmethod
    def prefix(safe_id: str) -> str:
        return f"sandboxes/{safe_id}/"

    async def _offload[T](self, operation: Any, /, *args: Any, **kwargs: Any) -> T:
        async with self._semaphore:
            return await asyncio.to_thread(operation, *args, **kwargs)

    async def url(self, object_key: str, *, master: bool = False) -> str:
        """Return a browser-reachable object URL.

        Prefer MEDIA_PUBLIC_BASE_URL (unsigned public/gateway URL). Presigned
        fallbacks use the MinIO client endpoint and are only useful when that
        hostname is reachable by the browser.
        """
        bucket_name = self.master_bucket if master else self.sandbox_bucket
        if self._base_url is not None:
            bucket = quote(bucket_name, safe="")
            key = quote(object_key, safe="/")
            return f"{self._base_url}/{bucket}/{key}"
        return await self._offload(
            self._client.presigned_get_object,
            bucket_name,
            object_key,
            expires=timedelta(hours=1),
        )

    async def ping(self) -> bool:
        master: bool = await self._offload(self._client.bucket_exists, self.master_bucket)
        sandbox: bool = await self._offload(self._client.bucket_exists, self.sandbox_bucket)
        return bool(master and sandbox)

    async def upload(
        self,
        safe_id: str,
        data: bytes,
        declared_content_type: str | None,
        alt_text: str,
        sort_order: int,
        *,
        is_main: bool = False,
    ) -> MediaSnapshot:
        if len(data) > self.max_upload_bytes:
            raise MediaError(413, "file_too_large", "Upload exceeds the configured size limit")
        if not data:
            raise MediaError(422, "empty_file", "Upload cannot be empty")
        detected = detect_image_type(data)
        if detected is None:
            raise MediaError(422, "unsupported_media", "File signature is not PNG, JPEG, or WebP")
        declared = (declared_content_type or "").split(";", 1)[0].strip().lower()
        if declared not in _IMAGE_TYPES:
            raise MediaError(422, "unsupported_media_type", "Declared media type is not allowed")
        if declared != detected:
            raise MediaError(
                422,
                "media_type_mismatch",
                "Declared type does not match file signature",
            )
        media_id = uuid4()
        object_key = f"{self.prefix(safe_id)}{uuid4().hex}.{_IMAGE_TYPES[detected]}"
        await self._offload(
            self._client.put_object,
            self.sandbox_bucket,
            object_key,
            BytesIO(data),
            len(data),
            content_type=detected,
        )
        return MediaSnapshot(
            id=media_id,
            object_key=object_key,
            content_type=detected,
            alt_text=alt_text,
            byte_size=len(data),
            sort_order=sort_order,
            is_main=is_main,
            url=await self.url(object_key),
        )

    async def upload_master(
        self,
        data: bytes,
        declared_content_type: str | None,
        alt_text: str,
        sort_order: int,
        *,
        object_prefix: str = "catalog/",
        is_main: bool = False,
    ) -> MediaSnapshot:
        if len(data) > self.max_upload_bytes:
            raise MediaError(413, "file_too_large", "Upload exceeds the configured size limit")
        if not data:
            raise MediaError(422, "empty_file", "Upload cannot be empty")
        detected = detect_image_type(data)
        if detected is None:
            raise MediaError(422, "unsupported_media", "File signature is not PNG, JPEG, or WebP")
        declared = (declared_content_type or "").split(";", 1)[0].strip().lower()
        if declared not in _IMAGE_TYPES:
            raise MediaError(422, "unsupported_media_type", "Declared media type is not allowed")
        if declared != detected:
            raise MediaError(
                422,
                "media_type_mismatch",
                "Declared type does not match file signature",
            )
        prefix = object_prefix if object_prefix.endswith("/") else f"{object_prefix}/"
        media_id = uuid4()
        object_key = f"{prefix}{uuid4().hex}.{_IMAGE_TYPES[detected]}"
        await self._offload(
            self._client.put_object,
            self.master_bucket,
            object_key,
            BytesIO(data),
            len(data),
            content_type=detected,
        )
        return MediaSnapshot(
            id=media_id,
            object_key=object_key,
            content_type=detected,
            alt_text=alt_text,
            byte_size=len(data),
            sort_order=sort_order,
            is_main=is_main,
            url=await self.url(object_key, master=True),
        )

    async def delete_master(self, object_key: str) -> None:
        if not object_key.startswith("catalog/"):
            raise MediaError(403, "media_not_master", "Only catalog/ master objects can be deleted")
        await self._offload(self._client.remove_object, self.master_bucket, object_key)

    async def delete(self, safe_id: str, object_key: str) -> None:
        if not object_key.startswith(self.prefix(safe_id)):
            raise MediaError(403, "media_not_owned", "Media is not owned by this sandbox")
        await self._offload(self._client.remove_object, self.sandbox_bucket, object_key)

    async def cleanup(self, safe_id: str, _state: object | None = None) -> None:
        prefix = self.prefix(safe_id)
        objects: list[Any] = await self._offload(
            lambda: list(
                self._client.list_objects(self.sandbox_bucket, prefix=prefix, recursive=True)
            )
        )
        for item in objects:
            object_name = getattr(item, "object_name", None)
            if isinstance(object_name, str) and object_name.startswith(prefix):
                await self._offload(self._client.remove_object, self.sandbox_bucket, object_name)
