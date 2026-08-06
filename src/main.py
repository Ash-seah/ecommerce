"""FastAPI application entry point."""

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from minio import Minio
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from src.admin.router import router as admin_router
from src.admin.service import AdminService
from src.catalog.cache import CatalogSnapshotCache
from src.catalog.repository import MasterCatalogRepository
from src.commerce.router import router as commerce_router
from src.commerce.service import (
    BasisPointTaxPolicy,
    CommerceLimits,
    CommerceService,
    DemoCouponPolicy,
    FlatShippingPolicy,
    PricingService,
)
from src.core.config import Settings, get_settings
from src.core.errors import install_exception_handlers, problem
from src.core.middleware import RequestMiddleware
from src.infrastructure.database import OwnerDatabase, ReaderDatabase
from src.infrastructure.minio import MediaService, MinioProtocol
from src.infrastructure.redis import RedisClient
from src.master.router import router as master_router
from src.master.service import MasterCatalogService
from src.sandbox.router import router as sandbox_router
from src.sandbox.security import SessionSecrets
from src.sandbox.service import RedisProtocol, SandboxService


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application without opening infrastructure connections."""

    resolved_settings = settings or get_settings()
    database = ReaderDatabase(resolved_settings)
    owner_database = OwnerDatabase(resolved_settings)
    redis = RedisClient(resolved_settings)
    catalog_repository = MasterCatalogRepository(
        database.session_factory,
        media_public_base_url=(
            str(resolved_settings.media_public_base_url)
            if resolved_settings.media_public_base_url
            else None
        ),
        master_bucket=resolved_settings.minio_master_bucket,
    )
    catalog_cache = CatalogSnapshotCache(
        redis, catalog_repository, key_prefix=resolved_settings.redis_key_prefix
    )
    session_secrets = SessionSecrets(
        resolved_settings.session_secret.get_secret_value().encode(),
        resolved_settings.csrf_secret.get_secret_value().encode(),
    )
    minio_client = Minio(
        resolved_settings.minio_endpoint,
        access_key=resolved_settings.minio_access_key,
        secret_key=resolved_settings.minio_secret_key.get_secret_value(),
        secure=resolved_settings.minio_secure,
    )
    media_service = MediaService(
        cast(MinioProtocol, minio_client),
        master_bucket=resolved_settings.minio_master_bucket,
        sandbox_bucket=resolved_settings.minio_sandbox_bucket,
        max_upload_bytes=resolved_settings.max_upload_bytes,
        media_base_url=(
            str(resolved_settings.media_public_base_url)
            if resolved_settings.media_public_base_url
            else None
        ),
        max_concurrency=resolved_settings.media_worker_concurrency,
    )
    sandbox_service = SandboxService(
        cast(RedisProtocol, redis),
        catalog_cache,
        session_secrets,
        key_prefix=resolved_settings.redis_key_prefix,
        ttl_seconds=resolved_settings.session_ttl_seconds,
        initial_wallet_minor=resolved_settings.demo_wallet_initial_minor,
        wallet_currency=resolved_settings.demo_wallet_currency,
        media_cleanup=media_service.cleanup,
    )
    admin_service = AdminService(
        sandbox_service, default_stock=resolved_settings.demo_stock_default
    )
    commerce_service = CommerceService(
        sandbox_service,
        PricingService(
            coupon=DemoCouponPolicy(),
            shipping=FlatShippingPolicy(
                flat_minor=resolved_settings.shipping_flat_minor,
                free_threshold_minor=resolved_settings.free_shipping_threshold_minor,
            ),
            tax_policy=BasisPointTaxPolicy(resolved_settings.tax_basis_points),
        ),
        CommerceLimits(
            page_max=resolved_settings.commerce_page_max,
            cart_quantity_max=resolved_settings.cart_quantity_max,
            address_max=resolved_settings.address_max,
            default_stock=resolved_settings.demo_stock_default,
        ),
    )
    master_service = MasterCatalogService(
        owner_database.session_factory,
        media_service,
        catalog_cache,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started = True
        try:
            yield
        finally:
            app.state.started = False
            await redis.close()
            await database.close()
            await owner_database.close()

    application = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        description=(
            "Anonymous, two-hour ecommerce demonstration API. Shopper mutations stay in "
            "expiring Redis sandboxes. Master catalog and master MinIO writes use JWT "
            "operator endpoints under /v1/master."
        ),
        openapi_tags=[
            {"name": "health", "description": "Process liveness and dependency readiness."},
            {"name": "observability", "description": "Prometheus monitoring endpoint."},
            {"name": "sandbox", "description": "Anonymous sandbox lifecycle and merged catalog."},
            {"name": "commerce", "description": "Session-local shopping and checkout workflows."},
            {"name": "admin", "description": "Copy-on-write catalog administration."},
            {
                "name": "master",
                "description": "JWT-protected master PostgreSQL and MinIO catalog editing.",
            },
        ],
        root_path=resolved_settings.root_path,
        lifespan=lifespan,
    )
    application.state.reader_database = database
    application.state.owner_database = owner_database
    application.state.redis = redis
    application.state.catalog_repository = catalog_repository
    application.state.catalog_cache = catalog_cache
    application.state.settings = resolved_settings
    application.state.session_secrets = session_secrets
    application.state.sandbox_service = sandbox_service
    application.state.admin_service = admin_service
    application.state.media_service = media_service
    application.state.commerce_service = commerce_service
    application.state.master_service = master_service
    application.state.started = False
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin).rstrip("/") for origin in resolved_settings.cors_origins],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-CSRF-Token",
            "X-Request-ID",
        ],
        expose_headers=["Retry-After", "X-Request-ID"],
    )
    application.add_middleware(RequestMiddleware)
    application.include_router(sandbox_router)
    application.include_router(commerce_router)
    application.include_router(admin_router)
    application.include_router(master_router)
    install_exception_handlers(application)

    @application.get(
        "/health/live",
        tags=["health"],
        summary="Check process liveness",
        response_description="The API process is accepting requests.",
    )
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @application.get(
        "/health/ready",
        tags=["health"],
        summary="Check required dependencies",
        response_description="Dependency readiness, without connection details.",
        response_model=None,
    )
    async def readiness(request: Request) -> dict[str, object] | Response:
        timeout = resolved_settings.readiness_timeout_seconds
        checks = {
            "postgres_reader": request.app.state.reader_database.ping(),
            "redis": request.app.state.redis.ping(),
            "minio": request.app.state.media_service.ping(),
        }

        async def bounded(check: Awaitable[bool]) -> bool:
            return await asyncio.wait_for(check, timeout=timeout)

        results = await asyncio.gather(
            *(bounded(check) for check in checks.values()), return_exceptions=True
        )
        details = {
            name: "ok" if result is True else "unavailable"
            for name, result in zip(checks, results, strict=True)
        }
        if any(value != "ok" for value in details.values()):
            return problem(
                request,
                status=503,
                code="dependencies_unavailable",
                title="Service not ready",
                detail="One or more required dependencies are unavailable.",
                errors=[{"dependency": name, "status": status} for name, status in details.items()],
            )
        return {"status": "ready", "dependencies": details}

    @application.get(
        "/metrics",
        tags=["observability"],
        summary="Export Prometheus metrics",
        include_in_schema=True,
    )
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return application


app = create_app()
