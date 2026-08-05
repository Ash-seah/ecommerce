"""RFC 9457 problem details shared by all HTTP error paths."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"


def request_instance(request: Request) -> str:
    request_id = getattr(request.state, "request_id", "unknown")
    return f"urn:request:{request_id}"


def problem(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {
        "type": f"https://ecommerce.example/problems/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request_instance(request),
        "code": code,
    }
    if errors is not None:
        content["errors"] = errors
    return JSONResponse(
        status_code=status,
        content=content,
        headers=headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def install_exception_handlers(application: FastAPI) -> None:
    """Install consistent application, framework, validation, and fallback handlers."""

    from src.admin.service import AdminError
    from src.commerce.service import CommerceError
    from src.infrastructure.minio import MediaError
    from src.sandbox.router import SandboxAPIError

    error_types = (SandboxAPIError, CommerceError, AdminError, MediaError)

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "location": [str(part) for part in item["loc"]],
                "message": item["msg"],
                "code": item["type"],
            }
            for item in exc.errors()
        ]
        return problem(
            request,
            status=422,
            code="validation_error",
            title="Request validation failed",
            detail="One or more request values are invalid.",
            errors=errors,
        )

    @application.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = (
            exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
        )
        return problem(
            request,
            status=exc.status_code,
            code="http_error",
            title="HTTP request failed",
            detail=detail,
            headers=dict(exc.headers) if exc.headers is not None else None,
        )

    async def application_error_handler(request: Request, exc: Any) -> JSONResponse:
        status_code = int(exc.status_code)
        code = str(exc.code)
        message = str(exc.message)
        return problem(
            request,
            status=status_code,
            code=code,
            title=code.replace("_", " ").title(),
            detail=message,
        )

    for error_type in error_types:
        application.add_exception_handler(error_type, application_error_handler)

    @application.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        import logging

        logging.getLogger("ecommerce.errors").exception(
            "unhandled_exception request_id=%s path=%s",
            getattr(request.state, "request_id", "unknown"),
            request.url.path,
            exc_info=exc,
        )
        return problem(
            request,
            status=500,
            code="internal_error",
            title="Internal server error",
            detail="An unexpected error occurred.",
        )
