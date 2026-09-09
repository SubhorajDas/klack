# Klack backend

The backend is a Python 3.13 FastAPI modular monolith packaged from `src/klack`. It provides the
infrastructure foundation and a complete identity/authentication vertical slice.

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

The identity module lives at `src/klack/modules/identity` and follows the domain/application/
infrastructure/API dependency direction. Its schema is introduced by
the `20260823_0001` and `20260824_0002` Alembic revisions. The accepted security decisions are
recorded in `docs/adr/0002-identity-and-browser-sessions.md` and
`docs/adr/0003-identity-security-followups.md`.

Run backend commands from the repository root with `uv run --project backend ...` so the root
`.env` is discovered consistently. Pass `-c backend/alembic.ini` to Alembic when invoking it from
the root.
