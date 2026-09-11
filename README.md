# Klack

Klack is a production-oriented collaboration platform built incrementally as a modular
monolith. The backend currently includes its infrastructure foundation plus the first vertical
slice: user registration, password authentication, and independently revocable browser sessions.

## Implemented

- Python 3.13 FastAPI application with validated configuration and async PostgreSQL access.
- Alembic migrations, structured logging, correlation IDs, and operational health checks.
- Case-insensitive normalized email registration and Argon2id password hashing.
- Short-lived access JWTs backed by durable PostgreSQL session checks.
- Opaque rotating refresh credentials with replay detection and family revocation.
- Exact-origin and session-bound double-submit CSRF protection.
- Current-user, logout, logout-all, session listing, and individual session revocation APIs.
- Single-use email verification and password recovery through an encrypted transactional outbox.
- PostgreSQL-shared authentication throttles, active-session caps, and bounded global cleanup.
- Database-backed workspaces with owner, administrator, and member authorization.
- Single-use, manually shared workspace invitation links that do not depend on email delivery.

Channels, messaging, and a frontend are not implemented yet.

## Prerequisites

- Docker Desktop with Docker Compose v2
- Python 3.13 and [uv](https://docs.astral.sh/uv/) for host-based development

## Start the development stack

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The API is available at `http://127.0.0.1:8000`. In development, interactive Swagger is available
at `http://127.0.0.1:8000/docs`; use that exact host and port because unsafe requests require the
configured exact Origin. The interactive page is disabled outside development, while
`/openapi.json` remains available for tooling.
Operational endpoints are:

- `GET /health/live`: process-only liveness; never accesses PostgreSQL.
- `GET /health/ready`: bounded PostgreSQL readiness check.

### Browser authentication API

The versioned identity routes are:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/email-verification/request`
- `POST /api/v1/auth/email-verification/complete`
- `POST /api/v1/auth/password-recovery/request`
- `POST /api/v1/auth/password-recovery/complete`
- `POST /api/v1/auth/refresh`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/logout-all`
- `GET /api/v1/auth/sessions`
- `DELETE /api/v1/auth/sessions/{session_id}`

Bearer credentials are transported only in host-only cookies and are never returned in JSON.
Unsafe auth requests require the configured exact `Origin`. Cookie-authenticated mutations also
require `X-CSRF-Token` to match the readable CSRF cookie and the durable session binding.

Access tokens default to 15 minutes and sessions to a 30-day absolute lifetime. Refresh rotation
has a five-minute default cooldown; an early valid refresh returns `429` with `Retry-After`
without consuming the token. Login, registration, verification, and recovery also consume shared
PostgreSQL subject and IP throttle buckets. Login retains at most ten active sessions per account
by default, revoking the oldest session families first.

### Workspace API

Authenticated users can create workspaces, manage memberships according to their current durable
role, and generate manually shareable invitation links. Workspace authorization is read from
PostgreSQL rather than JWT claims, so removals and role changes take effect immediately.

The versioned workspace routes are:

- `POST /api/v1/workspaces`
- `GET /api/v1/workspaces`
- `GET|PATCH /api/v1/workspaces/{workspace_id}`
- `GET /api/v1/workspaces/{workspace_id}/memberships`
- `GET /api/v1/workspaces/{workspace_id}/memberships/me`
- `PATCH|DELETE /api/v1/workspaces/{workspace_id}/memberships/{user_id}`
- `POST /api/v1/workspaces/{workspace_id}/leave`
- `POST|GET /api/v1/workspaces/{workspace_id}/invitations`
- `POST /api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/rotate`
- `DELETE /api/v1/workspaces/{workspace_id}/invitations/{invitation_id}`
- `POST /api/v1/workspace-invitations/accept`

Invitation links are seven-day, member-only bearer credentials by default. The API reveals a link
only when it is created or rotated, stores only its HMAC digest, and never requires SMTP or email
verification. The inviter must share the link through an external channel. Whoever first redeems
the active link while authenticated becomes its member.

### Swagger demo accounts

The repository includes an explicit, development-only fixture command for manually exercising the
authenticated API. After copying `.env.example` to `.env`, set `DEV_SEED_ENABLED=true` in `.env`
and run this from the repository root after the stack is up and migrations have completed:

```powershell
uv run --project backend klack-dev-seed
```

The command creates four enabled, unverified accounts and one `Klack Swagger Demo` workspace:

- `dev.owner@klack.example` — owner
- `dev.admin@klack.example` — admin
- `dev.member@klack.example` — member
- `dev.outsider@klack.example` — not a member, ready to accept an invitation

All four use the password in `DEV_SEED_PASSWORD` (the example value is local-only). The seed is
idempotent, creates no sessions, email actions, outbox messages, or invitations, and refuses to
run outside `APP_ENV=development`. It never runs automatically as part of Compose startup.

Swagger stores the normal login cookies and automatically presents the readable CSRF cookie for
same-origin API mutations. Log in as the owner, use the seeded workspace or create another one,
create an invitation, then log in as the outsider and submit the token from the returned
`invite_url` to `POST /api/v1/workspace-invitations/accept`. Logging in as each seeded role lets
you exercise the role and last-owner rules without an email service.

### Local environment contract

The root `.env` supports both host and Compose workflows:

- Host-run commands use `APP_ENV` and the host-facing `DATABASE_URL` directly.
- Compose is development-only: it forces `APP_ENV=development` and builds an internal database
  URL from `POSTGRES_*` values.
- The `klack-dev-seed` command is a host-run development tool and requires the separate
  `DEV_SEED_ENABLED=true` opt-in plus `DEV_SEED_PASSWORD`.

Replace all four checked-in `change-me` application secrets outside local development. Staging and
production reject those markers and require HTTPS plus secure cookies. Keep local PostgreSQL
values URL-safe because Compose interpolates them into the internal URL.

## Backend development on the host

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
uv build --project backend
docker compose --env-file .env.example config --quiet
```

Set `RUN_INTEGRATION_TESTS=1` with the host PostgreSQL `DATABASE_URL` to include the real
registration, refresh rotation, replay, and row-lock concurrency checks.

## Migrations

Revision `20260823_0001_identity_authentication` owns users, password credentials, sessions, and
refresh tokens. Revision `20260824_0002_identity_security_followups` adds email actions, the
encrypted outbox, and shared throttle buckets. Revision `20260909_0003` adds workspaces,
memberships, and manual invitation links.

```powershell
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend alembic -c backend/alembic.ini check
```

Alembic jobs require only `DATABASE_URL`. The API never migrates at startup; Compose and
deployments run migrations as an explicit one-shot job.

## Identity maintenance worker

Compose starts `klack-identity-worker run` after migrations. It leases and delivers queued email
actions when SMTP is configured and performs global cleanup even when delivery is disabled.
Configure `SMTP_HOST` plus `EMAIL_FROM_ADDRESS`; SMTP username/password are optional but must be
supplied together. Useful one-shot modes are:

```powershell
uv run --project backend klack-identity-worker once
uv run --project backend klack-identity-worker deliver
uv run --project backend klack-identity-worker cleanup
```

## Useful container commands

```powershell
docker compose logs -f api
docker compose run --rm migrate
docker compose down
```

`docker compose down --volumes` also deletes the local PostgreSQL data volume and is destructive.
