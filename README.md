# Klack

Klack is a collaboration app with workspaces, channels, quoted replies, private direct
messages, and one-to-one voice calls. It combines a Next.js/React frontend with a Python/FastAPI
modular monolith and PostgreSQL. LiveKit provides optional voice media transport.

## What works today

- Account registration, secure browser sessions, email verification, password recovery, and session management.
- Workspaces with owner/admin/member roles and single-use invitation links.
- Public and private channels with explicit membership and reversible archival.
- Durable messages with pagination, edits, content-erasing deletion, quoted replies, and reactions.
- Rich message composition with Markdown shortcuts, code blocks, and attachments between paragraphs.
- Retry-safe sends and authenticated realtime delivery across API processes.
- Private file sharing in channels, DMs, and quoted replies, with upload progress, image previews, and downloads.
- Private one-to-one DMs, persistent read positions, and unread counts.
- Redis typing indicators, read receipts, and online status across tabs and devices.
- Alerts inbox for unread channel messages, DMs, and quoted replies, with search and mark-as-read actions.
- Voice calls with accept/decline, microphone controls, history, and incoming notifications while the app is open.
- Responsive desktop/mobile layouts and tab-local message drafts.

Pinned/saved messages, group/video calls, SSO, and editable profiles are not
implemented. Search filters loaded messages; it is not a full-history search service.

## Quick start

You need Docker Desktop with Compose v2 and Node.js 22.12+ for the frontend. Host backend
development additionally requires Python 3.13 and uv (`>=0.11.32,<0.12`). Examples use PowerShell.

From the repository root, create your local configuration if you do not already have one:

```powershell
Copy-Item .env.example .env
```

Edit these values in `.env` **before starting the API**:

```dotenv
AUTH_TRUSTED_ORIGIN=http://127.0.0.1:3000
AUTH_PUBLIC_WEB_ORIGIN=http://127.0.0.1:3000
```

Start PostgreSQL, the migration job, the API, and the identity maintenance worker:

```powershell
docker compose up -d --build
```

Compose waits for migrations before starting the API. It does not start the frontend. In another
terminal, from the repository root:

```powershell
cd frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:3000**, register an account, and create a workspace and channel.
Workspace creation does not automatically create a `general` channel. Use the exact host above;
`localhost` will not match the configured trusted origin. SMTP and LiveKit are optional for
core messaging. Existing installations should keep their `.env` and apply all migrations.

| Local endpoint | Purpose |
| --- | --- |
| `http://127.0.0.1:3000` | Browser application |
| `http://127.0.0.1:8000/health/live` | API process liveness |
| `http://127.0.0.1:8000/health/ready` | PostgreSQL readiness |
| `http://127.0.0.1:8000/docs` | Development API explorer |
| `http://127.0.0.1:8000/openapi.json` | REST schema |

Swagger mutations require switching the trusted origin to port 8000; see
[browser origins](docs/development.md#browser-origins). If you change the root `.env` after startup,
recreate the API with `docker compose up -d --force-recreate api`.

See [file-sharing setup](docs/files.md) for storage, scanning, limits, and the cleanup worker.

## Optional services

**Email:** set `SMTP_HOST` and `EMAIL_FROM_ADDRESS` for verification and password-recovery
messages. SMTP username/password must be supplied together when used. Compose runs the identity
worker; without SMTP it still performs cleanup. Workspace invitations are manually shared and
do not require email delivery.

**Voice:** set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` in the root `.env`, then
recreate the API. Provider credentials stay on the backend. Open a DM and select **Start call**.
Microphone access requires localhost or HTTPS. Incoming calls require an open signed-in app;
there are no offline push notifications. Reloading disconnects media, and abandoned calls expire.
See [voice-call architecture](docs/architecture/README.md#voice-call-lifecycle) for timing and recovery.

## Documentation

| Guide | What it covers |
| --- | --- |
| [Documentation index](docs/README.md) | Reading paths and source references |
| [Development](docs/development.md) | Host setup, demo accounts, migrations, checks, troubleshooting |
| [API guide](docs/api.md) | Authentication, authorization, messaging, realtime, and call contracts |
| [Architecture](docs/architecture/README.md) | Runtime diagram, ownership, data model, and request flows |
| [Operations](docs/operations.md) | Configuration, deployment, health, and failure recovery |
| [Free Render beta](docs/render.md) | Supabase database/files, Brevo email, and demo limitations |
| [Backend](backend/README.md) | Backend layout and implementation guarantees |
| [Frontend](frontend/README.md) | UI behavior, state ownership, proxy, and browser tests |
| [Architecture decisions](docs/adr/README.md) | Accepted decisions and their tradeoffs |
| [Performance](performance/README.md) | HTTP workload and measurement limits |

## Repository map

```text
backend/          FastAPI application, Alembic migrations, Python tests
frontend/         Next.js app, UI components, unit and browser tests
docs/             Development, API, operations, architecture, and ADRs
performance/      Local load-test runner and reports
compose.yaml      Development PostgreSQL, migrations, API, and identity worker
.env.example      Root configuration template
```

For quality gates and verification, follow the [development guide](docs/development.md).
The Compose stack is development-only; see [operations](docs/operations.md) before deployment.
