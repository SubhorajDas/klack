# Klack backend

The backend is a Python 3.13 FastAPI modular monolith packaged from `src/klack`. It provides the
infrastructure foundation, a complete identity/authentication vertical slice, and workspace
membership authorization with manually shared invitation links.

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

Run backend commands from the repository root with `uv run --project backend ...` so the root
`.env` is discovered consistently. Pass `-c backend/alembic.ini` to Alembic when invoking it from
the root.

For local Swagger exploration, copy `.env.example` to `.env`, set `DEV_SEED_ENABLED=true`, start
the Compose stack, and run `uv run --project backend klack-dev-seed`. The command creates the four
documented `dev.*@klack.example` accounts and the `Klack Swagger Demo` workspace, is idempotent,
creates no sessions or email work, and refuses every environment other than development. Open
Swagger at the exact configured origin, `http://127.0.0.1:8000/docs`; its development-only request
interceptor presents the readable CSRF cookie on same-origin mutation requests without weakening
the API's Origin or session-bound CSRF checks.
