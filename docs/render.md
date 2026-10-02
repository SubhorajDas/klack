# Free Render beta deployment

Klack runs its Next.js frontend, FastAPI backend, email worker, and file cleanup worker
in one Docker web service. PostgreSQL and private uploads live in Supabase. The public
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

Alternatively, create a Blueprint using the checked-in `render.yaml`. That file sets
the non-secret defaults and generates four distinct application secrets. For a manually
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

## Demo limitations

- `FILES_SCAN_REQUIRED=false`: attachments are **not antivirus-scanned**. ZIP files are
  accepted as downloads and never extracted. Verified raster images may preview inline;
  other formats download with restrictive response headers.
- Free Render services sleep after inactivity. Visitors may wait for the first request
  to wake the app. Email delivery and cleanup resume when the service is awake.
- Each workspace has a 100 MiB upload quota in this deployment. Supabase's shared storage
  and database allowances still apply across all workspaces; monitor usage in its dashboard.
- All processes share the free service's memory. This setup targets a small resume demo.

## Check after deployment

Open `/health/ready`, then sign up and verify the email link uses the Render URL. Check
login, workspace creation, messaging between two browser sessions, realtime updates,
upload/download of an image and ZIP, and attachment deletion. Restart the Render service
and confirm messages and files remain available. Check logs for worker startup and the
intentional `file_antivirus_scanning_disabled` warning.

References: [Render free services](https://render.com/docs/free),
[Brevo SMTP ports](https://help.brevo.com/hc/en-us/articles/10905415650322-Which-SMTP-port-should-I-use-Port-587-465-or-2525).
