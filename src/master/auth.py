"""JWT issuance and verification for master-catalog operators."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from src.core.config import Settings


class MasterAuthError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def issue_access_token(settings: Settings, subject: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": "master_admin",
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_ttl_seconds),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
        )
    except jwt.PyJWTError as exc:
        raise MasterAuthError(401, "invalid_token", "Access token is invalid or expired") from exc
    if payload.get("role") != "master_admin" or not isinstance(payload.get("sub"), str):
        raise MasterAuthError(401, "invalid_token", "Access token is invalid or expired")
    return payload


def authenticate_password(settings: Settings, username: str, password: str) -> str:
    if username != settings.admin_username or password != settings.admin_password:
        raise MasterAuthError(401, "invalid_credentials", "Username or password is incorrect")
    return username
