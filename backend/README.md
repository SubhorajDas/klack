# Klack backend

The backend is a Python 3.13 FastAPI modular monolith packaged from `src/klack`. It implements
identity, workspaces, channels, durable messaging, threads, reactions, read positions, private
direct conversations, realtime delivery, and optional LiveKit voice calls.

See the [full-app quick start](../README.md#quick-start), [development commands](../docs/development.md),
[API guide](../docs/api.md), and [architecture overview](../docs/architecture/README.md).

## Source map

| Location | Responsibility |
| --- | --- |
| `src/klack/bootstrap.py` | App factory, middleware, routes, process startup/shutdown |
| `src/klack/core/` | Validated configuration, dependency container, database, logging, errors |
| `src/klack/modules/` | Identity, workspaces, channels, messaging, realtime, calling |
| `src/klack/identity_worker.py` | Separate email-outbox and identity-cleanup process |
| `src/klack/dev_seed.py` | Explicit development fixtures |
| `alembic/versions/` | Ordered schema migrations through `20260925_0008` |
| `tests/unit/`, `tests/api/`, `tests/integration/` | Isolated rules, HTTP contracts, and real PostgreSQL behavior |

## Implementation guarantees

Key guarantees:

- Settings are validated at startup and injected into the application container.
- Importing the package does not connect to PostgreSQL.
- One async SQLAlchemy engine exists per process.
- Request/task scopes receive their own `AsyncSession`; dependencies never auto-commit.
- Alembic is the only schema-management mechanism.
- Production logs and public errors do not expose credentials or raw infrastructure details.
- Liveness is process-only; readiness is a bounded PostgreSQL query.
- Password work runs off the event loop behind a bounded Argon2 concurrency limiter.
- Access authentication rechecks durable user/session state for immediate revocation.
- Refresh tokens are stored only as HMAC digests, rotate under row locks, and revoke their
  session family when a consumed token is replayed.
- Browser mutations enforce exact-origin and session-bound CSRF validation.
- Email actions are HMAC-only, purpose-bound, and queued in an AES-GCM encrypted outbox.
- Shared PostgreSQL throttles and deterministic session caps work across API replicas.
- `klack-identity-worker` delivers leased SMTP work and drains expired identity state in batches.
- Workspace roles are loaded durably and every mutation reauthorizes under a workspace row lock.
- Invitation links are HMAC-only at rest, single-use, revocable, and independent of SMTP.
- Message changes and body-free realtime signals commit atomically before PostgreSQL broadcasts
them to authenticated WebSocket subscribers on every API replica.
- Realtime delivery rechecks durable sessions and explicit channel membership, bounds connection
  memory, and falls back to REST history after any delivery-path failure.

The identity module lives at `src/klack/modules/identity` and follows the domain/application/
infrastructure/API dependency direction. Its schema is introduced by
the `20260823_0001` and `20260824_0002` Alembic revisions. The accepted security decisions are
recorded in `docs/adr/0002-identity-and-browser-sessions.md` and
`docs/adr/0003-identity-security-followups.md`.

The workspace module lives at `src/klack/modules/workspaces`. Its schema is introduced by the
`20260909_0003` Alembic revision, and its ownership, authorization, and manual-invitation decisions
are recorded in `docs/adr/0004-workspaces-memberships-and-invitation-links.md`.

The messaging and realtime modules live at `src/klack/modules/messaging` and
`src/klack/modules/realtime`. Revisions `20260911_0005` and `20260911_0006` add durable messages,
retry-safe client identifiers, revisions, and committed realtime signals. Message writes remain on
REST; `/api/v1/realtime` uses the `klack.realtime.v1` WebSocket subprotocol for delivery.

Channels live at `src/klack/modules/channels`, with explicit membership and reversible archival
introduced by `20260911_0004`. Revision `20260925_0007` adds threads, reactions, read cursors,
and direct-conversation keys. Direct messages reuse channel-backed message access while excluding
administrative participant changes and public discovery.

Calling lives at `src/klack/modules/calling`. Revision `20260925_0008` adds call history and
exclusive participant seats. The API controls authorization, device/session ownership, leases,
and durable room-cleanup retries; LiveKit handles media. Unlike the layered modules, calling
currently uses a flat router/service/models/worker layout. See the
[architecture guide](../docs/architecture/README.md) for current boundaries and couplings.

## Development entry points

Run backend commands from the repository root with `uv run --project backend ...` so the root
`.env` is discovered consistently. Pass `-c backend/alembic.ini` to Alembic when invoking it from
the root.

For local Swagger exploration, copy `.env.example` to `.env`, set `DEV_SEED_ENABLED=true`, start
the Compose stack, and run `uv run --project backend klack-dev-seed`. The command creates the four
documented `dev.*@klack.example` accounts and the `Klack Swagger Demo` workspace, is idempotent,
creates no sessions or email work, and refuses every environment other than development. Open
Swagger at `http://127.0.0.1:8000/docs` with `AUTH_TRUSTED_ORIGIN=http://127.0.0.1:8000`;
its development-only request
interceptor presents the readable CSRF cookie on same-origin mutation requests without weakening
the API's Origin or session-bound CSRF checks.

Switch the trusted origin back to port 3000 for frontend mutations, then recreate the API.
See [browser origins](../docs/development.md#browser-origins) and
[operations](../docs/operations.md) for configuration and process supervision.
