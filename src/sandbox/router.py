"""Thin v1 HTTP surface for sandbox lifecycle operations."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict

from src.catalog.schemas import CatalogSnapshot
from src.core.config import Settings
from src.sandbox.models import SandboxState
from src.sandbox.security import InvalidOriginError, SessionSecrets, normalize_origin
from src.sandbox.service import (
    CatalogUnavailableError,
    MutationConflictError,
    SandboxNotFoundError,
    SandboxService,
)

router = APIRouter(prefix="/v1/sandbox", tags=["sandbox"])


class SandboxResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: SandboxState
    csrf_token: str | None = None


class SandboxAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SessionContext:
    session_id: str
    safe_id: str
    state: SandboxState


def _service(request: Request) -> SandboxService:
    service: SandboxService = request.app.state.sandbox_service
    return service


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _secrets(request: Request) -> SessionSecrets:
    secrets: SessionSecrets = request.app.state.session_secrets
    return secrets


def _allowed_origins(request: Request) -> list[str]:
    return [normalize_origin(str(item)) for item in _settings(request).cors_origins]


def _origin_from_host(request: Request, allowed: list[str]) -> str | None:
    """Resolve CSRF origin for Swagger/curl when the Origin header is omitted."""
    host_header = request.headers.get("host")
    if host_header is None:
        return None
    host = host_header.split(",", 1)[0].strip().lower()
    hostname = host.split(":", 1)[0]
    matches = [
        origin
        for origin in allowed
        if (urlsplit(origin).hostname or "").lower() == hostname
        or urlsplit(origin).netloc.lower() == host
    ]
    if not matches:
        return None
    https = [origin for origin in matches if origin.startswith("https://")]
    return (https or matches)[0]


def _request_origin(request: Request) -> str:
    allowed = _allowed_origins(request)
    value = request.headers.get("origin")
    if value is None:
        referer = request.headers.get("referer")
        if referer:
            parts = urlsplit(referer)
            if parts.scheme in {"http", "https"} and parts.hostname:
                value = f"{parts.scheme}://{parts.netloc}"
    if value is None:
        inferred = _origin_from_host(request, allowed)
        if inferred is None:
            raise SandboxAPIError(
                400,
                "origin_required",
                "Origin header is required unless Host matches an allowed CORS origin",
            )
        return inferred
    try:
        origin = normalize_origin(value)
    except InvalidOriginError as exc:
        raise SandboxAPIError(400, "invalid_origin", str(exc)) from exc
    if origin not in set(allowed):
        raise SandboxAPIError(403, "origin_denied", "Origin is not allowed")
    return origin


def _set_session_cookie(response: Response, session_id: str, settings: Settings) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=settings.session_ttl_seconds,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )


async def _existing_context(request: Request) -> SessionContext:
    session_id = request.cookies.get(_settings(request).session_cookie_name)
    if session_id is None:
        raise SandboxAPIError(401, "session_required", "Sandbox session cookie is required")
    service = _service(request)
    try:
        state = await service.inspect(session_id)
    except SandboxNotFoundError as exc:
        raise SandboxAPIError(401, "invalid_session", "Sandbox session is invalid") from exc
    return SessionContext(session_id, service.safe_id(session_id), state)


async def _require_csrf(request: Request, x_csrf_token: str | None) -> SessionContext:
    context = await _existing_context(request)
    origin = _request_origin(request)
    if x_csrf_token is None or not _secrets(request).verify_csrf(
        x_csrf_token,
        context.safe_id,
        origin,
        context.state.csrf_nonce_hash,
    ):
        raise SandboxAPIError(403, "csrf_failed", "CSRF validation failed")
    return context


async def _new_session(request: Request, response: Response) -> tuple[str, SandboxResponse]:
    origin = _request_origin(request)
    try:
        session_id, nonce, state = await _service(request).create()
    except CatalogUnavailableError as exc:
        raise SandboxAPIError(503, "catalog_unavailable", str(exc)) from exc
    _set_session_cookie(response, session_id, _settings(request))
    token = _secrets(request).issue_csrf(_service(request).safe_id(session_id), origin, nonce)
    return session_id, SandboxResponse(state=state, csrf_token=token)


async def _create(request: Request, response: Response) -> SandboxResponse:
    _session_id, result = await _new_session(request, response)
    return result


@router.get("/session/create", response_model=SandboxResponse)
async def create_session(request: Request, response: Response) -> SandboxResponse:
    """Create explicitly; GET avoids an unprotected mutating API request."""
    return await _create(request, response)


@router.get("/session", response_model=SandboxResponse)
async def inspect_session(request: Request, response: Response) -> SandboxResponse:
    session_id = request.cookies.get(_settings(request).session_cookie_name)
    if session_id is None:
        return await _create(request, response)
    try:
        state = await _service(request).inspect(session_id)
    except SandboxNotFoundError:
        return await _create(request, response)
    _set_session_cookie(response, session_id, _settings(request))
    return SandboxResponse(state=state)


@router.post("/session/refresh", response_model=SandboxResponse)
async def refresh_session(
    request: Request,
    response: Response,
    x_csrf_token: str | None = Header(default=None),
) -> SandboxResponse:
    context = await _require_csrf(request, x_csrf_token)
    try:
        state = await _service(request).refresh(context.session_id)
    except SandboxNotFoundError as exc:
        raise SandboxAPIError(401, "invalid_session", "Sandbox session is invalid") from exc
    _set_session_cookie(response, context.session_id, _settings(request))
    return SandboxResponse(state=state)


@router.post("/session/reset", response_model=SandboxResponse)
async def reset_session(
    request: Request,
    response: Response,
    x_csrf_token: str | None = Header(default=None),
) -> SandboxResponse:
    context = await _require_csrf(request, x_csrf_token)
    try:
        nonce, state = await _service(request).reset(context.session_id)
    except MutationConflictError as exc:
        raise SandboxAPIError(409, "mutation_conflict", str(exc)) from exc
    except SandboxNotFoundError as exc:
        raise SandboxAPIError(401, "invalid_session", "Sandbox session is invalid") from exc
    token = _secrets(request).issue_csrf(context.safe_id, _request_origin(request), nonce)
    _set_session_cookie(response, context.session_id, _settings(request))
    return SandboxResponse(state=state, csrf_token=token)


@router.post("/session/csrf", response_model=SandboxResponse)
async def rotate_csrf(
    request: Request,
    response: Response,
    x_csrf_token: str | None = Header(default=None),
) -> SandboxResponse:
    context = await _require_csrf(request, x_csrf_token)
    try:
        nonce, state = await _service(request).rotate_csrf(context.session_id)
    except MutationConflictError as exc:
        raise SandboxAPIError(409, "mutation_conflict", str(exc)) from exc
    except SandboxNotFoundError as exc:
        raise SandboxAPIError(401, "invalid_session", "Sandbox session is invalid") from exc
    token = _secrets(request).issue_csrf(context.safe_id, _request_origin(request), nonce)
    _set_session_cookie(response, context.session_id, _settings(request))
    return SandboxResponse(state=state, csrf_token=token)


@router.get("/catalog", response_model=CatalogSnapshot)
async def get_catalog(request: Request, response: Response) -> CatalogSnapshot:
    session_id = request.cookies.get(_settings(request).session_cookie_name)
    if session_id is None:
        session_id, _created = await _new_session(request, response)
    try:
        catalog = await _service(request).merged_catalog(session_id)
        _set_session_cookie(response, session_id, _settings(request))
        return catalog
    except SandboxNotFoundError:
        session_id, _created = await _new_session(request, response)
        return await _service(request).merged_catalog(session_id)
    except CatalogUnavailableError as exc:
        raise SandboxAPIError(503, "catalog_unavailable", str(exc)) from exc
