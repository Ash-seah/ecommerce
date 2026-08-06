# Ecommerce Sandbox API

This is an anonymous, production-shaped ecommerce demonstration API. It is not an
account system, payment processor, durable storefront, or suitable place for personal
or valuable data. Every sandbox expires after two hours by default. Browser cookies,
cart state, orders, wallet entries, catalog edits, and uploaded sandbox media are
demo-only and can disappear.

## Architecture and copy-on-write behavior

- PostgreSQL database `ecommerce_master` holds the revisioned master catalog.
  Runtime shopper reads use `ecommerce_reader`. Migrations, seed, and JWT master
  write APIs use `ecommerce_owner`.
- Redis database `1`, under the `ecommerce:` prefix, holds the published catalog
  snapshot, anonymous sandbox state, optimistic-locking versions, idempotency results,
  and rate-limit counters.
- Each new sandbox pins a master revision. Admin changes create Redis overlays,
  tombstones, stock overrides, coupons, and session-local entities. They never update
  master PostgreSQL rows. Reads merge the pinned snapshot with those overlays.
- Redis `WATCH`/`MULTI` makes sandbox mutations atomic and retries conflicts. Checkout
  uses a caller-supplied `Idempotency-Key` so a replay cannot double-charge or
  double-decrement inventory.
- MinIO uses separate master and sandbox buckets. Sandbox object names are scoped under
  `sandboxes/<HMAC session id>/`; reset cleanup and a one-day lifecycle rule remove
  temporary objects.
- The API emits JSON request logs containing request ID, method, route template, status,
  and duration only. It never records headers, query values, bodies, cookies, session
  IDs, idempotency keys, or CSRF tokens.

## HTTP surface

Interactive OpenAPI is at <http://localhost:8001/docs>; the schema is
<http://localhost:8001/openapi.json>.

- Health/monitoring: `GET /health/live`, `GET /health/ready`, `GET /metrics`.
- Sandbox: create/inspect, refresh, reset, and rotate CSRF under
  `/v1/sandbox/session`; merged catalog at `/v1/sandbox/catalog`.
- Catalog: paginated categories and products, lookup, search, filters, availability,
  and sorting under `/v1/catalog`.
- Cart and wishlist: `/v1/cart`, `/v1/cart/items`, `/v1/wishlist`,
  `/v1/wishlist/items`.
- Addresses and wallet: `/v1/addresses`, `/v1/wallet`, `/v1/wallet/ledger`, and demo
  credits/debits under `/v1/commerce/wallet/adjustments/{credit|debit}`.
- Checkout and orders: `/v1/checkout`, `/v1/orders`, `/v1/orders/{id}`, and
  `/v1/orders/{id}/transition`.
- Sandbox admin: copy-on-write categories, products, variants, prices, inventory,
  active flags, coupons, media, and restore operations under `/v1/admin`.
- Master admin (JWT): login at `POST /v1/master/auth/login`, then Bearer-protected
  create/update for categories, products, variants; multipart product media upload
  into the master MinIO bucket; delete media; and
  `POST /v1/master/catalog/publish` (also runs automatically after each write).

Sandbox/commerce mutations require the session cookie, an allowed `Origin` (or Host
matching CORS), and the origin-bound `X-CSRF-Token`. Checkout additionally requires
`Idempotency-Key`. Master routes use `Authorization: Bearer <jwt>` instead of CSRF.
Errors use RFC 9457 `application/problem+json`; `instance` is the safe request ID URN.

## Configuration and secrets

Copy `.env.example` to `.env`. The example is intentionally unusable: placeholder
secrets are rejected. `.env` and every `.env.*` except `.env.example` are ignored.
Generate every password and signing key independently:

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Set both password variables and the matching passwords embedded in `DATABASE_URL` and
`MIGRATION_DATABASE_URL`. Do not reuse terabit-fintech or other application secrets.
Important settings are:

- `ENVIRONMENT`, `API_PORT`, `CORS_ORIGINS`.
- owner/reader passwords and URLs; the runtime URL must be `ecommerce_reader` and the
  migration URL must be `ecommerce_owner`.
- isolated `REDIS_URL` database `1` and fixed `REDIS_KEY_PREFIX=ecommerce`.
- MinIO endpoint, independent credentials, bucket names, upload/concurrency limits,
  and optional media URL gateway.
- independent session/CSRF secrets, two-hour TTL, and cookie flags.
- plain `ADMIN_USERNAME` / `ADMIN_PASSWORD` (defaults `admin` / `admin123`) and a
  distinct `JWT_SECRET` (≥32 chars) for master write APIs.
