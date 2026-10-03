# File sharing

Channels, private DMs, and quoted replies support up to five attachments per message,
25 MiB each by default, with optional text. The shared composer supports the file picker,
drag-and-drop, pasted images, upload progress, removal, and individual retries. Ready uploads
survive tab reloads for 24 hours. Incomplete local files must be selected again after reload.
The Files tab lists sent attachments with pagination. Text edits preserve attachments;
delete the message to remove its files.

## Development

Apply migrations and rebuild the backend services:

```powershell
docker compose run --rm migrate
docker compose up -d --build api files-worker
```

The development Compose stack shares `backend/.data/files` between API and cleanup worker.
It is ignored by Git and is never exposed as a static web directory. Host-run development
uses `.data/files` relative to the working directory; launch both processes from the same
directory, or configure an absolute `FILES_LOCAL_PATH`.

```powershell
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend klack-files-worker
```

The development default does **not** perform antivirus scans. Set `FILES_SCAN_HOST` to a
reachable ClamAV daemon to enable them; failed/unavailable scans never release files.
Only PNG, JPEG, GIF, and WebP detected and verified by Pillow render inline. Other formats,
including SVG and HTML, are served as downloads with `nosniff` and a restrictive CSP.

## Deployment

For Supabase Storage, use `FILES_STORAGE=supabase`, `FILES_S3_BUCKET=klack-files`,
`SUPABASE_URL` set to the project's HTTPS origin (without an API path), and
`SUPABASE_SERVICE_ROLE_KEY` set to its server-only service-role key. Keep the bucket private.
Klack uses the native Storage API with `x-upsert: false`, because the Supabase S3 endpoint
does not enforce the conditional uploads Klack needs. The S3 access keys are not used in this
mode. API and cleanup-worker processes need the same Supabase settings. The scanner remains
required by default in staging/production. A 10 MiB demo limit is `FILES_MAX_BYTES=10485760`; set the
bucket's limit to match. Supabase's free file allowance is shared across workspaces, so monitor
total storage as well as each workspace's quota.

Staging/production startup requires private S3 or Supabase storage and, by default, a
configured scanner when files are enabled.
Set `FILES_ENABLED=false` if deployment is not ready for file sharing.

The public resume demo explicitly uses `FILES_SCAN_REQUIRED=false` with no scanner host.
Its uploads are **not antivirus-scanned**. Private downloads, authorization, upload limits,
verified raster-image previews, and download-only handling of other formats still apply.
ZIP archives are never extracted by Klack. This is a documented demo limitation, not an
equivalent replacement for antivirus scanning. Setting a scanner host always enables scanning;
failed or unavailable scans continue to reject uploads even when the required flag is false.

| Setting | Default / purpose |
| --- | --- |
| `FILES_ENABLED` | `true` |
| `FILES_STORAGE` | `local` for development; `s3` or `supabase` for deployment |
| `FILES_LOCAL_PATH` | `.data/files` |
| `FILES_S3_BUCKET` | Required private bucket for S3 |
| `FILES_S3_ENDPOINT` | Optional S3-compatible service endpoint |
| `FILES_SCAN_HOST`, `FILES_SCAN_PORT` | ClamAV host and port (`3310`) |
| `FILES_SCAN_REQUIRED` | `true`; explicitly set `false` for the unscanned resume demo |
| `FILES_MAX_BYTES` | `26214400`; supported maximum 100 MiB |
| `FILES_MAX_ATTACHMENTS` | `5`; configurable from 1 to 5 |
| `FILES_WORKSPACE_QUOTA_BYTES` | `5368709120` (5 GiB), including reservations and pending deletion |
| `FILES_UPLOADS_PER_HOUR` | `100` reservations per user per workspace |

Provision the private bucket separately. S3 credentials use the standard AWS credential
chain; prefer a workload role. Grant only the required object get/put/delete permissions
for the bucket prefix. S3-compatible services must support conditional `PutObject` with
`If-None-Match: *`. Do not enable public reads. Configure bucket encryption and backups
according to deployment requirements. If versioning is enabled, configure lifecycle expiry
of noncurrent versions: deleting the current object does not erase historical versions.

Run `klack-files-worker` with the same database, storage configuration, and credentials as
the API. It polls every minute. `klack-files-worker once` runs one bounded cleanup pass.
Back up file storage together with PostgreSQL; do not switch storage backends or buckets
without migrating existing objects. Existing file records retain keys, not backend locations.

The API mediates uploads and downloads, so the browser needs no bucket CORS rules or signed
URLs. Configure upstream request-size and timeout limits for your chosen file limit. The
Next.js proxy buffer is set to 101 MiB. API transfer time is limited to 120 seconds, and four
file operations per API process may use transfer/inspection buffers concurrently. Scale with
these memory and bandwidth costs in mind. Very large/resumable transfers are outside this release.

ClamAV must accept streams at least as large as `FILES_MAX_BYTES`. Keep signatures current
and configure archive/scan limits appropriately. Scanner communication belongs on a private
network. The daemon's content inspection and storage I/O run off the API event loop.

## Consistency and cleanup

Reservations count against quotas before bytes arrive. Uploads are bound to one uploader,
workspace, and channel. Each storage key is written only once. Sending validates readiness,
ownership, expiration, and membership, then atomically attaches the files and writes the
message/realtime event. The retry identifier includes the ordered attachment IDs.

Pending/ready drafts expire after 24 hours; abandoned active transfers expire after one hour.
Removing an in-progress upload aborts the browser request; its durable lease allows later
cleanup even if disconnect handling fails. Physical deletion retries without making files
available again. Deleted reservation records remain at least a day to preserve rate-limit
accounting, then are removed.

Deleting a message immediately removes attachment metadata from message responses and blocks
new downloads. Cleanup removes its objects asynchronously. Previously downloaded copies cannot
be recalled. Downloads always authenticate and check membership; revoked members cannot obtain
new download responses. Uploaded bytes and object keys never appear in realtime events.

Before rolling back revision `20260928_0009`, delete attachment-bearing messages and run
cleanup. The downgrade refuses to silently discard live files or file-only message content.

## Verification

The API tests exercise real SQLAlchemy services on a temporary SQLite database, including
file-only messages, retries, quoted attachments, access revocation, cleanup, previews, and
quotas. Storage tests cover local immutability, S3 request contracts, scanner failures, and
production configuration. Browser tests cover the real Next.js proxy against the fixture API.
PostgreSQL integration tests require the separate disposable database documented in
[development](development.md); S3/ClamAV deployment connectivity must also be verified in
the target environment.
