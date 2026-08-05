import json
from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import BaseModel

from src.core.config import get_settings
from src.main import create_app


class Probe:
    def __init__(self, result: bool | Exception = True) -> None:
        self.result = result

    async def ping(self) -> bool:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class RateProbe(Probe):
    def __init__(self, count: int = 1) -> None:
        super().__init__()
        self.count = count

    async def rate_limit(self, _key: str, _window: int) -> tuple[int, int]:
        return self.count, 30


class Payload(BaseModel):
    name: str


def hardened_app() -> object:
    settings = get_settings().model_copy(
        update={
            "max_request_body_bytes": 1024,
            "max_upload_bytes": 1024,
            "rate_limit_requests": 2,
        }
    )
    app = create_app(settings)
    app.state.redis = RateProbe()

    @app.post("/v1/test-validation", include_in_schema=False)
    async def validation_endpoint(body: Payload) -> Payload:
        return body

    @app.get("/test-unhandled", include_in_schema=False)
    async def unhandled_endpoint() -> None:
        raise RuntimeError("secret backend detail")

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = hardened_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as value:
        yield value


@pytest.mark.asyncio
async def test_request_ids_security_headers_and_structured_safe_log(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "safe-id_1"})
    assert response.headers["x-request-id"] == "safe-id_1"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"].startswith("default-src")

    generated = await client.get(
        "/health/live",
        headers={
            "X-Request-ID": "bad id\nvalue",
            "Cookie": "ecommerce_session=never-log-me",
            "X-CSRF-Token": "never-log-csrf",
        },
    )
    assert generated.headers["x-request-id"] != "bad id\nvalue"
    records = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "ecommerce.requests" and record.message.startswith("{")
    ]
    assert records[-1]["route"] == "/health/live"
    serialized = json.dumps(records)
    assert "never-log-me" not in serialized
    assert "never-log-csrf" not in serialized


@pytest.mark.asyncio
async def test_docs_csp_allows_swagger_cdn(client: httpx.AsyncClient) -> None:
    docs = await client.get("/docs")
    assert docs.status_code == 200
    policy = docs.headers["content-security-policy"]
    assert "cdn.jsdelivr.net" in policy
    assert "unsafe-inline" in policy
    assert "connect-src 'self' https://cdn.jsdelivr.net" in policy

    live = await client.get("/health/live")
    assert live.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


@pytest.mark.asyncio
async def test_problem_details_validation_unhandled_and_body_limit(
    client: httpx.AsyncClient,
) -> None:
    invalid = await client.post("/v1/test-validation", json={})
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")
    payload = invalid.json()
    assert payload["type"].endswith("/validation_error")
    assert payload["status"] == 422
    assert payload["instance"].endswith(invalid.headers["x-request-id"])
    assert payload["errors"][0]["location"] == ["body", "name"]

    oversized = await client.post(
        "/v1/test-validation",
        content=b"x" * 1025,
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "request_too_large"

    unhandled = await client.get("/test-unhandled")
    assert unhandled.status_code == 500
    assert unhandled.json()["code"] == "internal_error"
    assert "secret backend detail" not in unhandled.text


@pytest.mark.asyncio
async def test_rate_limit_and_metrics_use_bounded_labels(client: httpx.AsyncClient) -> None:
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.redis = RateProbe(count=3)
    limited = await client.post("/v1/test-validation", json={"name": "demo"})
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "30"

    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    assert "ecommerce_http_requests_total" in metrics.text
    assert 'route="/v1/test-validation"' in metrics.text
    assert "safe-id" not in metrics.text


@pytest.mark.asyncio
async def test_readiness_checks_every_dependency_without_exposing_errors(
    client: httpx.AsyncClient,
) -> None:
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.reader_database = Probe()
    app.state.redis = RateProbe()
    app.state.media_service = Probe()
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["dependencies"] == {
        "postgres_reader": "ok",
        "redis": "ok",
        "minio": "ok",
    }

    app.state.redis = RateProbe()
    app.state.media_service = Probe(RuntimeError("minio password=secret"))
    unavailable = await client.get("/health/ready")
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "dependencies_unavailable"
    assert "password" not in unavailable.text
    assert unavailable.json()["errors"][-1] == {
        "dependency": "minio",
        "status": "unavailable",
    }


def test_openapi_has_tags_examples_and_operation_metadata() -> None:
    app = hardened_app()
    schema = app.openapi()
    assert {tag["name"] for tag in schema["tags"]} >= {
        "health",
        "observability",
        "sandbox",
        "commerce",
        "admin",
    }
    assert schema["components"]["schemas"]["CartQuantityRequest"]["examples"]
    for path in schema["paths"].values():
        for operation in path.values():
            assert operation["tags"]
            assert operation["summary"]