- request/upload byte limits, rate count/window, readiness timeout, and commerce limits.
- `TRUSTED_PROXY_IPS`: only direct reverse-proxy IPs/CIDRs trusted by Uvicorn.

Client IP for rate limiting is `request.client.host` after Uvicorn's proxy processing.
Do not use `TRUSTED_PROXY_IPS=*`; production validation rejects it. If no reverse proxy
is used, keep `127.0.0.1`. Behind Nginx/Traefik, list only the proxy container/network
addresses and ensure the API port is not directly exposed to untrusted clients.

Production requires `SESSION_COOKIE_SECURE=true`, HTTPS CORS origins, secret-manager
injection, pinned image digests, TLS at the reverse proxy, and restricted `/metrics`.

## Windows local setup

Prerequisites: Python 3.12, Docker Desktop with Compose, and the existing
terabit-fintech PostgreSQL and Redis containers.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
docker network ls
docker inspect terabit-fintech-postgres-1 --format '{{json .NetworkSettings.Networks}}'
```

Set `SHARED_NETWORK_NAME`, `POSTGRES_CONTAINER`, `POSTGRES_SERVICE_HOST`, and
`REDIS_SERVICE_HOST` to the real names/aliases. `POSTGRES_SUPERUSER` defaults to
`financial`, matching the supplied terabit-fintech Postgres configuration. The
PowerShell bootstrap is idempotent:

```powershell
.\scripts\bootstrap-shared-postgres.ps1
docker compose config
docker compose --profile setup up --build --abort-on-container-exit --exit-code-from cache-refresh cache-refresh
docker compose up --build -d api
docker compose ps
Invoke-RestMethod http://localhost:8001/health/ready
```

The setup profile runs migration, seed, then cache refresh in order. The seed uses
stable UUIDs and upserts, so rerunning it is safe. Equivalent host commands are:

```powershell
alembic upgrade head
python -m scripts.seed_master_catalog
python -m scripts.refresh_catalog_cache
```

Run tests in the dedicated image with:

```powershell
docker compose --profile test run --rm test
```

## Ubuntu deployment beside terabit-fintech

Install Docker Engine plus the Compose plugin. Discover the network and aliases rather
than assuming the Compose project name:

```bash
docker network ls
docker inspect terabit-fintech-postgres-1 \
  --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}'
docker inspect terabit-fintech-postgres-1 \
  --format '{{json .NetworkSettings.Networks}}'
docker ps --format '{{.Names}}'
```

Set the discovered external network and service aliases in `.env`. Then bootstrap
PostgreSQL from a trusted administrative shell (or run the SQL through `psql` with
`owner_password` and `reader_password` variables), and run:

```bash
docker compose config
docker compose --profile setup up --build --abort-on-container-exit \
  --exit-code-from cache-refresh cache-refresh
