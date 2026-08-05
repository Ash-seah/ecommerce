"""Request hardening, structured access logging, metrics, and rate limiting."""

import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.errors import problem

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_logger = logging.getLogger("ecommerce.requests")
_logger.setLevel(logging.INFO)

REQUESTS = Counter(
    "ecommerce_http_requests_total",
    "Completed HTTP requests.",
    ("method", "route", "status"),
)
LATENCY = Histogram(
    "ecommerce_http_request_duration_seconds",
    "HTTP request latency.",
    ("method", "route"),
)


def safe_request_id(value: str | None) -> str:
    """Propagate a bounded, log-safe identifier or generate a random one."""
    if value is not None and _REQUEST_ID.fullmatch(value):
        return value
    return uuid4().hex


def _route(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


_DOCS_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})


def _security_headers(request: Request, response: Response) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    # Swagger/ReDoc load CDN assets and an inline bootstrap script.
    if request.url.path in _DOCS_PATHS or request.url.path.startswith(("/docs/", "/redoc/")):
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'none'; "
                "base-uri 'none'; "
                "frame-ancestors 'none'; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "connect-src 'self' https://cdn.jsdelivr.net; "
                "font-src 'self' data: https://cdn.jsdelivr.net"
            ),
        )
        return
    response.headers.setdefault(
        "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
    )


class RequestMiddleware(BaseHTTPMiddleware):
    """Apply controls without recording request headers, cookies, bodies, or tokens."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        request_id = safe_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        settings = request.app.state.settings

        response: Response
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = -1
            if declared_size < 0:
                response = problem(
                    request,
                    status=400,
                    code="invalid_content_length",
                    title="Invalid content length",
                    detail="Content-Length must be a non-negative integer.",
                )
                return self._finish(request, response, started, request_id)
            if declared_size > settings.max_request_body_bytes:
                response = problem(
                    request,
                    status=413,
                    code="request_too_large",
                    title="Request body too large",
                    detail="The request body exceeds the configured limit.",
                )
                return self._finish(request, response, started, request_id)

        if request.method in {"POST", "PUT", "PATCH"} and content_length is None:
            chunks: list[bytes] = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_request_body_bytes:
                    response = problem(
                        request,
                        status=413,
                        code="request_too_large",
                        title="Request body too large",
                        detail="The request body exceeds the configured limit.",
                    )
                    return self._finish(request, response, started, request_id)
                chunks.append(chunk)
            request._body = b"".join(chunks)

        if request.url.path.startswith("/v1/"):
            client = request.client.host if request.client else "unknown"
            digest = hashlib.sha256(client.encode()).hexdigest()[:32]
            key = f"{settings.redis_key_prefix}:rate:{digest}"
            try:
                count, retry_after = await request.app.state.redis.rate_limit(
                    key, settings.rate_limit_window_seconds
                )
            except Exception:
                _logger.error(
                    json.dumps(
                        {
                            "event": "rate_limit_unavailable",
                            "request_id": request_id,
                            "method": request.method,
                            "path": request.url.path,
                        },
                        separators=(",", ":"),
                    )
                )
                response = problem(
                    request,
                    status=503,
                    code="rate_limiter_unavailable",
                    title="Service unavailable",
                    detail="Request admission is temporarily unavailable.",
                )
                return self._finish(request, response, started, request_id)
            if count > settings.rate_limit_requests:
                response = problem(
                    request,
                    status=429,
                    code="rate_limit_exceeded",
                    title="Too many requests",
                    detail="The request rate limit was exceeded.",
                    headers={"Retry-After": str(retry_after)},
                )
                return self._finish(request, response, started, request_id)

        try:
            response = await call_next(request)
        except Exception:
            _logger.error(
                json.dumps(
                    {
                        "event": "unhandled_request_error",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                    },
                    separators=(",", ":"),
                )
            )
            response = problem(
                request,
                status=500,
                code="internal_error",
                title="Internal server error",
                detail="An unexpected error occurred.",
            )
        return self._finish(request, response, started, request_id)

    @staticmethod
    def _finish(request: Request, response: Response, started: float, request_id: str) -> Response:
        duration = time.perf_counter() - started
        route = _route(request)
        response.headers["X-Request-ID"] = request_id
        _security_headers(request, response)
        REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        LATENCY.labels(request.method, route).observe(duration)
        _logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "route": route,
                    "status": response.status_code,
                    "duration_ms": round(duration * 1000, 3),
                },
                separators=(",", ":"),
            )
        )
        return response
