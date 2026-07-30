"""Opaque session identifiers and origin-bound CSRF tokens."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit


class InvalidOriginError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SessionSecrets:
    session_secret: bytes
    csrf_secret: bytes

    def new_session_id(self) -> str:
        return secrets.token_urlsafe(32)

    def session_hash(self, session_id: str) -> str:
        return hmac.new(self.session_secret, session_id.encode(), hashlib.sha256).hexdigest()

    def new_csrf_nonce(self) -> str:
        return secrets.token_urlsafe(32)

    def csrf_nonce_hash(self, nonce: str) -> str:
        return hmac.new(self.csrf_secret, nonce.encode(), hashlib.sha256).hexdigest()

    def issue_csrf(self, safe_session_id: str, origin: str, nonce: str) -> str:
        normalized = normalize_origin(origin)
        message = f"{safe_session_id}\0{normalized}\0{nonce}".encode()
        signature = hmac.new(self.csrf_secret, message, hashlib.sha256).hexdigest()
        return f"{nonce}.{signature}"

    def verify_csrf(
        self,
        token: str,
        safe_session_id: str,
        origin: str,
        expected_nonce_hash: str,
    ) -> bool:
        try:
            nonce, signature = token.split(".", 1)
            expected_token = self.issue_csrf(safe_session_id, origin, nonce)
        except (InvalidOriginError, ValueError):
            return False
        expected_signature = expected_token.rsplit(".", 1)[1]
        return hmac.compare_digest(signature, expected_signature) and hmac.compare_digest(
            self.csrf_nonce_hash(nonce), expected_nonce_hash
        )


def normalize_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidOriginError("a valid http(s) Origin header is required")
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise InvalidOriginError("Origin header is malformed")
    host = parsed.hostname.lower()
    default_port = 80 if parsed.scheme == "http" else 443
    port = parsed.port
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"
