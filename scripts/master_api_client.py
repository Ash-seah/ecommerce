"""Stdlib HTTP client for JWT master endpoints (no project venv required)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import UUID


class MasterApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"master API {status}: {body}")
        self.status = status
        self.body = body


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values


def env(name: str, dotenv: dict[str, str], default: str | None = None) -> str:
    value = os.environ.get(name) or dotenv.get(name) or default
    if value is None or value == "":
        raise MasterApiError(500, f"missing required setting {name}")
    return value


def load_repo_dotenv() -> dict[str, str]:
    repo_root = Path(__file__).resolve().parents[1]
    return {**load_dotenv(repo_root / ".env"), **load_dotenv(Path.cwd() / ".env")}


def resolve_base_url(dotenv: dict[str, str], base_url: str | None = None) -> str:
    api_port = env("API_PORT", dotenv, "8001")
    return (
        base_url or os.environ.get("MASTER_API_BASE_URL") or f"http://127.0.0.1:{api_port}"
    ).rstrip("/")


class MasterApiClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._token = token

    def login(self, username: str, password: str) -> str:
        payload = self._request(
            "POST",
            "/v1/master/auth/login",
            {"username": username, "password": password},
            auth=False,
        )
        token = payload["access_token"]
        if not isinstance(token, str) or not token:
            raise MasterApiError(500, "login response missing access_token")
        self._token = token
        return token

    def create_category(
        self,
        *,
        name: str,
        description: str,
        sort_order: int,
        parent_id: UUID | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "description": description,
            "sort_order": sort_order,
            "is_active": True,
        }
        if parent_id is not None:
            body["parent_id"] = str(parent_id)
        return self._request("POST", "/v1/master/categories", body)["category"]

    def update_category(self, category_id: UUID, **fields: Any) -> dict[str, Any]:
        return self._request("PATCH", f"/v1/master/categories/{category_id}", fields)["category"]

    def delete_category(self, category_id: UUID) -> None:
        self._request("DELETE", f"/v1/master/categories/{category_id}", None)

    def create_product(self, *, category_id: UUID, name: str, description: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/master/products",
            {
                "category_id": str(category_id),
                "name": name,
                "description": description,
                "is_active": True,
            },
        )["product"]

    def update_product(self, product_id: UUID, **fields: Any) -> dict[str, Any]:
        body = {
            key: (str(value) if isinstance(value, UUID) else value)
            for key, value in fields.items()
        }
        return self._request("PATCH", f"/v1/master/products/{product_id}", body)["product"]

    def delete_product(self, product_id: UUID) -> None:
        self._request("DELETE", f"/v1/master/products/{product_id}", None)

    def create_variant(
        self,
        *,
        product_id: UUID,
        name: str,
        price_minor: int,
        currency: str = "IRR",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/master/variants",
            {
                "product_id": str(product_id),
                "name": name,
                "price_minor": price_minor,
                "currency": currency,
                "is_active": True,
            },
        )["variant"]

    def update_variant(self, variant_id: UUID, **fields: Any) -> dict[str, Any]:
        return self._request("PATCH", f"/v1/master/variants/{variant_id}", fields)["variant"]

    def delete_variant(self, variant_id: UUID) -> None:
        self._request("DELETE", f"/v1/master/variants/{variant_id}", None)

    def publish(self) -> dict[str, Any]:
        return self._request("POST", "/v1/master/catalog/publish", {})

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        *,
        auth: bool = True,
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if auth:
            if not self._token:
                raise MasterApiError(401, "not logged in")
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            f"{self._base}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MasterApiError(exc.code, detail) from exc
        if not raw:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise MasterApiError(500, f"expected JSON object, got {type(payload).__name__}")
        return payload


def login_from_env(base_url: str | None = None) -> tuple[MasterApiClient, str]:
    dotenv = load_repo_dotenv()
    resolved = resolve_base_url(dotenv, base_url)
    username = env("ADMIN_USERNAME", dotenv, "admin")
    password = env("ADMIN_PASSWORD", dotenv, "admin123")
    client = MasterApiClient(resolved)
    client.login(username, password)
    return client, resolved
