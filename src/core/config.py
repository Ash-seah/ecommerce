"""Application configuration with fail-fast security validation."""

import re
from functools import lru_cache
from typing import Literal, Self

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "example",
    "insecure",
    "placeholder",
    "replace-with",
)


class Settings(BaseSettings):
    """Validated settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        strict=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "Ecommerce Sandbox API"
    api_port: int = Field(default=8001, ge=1, le=65535)
    # Public reverse-proxy prefix (e.g. /api). Empty for direct local access.
    root_path: str = Field(default="", pattern=r"^$|^/([A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*)$")

    ecommerce_owner_password: SecretStr = Field(min_length=32)
    ecommerce_reader_password: SecretStr = Field(min_length=32)
    database_url: PostgresDsn
    migration_database_url: PostgresDsn

    redis_url: RedisDsn
    redis_key_prefix: str = Field(default="ecommerce", pattern=r"^[a-z][a-z0-9_-]*$")

    minio_endpoint: str = Field(min_length=3)
    minio_access_key: str = Field(min_length=8)
    minio_secret_key: SecretStr = Field(min_length=32)
    minio_secure: bool = False
    minio_master_bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    minio_sandbox_bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    media_public_base_url: AnyHttpUrl | None = None
    media_worker_concurrency: int = Field(default=4, ge=1, le=32)

    session_secret: SecretStr = Field(min_length=32)
    csrf_secret: SecretStr = Field(min_length=32)
    session_ttl_seconds: int = Field(default=7200, ge=300, le=86400)
    session_cookie_name: str = Field(default="ecommerce_session", pattern=r"^[A-Za-z0-9_-]+$")
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "strict"] = "lax"

    cors_origins: list[AnyHttpUrl] = Field(default_factory=list)
    max_request_body_bytes: int = Field(default=12 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    rate_limit_requests: int = Field(default=120, ge=1, le=100_000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    readiness_timeout_seconds: int = Field(default=2, ge=1, le=10)
    trusted_proxy_ips: str = Field(default="127.0.0.1", min_length=1, max_length=500)
    commerce_page_max: int = Field(default=100, ge=1, le=500)
    cart_quantity_max: int = Field(default=20, ge=1, le=1000)
    address_max: int = Field(default=10, ge=1, le=100)
    demo_wallet_initial_minor: int = Field(default=1_000_000, ge=0, le=1_000_000_000)
    demo_wallet_currency: str = Field(default="IRR", pattern=r"^[A-Z]{3}$")
    demo_stock_default: int = Field(default=100, ge=0, le=1_000_000)
    shipping_flat_minor: int = Field(default=500, ge=0, le=1_000_000)
    free_shipping_threshold_minor: int = Field(default=5_000, ge=0, le=1_000_000_000)
    tax_basis_points: int = Field(default=0, ge=0, le=10_000)

    # Master-catalog operator credentials (JWT). Password is stored plainly in .env.
    admin_username: str = Field(default="admin", min_length=1, max_length=64)
    admin_password: str = Field(default="admin123", min_length=1, max_length=128)
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_ttl_seconds: int = Field(default=28800, ge=300, le=86400)

    @field_validator(
        "ecommerce_owner_password",
        "ecommerce_reader_password",
        "minio_access_key",
        "minio_secret_key",
        "session_secret",
        "csrf_secret",
        "jwt_secret",
        mode="before",
    )
    @classmethod
    def reject_placeholder_secrets(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if any(marker in normalized for marker in _PLACEHOLDER_MARKERS):
                raise ValueError("placeholder or insecure secret values are not allowed")
        return value

    @field_validator(
        "api_port",
        "session_ttl_seconds",
        "max_request_body_bytes",
        "max_upload_bytes",
        "rate_limit_requests",
        "rate_limit_window_seconds",
        "readiness_timeout_seconds",
        "media_worker_concurrency",
        "commerce_page_max",
        "cart_quantity_max",
        "address_max",
        "demo_wallet_initial_minor",
        "demo_stock_default",
        "shipping_flat_minor",
        "free_shipping_threshold_minor",
        "tax_basis_points",
        "jwt_ttl_seconds",
        mode="before",
    )
    @classmethod
    def parse_environment_integer(cls, value: object) -> object:
        if isinstance(value, str):
            if not re.fullmatch(r"[0-9]+", value):
                raise ValueError("must be an unsigned base-10 integer")
            return int(value)
        return value

    @field_validator("minio_secure", "session_cookie_secure", mode="before")
    @classmethod
    def parse_environment_boolean(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
            raise ValueError("must be exactly true or false")
        return value

    @model_validator(mode="after")
    def validate_isolation_and_security(self) -> Self:
        runtime_url = str(self.database_url)
        migration_url = str(self.migration_database_url)
        if runtime_url == migration_url:
            raise ValueError("runtime and migration database URLs must be different")

        runtime_hosts = self.database_url.hosts()
        migration_hosts = self.migration_database_url.hosts()
        if len(runtime_hosts) != 1 or len(migration_hosts) != 1:
            raise ValueError("database URLs must specify exactly one host")

        runtime_auth = runtime_hosts[0]
        migration_auth = migration_hosts[0]
        if runtime_auth["username"] != "ecommerce_reader":
            raise ValueError("DATABASE_URL must use the ecommerce_reader role")
        if migration_auth["username"] != "ecommerce_owner":
            raise ValueError("MIGRATION_DATABASE_URL must use the ecommerce_owner role")
        if self.database_url.path != "/ecommerce_master":
            raise ValueError("DATABASE_URL must target ecommerce_master")
        if self.migration_database_url.path != "/ecommerce_master":
            raise ValueError("MIGRATION_DATABASE_URL must target ecommerce_master")

        if runtime_auth["password"] != self.ecommerce_reader_password.get_secret_value():
            raise ValueError("DATABASE_URL password must match ECOMMERCE_READER_PASSWORD")
        if migration_auth["password"] != self.ecommerce_owner_password.get_secret_value():
            raise ValueError("MIGRATION_DATABASE_URL password must match ECOMMERCE_OWNER_PASSWORD")
        if (
            self.ecommerce_reader_password.get_secret_value()
            == self.ecommerce_owner_password.get_secret_value()
        ):
            raise ValueError("runtime and migration roles must use different passwords")

        redis_database = (self.redis_url.path or "").lstrip("/")
        if not redis_database.isdigit() or int(redis_database) == 0:
            raise ValueError("REDIS_URL must select a non-zero database")
        if int(redis_database) != 1:
            raise ValueError("REDIS_URL must select the isolated ecommerce database 1")
        if self.redis_key_prefix != "ecommerce":
            raise ValueError("REDIS_KEY_PREFIX must be ecommerce")

        if self.minio_master_bucket == self.minio_sandbox_bucket:
            raise ValueError("master and sandbox media must use separate MinIO buckets")
        if self.session_secret.get_secret_value() == self.csrf_secret.get_secret_value():
            raise ValueError("session and CSRF secrets must be different")
        if self.jwt_secret.get_secret_value() in {
            self.session_secret.get_secret_value(),
            self.csrf_secret.get_secret_value(),
        }:
            raise ValueError("JWT secret must differ from session and CSRF secrets")
        if self.environment == "production" and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE must be true in production")
        if self.environment == "production" and "*" in self.trusted_proxy_ips:
            raise ValueError("TRUSTED_PROXY_IPS cannot contain a wildcard in production")
        if self.max_request_body_bytes < self.max_upload_bytes:
            raise ValueError("MAX_REQUEST_BODY_BYTES must be at least MAX_UPLOAD_BYTES")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the single validated settings instance for this process."""

    return Settings()  # type: ignore[call-arg]