docker compose up --build -d api
docker compose ps
curl --fail http://127.0.0.1:8001/health/ready
```

`bootstrap-shared-postgres.sql` uses psql autocommit and `\gexec`; never wrap it in
`BEGIN` or `--single-transaction`, because `CREATE DATABASE` cannot run there. It only
creates/updates dedicated ecommerce roles and `ecommerce_master`; it does not modify
the `financial` database. The reader receives `CONNECT`, schema `USAGE`, and table
`SELECT`, while write privileges remain absent.

## Master catalog JWT examples

```bash
TOKEN=$(curl -sS -X POST http://localhost:8001/v1/master/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | jq -r .access_token)

curl -sS -X POST http://localhost:8001/v1/master/categories \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"slug":"shoes","name":"Shoes","sort_order":0}'

# After creating a product, upload a PNG/JPEG/WebP into ecommerce-master:
curl -sS -X POST "http://localhost:8001/v1/master/products/$PRODUCT_ID/media" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@./hero.png;type=image/png" \
  -F 'alt_text=Hero' -F 'sort_order=0'
```

Behind Nginx with `ROOT_PATH=/api`, call `https://ecommerce.terabitventure.com/api/v1/master/...`.
Writes refresh the Redis catalog snapshot; new sandboxes pick up the updated master.

## Cookie and CSRF examples

With curl, preserve the cookie jar and extract the returned token. `jq` is used only to
parse JSON:

```bash
ORIGIN=http://localhost:3000
curl -sS -c cookies.txt -H "Origin: $ORIGIN" \
  http://localhost:8001/v1/sandbox/session/create > session.json
CSRF=$(jq -r .csrf_token session.json)
curl -sS -b cookies.txt -H "Origin: $ORIGIN" -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"variant_id":"REPLACE-WITH-VARIANT-UUID","quantity":1}' \
  http://localhost:8001/v1/cart/items
curl -sS -b cookies.txt -H "Origin: $ORIGIN" -H "X-CSRF-Token: $CSRF" \
  -H "Idempotency-Key: demo-checkout-001" -H "Content-Type: application/json" \
  -d '{"address_id":"REPLACE-WITH-ADDRESS-UUID"}' \
  http://localhost:8001/v1/checkout
```

PowerShell's web session is the cookie jar:

```powershell
$origin = 'http://localhost:3000'
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$created = Invoke-RestMethod -WebSession $session -Headers @{ Origin = $origin } `
  http://localhost:8001/v1/sandbox/session/create
$headers = @{ Origin = $origin; 'X-CSRF-Token' = $created.csrf_token }
Invoke-RestMethod -Method Post -WebSession $session -Headers $headers `
  -ContentType 'application/json' `
  -Body '{"variant_id":"REPLACE-WITH-VARIANT-UUID","quantity":1}' `
  http://localhost:8001/v1/cart/items
$headers['Idempotency-Key'] = 'demo-checkout-001'
Invoke-RestMethod -Method Post -WebSession $session -Headers $headers `
  -ContentType 'application/json' `
  -Body '{"address_id":"REPLACE-WITH-ADDRESS-UUID"}' `
  http://localhost:8001/v1/checkout
```

## MinIO URLs and lifecycle

MinIO S3 is <http://localhost:9000>; the console is <http://localhost:9001>. The init
job idempotently creates both buckets and imports the lifecycle policy from
`docker/minio-sandbox-lifecycle.json`. Expiration is one day because S3 lifecycle
granularity is days; normal reset cleanup is immediate and Redis sessions expire in
two hours.

When `MEDIA_PUBLIC_BASE_URL` is unset, the API returns one-hour presigned URLs based on
`MINIO_ENDPOINT`. That hostname must be resolvable by both the API container and the
client (normally an HTTPS reverse-proxy DNS name in deployment). `minio:9000` is only
resolvable on the Docker network. Set `MEDIA_PUBLIC_BASE_URL` only when a gateway
deliberately serves `/<bucket>/<object>` without MinIO query signatures; it does not
magically make a private bucket public.

## Backup and restore boundaries

Back up PostgreSQL `ecommerce_master` and the master MinIO bucket as durable source
data. Back up migration history with the database and test restores together. Redis
sandbox state, rate counters, cached snapshots, and the sandbox MinIO bucket are
ephemeral and are normally excluded. After restoring PostgreSQL, run migrations if
needed and `python -m scripts.refresh_catalog_cache`. Restoring PostgreSQL does not
restore anonymous sessions; restoring Redis does not make missing MinIO objects return.

## Verification

```powershell
ruff check .
ruff format --check .
mypy --strict src
pytest
python -m compileall -q src scripts tests
alembic upgrade head --sql > $null
docker compose config
```

Pytest enforces source coverage of at least 75%. Runtime dependency verification is
available at `/health/ready`; failures return 503 and dependency names/status only.
Prometheus request count and latency use method, route template, and status labels—no
session, request ID, path parameter, query, or client-IP labels.

## Troubleshooting

- Settings fail at startup: replace every placeholder, keep owner/reader and
  session/CSRF secrets distinct, select Redis DB `1`, and align URL passwords.
- PostgreSQL cannot resolve/connect: inspect the external network, service alias, and
  `POSTGRES_SERVICE_HOST`; rerun the idempotent bootstrap.
- Readiness says `postgres_reader` unavailable: confirm migration/seed completed and
  the reader has `CONNECT`, `USAGE`, and `SELECT`.
- Readiness says `redis` unavailable or requests return rate-limiter 503: verify Redis
  alias, DB `1`, and network attachment.
- Readiness says `minio` unavailable: run `docker compose up -d minio minio-init` and
  inspect `docker compose logs minio minio-init`.
- Catalog unavailable: run the cache-refresh job after migration and seed.
- Browser mutation gets 403: send an exact configured `Origin`, retain the HttpOnly
  cookie, and use the latest CSRF token (rotation/reset invalidates the old token).
- Browser CORS fails: only explicit methods and `Authorization`, `Content-Type`,
  `Idempotency-Key`, `X-CSRF-Token`, and `X-Request-ID` are allowed.
- Master login fails after deploy: set plain `ADMIN_USERNAME`/`ADMIN_PASSWORD` and a
  unique `JWT_SECRET` in `.env`, then recreate the API container.
- Media URL uses `minio:9000`: configure an endpoint resolvable from clients or a
  correctly secured media gateway as described above.
