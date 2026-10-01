# Operations guide

[Documentation index](README.md) · [Architecture](architecture/README.md)

The checked-in Compose stack is for development: it defaults to `APP_ENV=development`, mounts source,
and runs the API with reload. It is not a production deployment definition.

## Configuration ownership

Use [`.env.example`](../.env.example) as the configuration inventory and
[`Settings`](../backend/src/klack/core/config.py) for validation rules.

| Settings | Consumer and purpose |
| --- | --- |
| `APP_ENV` | `development` permits unverified membership creation; `production` and `staging` require verified email |
| `DATABASE_URL` | Host processes and migration jobs; Compose constructs an internal URL from `POSTGRES_*` |
| `DB_*` | Per-process pool limits and database timeouts |
| `AUTH_JWT_SECRET`, `AUTH_REFRESH_SECRET`, `AUTH_ACTION_SECRET` | Distinct authentication, refresh, and action security secrets |
| `WORKSPACE_INVITATION_SECRET` | Invitation digest secret, distinct from authentication secrets |
| `AUTH_TRUSTED_ORIGIN` | Exact browser Origin accepted for unsafe requests and WebSockets |
| `AUTH_PUBLIC_WEB_ORIGIN` | Browser-facing origin used in generated links |
| `AUTH_COOKIE_SECURE` | Secure cookie transport; required outside local/test environments |
| `SMTP_*`, `EMAIL_FROM_ADDRESS` | Identity-worker email delivery; blank SMTP disables delivery |
| `IDENTITY_*` | Worker leases, polling, cleanup retention, and batch limits |
| `REALTIME_*` | Listener, socket limits, authorization rechecks, and event retention |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Optional backend call provider configuration |
| `API_ORIGIN` in `frontend/.env.local` or server environment | Next.js upstream API location |

Never expose provider keys or authentication secrets as frontend public settings. Staging and
production reject the checked-in `change-me` application secrets and require HTTPS origins and
secure cookies. Keep local `POSTGRES_*` credentials URL-safe because Compose interpolates them.

The frontend rewrite and client paths assume `/api/v1`. Changing only `API_V1_PREFIX` on the API
will break that contract; update the proxy/client routes together if changing the prefix.

## Deployment sequence

1. Prepare PostgreSQL and the environment-specific secrets/origins. Verify database backup and
   restore procedures before schema changes.
2. Install the locked backend dependencies and run the one-shot Alembic job against the target
   database: `uv run --project backend alembic -c backend/alembic.ini upgrade head`.
3. Start the API and the separate `klack-identity-worker run` process with the same database and
   relevant security settings. Call maintenance runs inside API processes when configured.
4. Build and start the frontend with `npm run build` and `npm start` from `frontend/`.
   Provide the correct `API_ORIGIN` at build and server start.
5. Terminate HTTPS at a reverse proxy and preserve WebSocket upgrades on `/api/v1/realtime`.
   Confirm sign-in, a protected mutation, message delivery between two sessions, and optional calls.

The frontend requires a server; static export cannot provide its API/WebSocket proxy. If the
hosting platform cannot proxy persistent WebSockets, route that path through a suitable same-origin
reverse proxy. Preserve cookie attributes and the original browser Origin.

## Health and process supervision

| Signal | What it proves | What it does not prove |
| --- | --- | --- |
| `GET /health/live` | API process can respond | Database or external services are healthy |
| `GET /health/ready` | Bounded PostgreSQL check succeeds | SMTP, LiveKit, or realtime listener is healthy |
| `realtime_listener_ready` log | This process established its listener | Every browser is subscribed or receiving |
| Worker/API logs | Delivery, maintenance, and lifecycle activity | End-to-end user success by themselves |

Use structured logs and correlation IDs to investigate request failures without logging tokens,
cookies, invitation URLs, passwords, or message bodies. Monitor API and identity-worker processes
separately. `/docs` is disabled outside development; `/openapi.json` remains available for tooling.

Each API replica owns a pool plus a dedicated listener connection when realtime is enabled.
Workers also use database connections. Budget total PostgreSQL capacity across all processes;
`DB_POOL_SIZE` and `DB_MAX_OVERFLOW` are not deployment-wide limits.

## Failure and recovery behavior

| Failure | Behavior and recovery |
| --- | --- |
| PostgreSQL unavailable | Readiness fails and database-dependent work cannot proceed; restore connectivity and inspect request errors. |
| Realtime listener lost | Local sockets close; the listener retries and clients resubscribe and reload REST history. |
| Slow WebSocket consumer | Bounded outbound queues cause disconnection; client recovery uses REST snapshots. |
| SMTP unavailable or disabled | Email delivery cannot complete; the identity worker continues its cleanup duties. Inspect configuration and worker logs. |
| LiveKit unavailable | Call control/history remain database-owned; ended-room cleanup retries. Provider-side media removal can be delayed. |
| Calling tab reloads or is suspended | Media disconnects or leases expire; maintenance ends abandoned calls. Another participant tab can end a call. |

The identity worker leases outbox messages and cleans expired state in bounded batches. Its
`once`, `deliver`, and `cleanup` modes are described in the [development guide](development.md#identity-maintenance-worker).
Realtime event cleanup runs in API tasks. Call maintenance uses bounded, skip-locked batches so
multiple API replicas can sweep safely. Do not treat retained realtime events as a client replay log.

## Data and migration care

PostgreSQL stores accounts, authorization, messages, call metadata, and pending maintenance work.
LiveKit carries voice media; Klack does not store audio recordings. Back up the database and
protect the secrets needed to interpret encrypted outbox data. Secret changes can affect existing
credentials and queued actions; this repository does not provide a universal secret-rotation runbook.

Review migrations and compatibility before upgrading. Do not assume downgrades are lossless or
automatically roll the schema back after an application failure. Development teardown with
`docker compose down` preserves the named data volume; `docker compose down --volumes` deletes it.

The [performance workload](../performance/README.md) measures HTTP API journeys through the
frontend proxy. It does not measure WebSocket delivery, LiveKit media, page rendering, or production
capacity. Use its results only within the environment and workload that produced them.

## File storage and cleanup

File sharing requires private durable storage and the `klack-files-worker` process.
See [file-sharing deployment](files.md) for configuration, scanning, backup, and limits.
