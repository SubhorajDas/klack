# Klack backend

The backend is a Python 3.13 FastAPI application packaged from `src/klack`. It currently
provides the infrastructure foundation only.

Key guarantees:

- Settings are validated at startup and injected into the application container.
- Importing the package does not connect to PostgreSQL.
- One async SQLAlchemy engine exists per process.
- Request/task scopes receive their own `AsyncSession`; dependencies never auto-commit.
- Alembic is the only schema-management mechanism.
- Production logs are structured JSON.
- Liveness is process-only; readiness is a bounded PostgreSQL query.

Run backend commands from the repository root with `uv run --project backend ...` so the
root `.env` file is discovered consistently. Pass `-c backend/alembic.ini` to Alembic when
invoking it from the root.
