# Klack

Klack is a production-oriented collaboration platform built incrementally as a modular
monolith. This repository currently contains only the backend foundation: configuration,
async PostgreSQL access, Alembic, structured logging, and operational health checks.

Authentication and product modules are intentionally not part of this milestone.

## Prerequisites

- Docker Desktop with Docker Compose v2
- Python 3.13 and [uv](https://docs.astral.sh/uv/) for host-based development

## Start the foundation stack

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The API is available at `http://127.0.0.1:8000`. Its operational endpoints are:

- `GET /health/live`: process-only liveness; never accesses PostgreSQL.
- `GET /health/ready`: bounded PostgreSQL readiness check.

The local API documentation is available at `http://127.0.0.1:8000/docs`. Health routes are
deliberately excluded from the public API schema.

### Local environment contract

The root `.env` serves two related development workflows:

- Host-run commands use `APP_ENV` and the host-facing `DATABASE_URL` directly.
- Compose is deliberately development-only: it forces `APP_ENV=development` and constructs an
  internal database URL using the `postgres` service hostname and the `POSTGRES_*` values.

Keep local `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` values URL-safe because Compose
interpolates them into that internal URL. `WATCHFILES_FORCE_POLLING=true` makes source reloads
reliable across Docker Desktop, WSL, macOS, and Linux bind mounts.

## Backend development on the host

Start PostgreSQL, copy the environment template, and install the locked environment:

```powershell
Copy-Item .env.example .env
docker compose up -d postgres
uv sync --project backend --locked
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend uvicorn klack.main:create_app --factory --reload --no-access-log
```

Run the quality gates from the repository root:

```powershell
uv run --project backend ruff format --check backend
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
uv run --project backend pytest backend/tests
```

## Migrations

Alembic is configured, but there is no empty placeholder revision. The first meaningful
revision will be created with the first domain table.

```powershell
uv run --project backend alembic -c backend/alembic.ini revision --autogenerate -m "describe change"
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend alembic -c backend/alembic.ini check
```

Always review generated revisions. The API never applies migrations during startup; Compose
and deployments run them as an explicit one-shot job.

## Useful container commands

```powershell
docker compose logs -f api
docker compose run --rm migrate
docker compose down
```

`docker compose down --volumes` also deletes the local PostgreSQL data volume and is
destructive.
