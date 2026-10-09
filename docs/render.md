# Free Render beta deployment

Klack runs its Next.js frontend, FastAPI backend, email worker, and file cleanup worker
in one Docker web service. A separate Render Key Value instance provides Redis-compatible
typing broadcasts and online leases. PostgreSQL and private uploads live in Supabase. The public
frontend proxies API requests and WebSockets to the backend, keeping authentication on
one origin. Render's local filesystem holds no durable application data.

## Create the service

Push the application changes to GitHub, then create a **Web Service** in Render from
that branch of `SubhorajDas/klack`:

| Field | Value |
| --- | --- |
| Language/runtime | Docker |
| Root directory | Leave blank (repository root) |
| Dockerfile path | `Dockerfile.render` |
| Instance type | Free |
| Health check path | `/health/ready` |

Alternatively, create a Blueprint using the checked-in `render.yaml`. It provisions a
free Key Value instance, connects its private URL as `REDIS_URL`, sets the non-secret
defaults, and generates four distinct application secrets. For a manually
created service, copy its environment settings and generate those four secrets yourself
(at least 32 random characters each).

Add these existing credentials in Render's **Environment** settings. Keep them out of
GitHub, the frontend, and build arguments:

Creating a Web Service manually does **not** import `render.yaml` or your local `.env`.
For the prepared local `.env.render`, replace both `AUTH_TRUSTED_ORIGIN` and
`AUTH_PUBLIC_WEB_ORIGIN` with the exact service URL. Open the service's **Environment**
page, choose **Add from .env**, and paste the file contents. Choose **Save and deploy**
to restart the existing image with those settings. `.env.render` is ignored by Git and
excluded from Docker builds; it is only for importing settings into Render.

For an already deployed service, use `DB_POOL_SIZE=5` and `DB_MAX_OVERFLOW=0` in
Render's Environment page. The original two-connection limit can make simultaneous
channel, unread-state, and alert requests wait for one another. This remains a bounded
pool; each worker also has a separate pool and opens connections only as needed.

The reasoning, validation, and observed before/after timings for this change are
recorded in [Render beta latency improvement](../performance/render-beta-latency.md).

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Supabase **Session pooler** URL, port 5432, using `postgresql+asyncpg://` |
| `REDIS_URL` | Render Key Value **Internal Connection URL**, from the same workspace and region as the web service |
| `SUPABASE_URL` | Project origin, `https://<project-ref>.supabase.co`, without `/rest/v1/` |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-only Supabase service-role key |
| `AUTH_TRUSTED_ORIGIN` | Exact Render service URL, e.g. `https://klack-example.onrender.com` |
| `AUTH_PUBLIC_WEB_ORIGIN` | Same exact service URL, without a trailing slash |
| `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD` | Existing Brevo SMTP credentials |
| `EMAIL_FROM_ADDRESS` | Brevo-verified sender address |

Use `SMTP_PORT=2525`, `SMTP_STARTTLS=true`, and `SMTP_USE_SSL=false`. Render free services
block outbound ports 25, 465, and 587; Brevo supports 2525. Optional voice calls also need
the existing `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` if voice is enabled.

The image runs migrations before starting the services. Do not configure a separate
build/start command or a Render Postgres database. Keep the Supabase bucket `klack-files`
private with its size limit matching `FILES_MAX_BYTES=10485760` (10 MiB). Bucket MIME
restrictions may remain empty to support ZIP files; Klack handles access and previews.

## Connect Redis to an existing web service

Creating or updating a web service directly from GitHub does not apply the Blueprint's
Key Value definition. For the existing `klack-x1h7.onrender.com` deployment:

1. In Render, create a **Key Value** instance named `klack-realtime` using the **Free**
   plan in the same workspace and region as Klack. Choose **noeviction** as the memory
   policy and leave external access disabled.
2. Copy its **Internal Connection URL**. In the existing Klack web service's
   **Environment** page, set `REDIS_URL` to that exact URL and `REALTIME_ENABLED=true`.
   Do not use the local development URL (`redis://127.0.0.1:6379/0`): the Docker web
   image does not run Redis.
3. Choose **Save and deploy**. This restarts the service with the new settings; it
   does not require an application code change or database migration.
4. Sign in with two separate browser sessions. Their authenticated
   `/api/v1/presence` responses should report `available: true`. Keep both sessions
   connected, open the same DM or channel, and check online status and typing in both
   directions. Closing one user's final session should mark them offline; an abrupt
   connection loss expires after the online lease (75 seconds by default).

`available: false` means Redis is unconfigured, unreachable, or still reconnecting.
Check the service logs for `redis_activity_unconfigured` or
`redis_activity_unavailable`. WebSocket `hello` and normal messages can still work
while Redis is unavailable, because message delivery uses PostgreSQL separately.
Read receipts remain stored in PostgreSQL; Redis carries their live refresh events.

## Demo limitations

- `FILES_SCAN_REQUIRED=false`: attachments are **not antivirus-scanned**. ZIP files are
  accepted as downloads and never extracted. Verified raster images may preview inline;
  other formats download with restrictive response headers.
- Free Render services sleep after inactivity. Visitors may wait for the first request
  to wake the app. Email delivery and cleanup resume when the service is awake.
- Each workspace has a 100 MiB upload quota in this deployment. Supabase's shared storage
  and database allowances still apply across all workspaces; monitor usage in its dashboard.
- All processes share the free service's memory. This setup targets a small resume demo.
- Free Key Value data is ephemeral. Online leases rebuild after a Redis restart;
  typing events are transient. Messages and read receipts remain in PostgreSQL.

## Check after deployment

Open `/health/ready`, then sign up and verify the email link uses the Render URL. Check
login, workspace creation, messaging between two browser sessions, realtime updates,
upload/download of an image and ZIP, and attachment deletion. Restart the Render service
and confirm messages and files remain available. Check logs for worker startup and the
intentional `file_antivirus_scanning_disabled` warning.

References: [Render free services](https://render.com/docs/free),
[Render Key Value](https://render.com/docs/key-value),
[Render Blueprint reference](https://render.com/docs/blueprint-spec),
[Brevo SMTP ports](https://help.brevo.com/hc/en-us/articles/10905415650322-Which-SMTP-port-should-I-use-Port-587-465-or-2525).
