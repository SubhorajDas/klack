# Development guide

[Documentation index](README.md)

Run backend commands from the repository root and npm commands from `frontend/`.
Examples use PowerShell.

## Browser origins

For the frontend, set both `AUTH_TRUSTED_ORIGIN` and `AUTH_PUBLIC_WEB_ORIGIN` to
`http://127.0.0.1:3000` in the root `.env`. For direct Swagger mutations, set
`AUTH_TRUSTED_ORIGIN=http://127.0.0.1:8000` and open `http://127.0.0.1:8000/docs`.
Only one exact trusted origin is configured at a time. `localhost` and `127.0.0.1` are different
origins, as are different ports. Switching to Swagger prevents frontend mutations until the
trusted origin is changed back.

After editing `.env`, recreate the API to load its new environment:

```powershell
docker compose up -d --force-recreate api
```

Recreate `identity-worker` as well when changing SMTP or email-link settings. A plain container
restart does not load new Compose environment values.

## Swagger demo accounts

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

## Local environment contract

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

This is an alternative to the containerized API. Do not run both APIs on port 8000.
Keep an existing `.env` instead of copying over it, and configure browser origins as above.

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
uv run --project backend pytest backend/tests --cov-config=backend/pyproject.toml
uv build --project backend
docker compose --env-file .env.example config --quiet
```

PostgreSQL integration tests are opt-in through `RUN_INTEGRATION_TESTS=1`. They cover migrations,
identity, workspaces, channels, conversations, messages, calls, and concurrency. Use a disposable
test database: integration fixtures modify schema and data. Inspect the fixtures before choosing
a database URL; do not target a database containing data you need to keep.

## Migrations

Revision `20260823_0001_identity_authentication` owns users, password credentials, sessions, and
refresh tokens. Revision `20260824_0002_identity_security_followups` adds email actions, the
encrypted outbox, and shared throttle buckets. Revision `20260909_0003` adds workspaces,
memberships, and manual invitation links. Revision `20260911_0004` adds channels, explicit channel
memberships, visibility, and soft archival.
Revision `20260911_0005` adds durable channel messages, history pagination indexes, and deletion
tombstones.
Revision `20260911_0006` adds client message IDs, message revisions, and body-free committed
realtime events.
Revision `20260925_0007` adds thread relationships, reactions, read cursors, and direct conversation keys.
Revision `20260925_0008` adds durable call history and exclusive participant seats.

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

## Frontend checks

From `frontend/`:

```powershell
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright starts its fixture API on port 8100 and frontend on port 3100. These browser tests
verify the real frontend proxy against controlled fixtures, not a live PostgreSQL backend.
See the [frontend guide](../frontend/README.md#verification) for coverage and artifacts.
For HTTP load testing, see the [performance guide](../performance/README.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Origin/CSRF error on a browser mutation | Use `127.0.0.1:3000`, match `AUTH_TRUSTED_ORIGIN`, recreate the API, and sign in again if needed. Keep CSRF enabled. |
| Sign-in cannot reach the API | Check `docker compose ps`, API logs, `/health/ready`, and the frontend's `API_ORIGIN`. |
| Missing-table error | Run `docker compose run --rm migrate` and confirm success before restarting the API. |
| Verification or recovery email does not arrive | Configure `SMTP_HOST` and `EMAIL_FROM_ADDRESS` for the identity worker; blank SMTP disables delivery. |
| Calling is disabled | Set all three `LIVEKIT_*` values in the root `.env`, apply migrations, and recreate the API. |
| Microphone cannot connect | Allow microphone access and use localhost or HTTPS; verify LiveKit connectivity. |
| Messages stop updating live | Check WebSocket upgrade forwarding, trusted Origin, session expiry, and API listener logs. |
| Host commands cannot reach PostgreSQL | Match `DATABASE_URL` to the published `POSTGRES_PORT`; Compose uses its own internal URL. |

## Documentation changes

Keep commands aligned with `.env.example`, `compose.yaml`, and package scripts. Update the
[API guide](api.md) when public contracts change and the [architecture overview](architecture/README.md)
when ownership, data flow, or failure behavior changes. Record decisions with meaningful alternatives
in [an ADR](adr/README.md). Distinguish implemented behavior from planned features.
