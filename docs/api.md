# API guide

[Documentation index](README.md)

Paths assume the default `/api/v1` prefix. Exact schemas are available from the running
API at `/openapi.json`; interactive `/docs` is development-only.

## Browser authentication API

The versioned identity routes are:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/email-verification/request`
- `POST /api/v1/auth/email-verification/complete`
- `POST /api/v1/auth/password-recovery/request`
- `POST /api/v1/auth/password-recovery/complete`
- `POST /api/v1/auth/refresh`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/logout-all`
- `GET /api/v1/auth/sessions`
- `DELETE /api/v1/auth/sessions/{session_id}`

Bearer credentials are transported only in host-only cookies and are never returned in JSON.
Unsafe auth requests require the configured exact `Origin`. Cookie-authenticated mutations also
require `X-CSRF-Token` to match the readable CSRF cookie and the durable session binding.

Access tokens default to 15 minutes and sessions to a 30-day absolute lifetime. Refresh rotation
has a five-minute default cooldown; an early valid refresh returns `429` with `Retry-After`
without consuming the token. Login, registration, verification, and recovery also consume shared
PostgreSQL subject and IP throttle buckets. Login retains at most ten active sessions per account
by default, revoking the oldest session families first.

## Workspace API

Authenticated users can create workspaces, manage memberships according to their current durable
role, and generate manually shareable invitation links. Workspace authorization is read from
PostgreSQL rather than JWT claims, so removals and role changes take effect immediately.

The versioned workspace routes are:

- `POST /api/v1/workspaces`
- `GET /api/v1/workspaces`
- `GET|PATCH /api/v1/workspaces/{workspace_id}`
- `GET /api/v1/workspaces/{workspace_id}/memberships`
- `GET /api/v1/workspaces/{workspace_id}/memberships/me`
- `PATCH|DELETE /api/v1/workspaces/{workspace_id}/memberships/{user_id}`
- `POST /api/v1/workspaces/{workspace_id}/leave`
- `POST|GET /api/v1/workspaces/{workspace_id}/invitations`
- `POST /api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/rotate`
- `DELETE /api/v1/workspaces/{workspace_id}/invitations/{invitation_id}`
- `POST /api/v1/workspace-invitations/accept`

Invitation links are seven-day, member-only bearer credentials by default. The API reveals a link
only when it is created or rotated, stores only its HMAC digest, and never requires SMTP or email
verification. The inviter must share the link through an external channel. Whoever first redeems
the active link while authenticated becomes its member.

## Channel API

Channels organize a workspace into public or private collaboration areas. Public channels are
discoverable by every workspace member, but joining remains explicit. Private channels are hidden
from ordinary nonmembers; workspace owners and administrators can see their metadata for
governance, while channel content still requires explicit membership. Channel-local roles do not
exist: current durable workspace roles govern channel management.

The versioned channel routes are:

- `POST /api/v1/workspaces/{workspace_id}/channels`
- `GET /api/v1/workspaces/{workspace_id}/channels`
- `GET|PATCH /api/v1/workspaces/{workspace_id}/channels/{channel_id}`
- `POST /api/v1/workspaces/{workspace_id}/channels/{channel_id}/archive`
- `POST /api/v1/workspaces/{workspace_id}/channels/{channel_id}/unarchive`
- `GET /api/v1/workspaces/{workspace_id}/channels/{channel_id}/memberships`
- `GET|PUT|DELETE /api/v1/workspaces/{workspace_id}/channels/{channel_id}/memberships/me`
- `PUT|DELETE /api/v1/workspaces/{workspace_id}/channels/{channel_id}/memberships/{user_id}`

Owners and administrators can create, update, archive, restore, and manage channels. Administrators
cannot remove workspace owners or other administrators from channel membership. Workspace members
can join public channels and leave channels they have joined; private membership is managed by an
owner or administrator. Channel slugs are lowercase, 1 through 80 characters, and unique within a
workspace. The creator joins automatically, visibility changes preserve explicit membership, and
archival is reversible. Workspace creation does not add an automatic `general` channel.

## Messaging API

Explicit channel members can create and read durable channel messages. Authors can edit their own
live messages and soft-delete their own content. Deleted rows remain as body-free tombstones so
conversation ordering stays stable. Archived channels remain readable to their members, reject new
messages and edits, and still allow authors to delete their own messages.

The versioned messaging routes are:

- `POST|GET /api/v1/workspaces/{workspace_id}/channels/{channel_id}/messages`
- `PATCH|DELETE /api/v1/workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}`

History is returned newest first. Use the response's `next_before` value as the next request's
`before` query parameter; page size defaults to 50 and is bounded at 100. Public-channel discovery
does not grant message access: every operation requires current explicit channel membership.
Clients may supply `client_message_id` on creation and must reuse it when retrying an uncertain
response. Messages expose a monotonic `revision` for deduplication and convergence.

## Threads, reactions, read cursors, and direct messages

- Set `parent_message_id` when creating a reply. List replies with
  `GET .../messages?parent_message_id=<root-id>`, using the same `before` pagination.
  The default history contains root messages only. Replies cannot have nested replies, and their
  root must belong to the same channel. Deleted roots retain their threads.
- Messages include `parent_message_id`, `reply_count`, and `reactions` (emoji/user-ID pairs).
  `PUT|DELETE .../messages/{message_id}/reactions/{emoji}` adds/removes your reaction idempotently.
  Supported reactions are 👍, ❤️, 😂, 🎉, 👀, and ✅. Archived channels and deleted messages reject
  reaction changes. Replies and reaction changes use the existing committed realtime delivery.
- `GET|PUT .../channels/{channel_id}/read-cursor` reads/advances your private cursor; PUT accepts
  `{"message_id":"<uuid>"}`. Positions compare `(created_at, id)` and never move backward, including
  concurrent requests. `unread_count` includes live roots and replies from other people after that
  position. The UI marks visible conversations read at the bottom and refreshes badges every 15
  seconds, on visibility changes, and after local reads. These are private positions, not shared
  read receipts. Reading a later message acknowledges all earlier messages in that conversation.
- `POST /api/v1/workspaces/{workspace_id}/direct-messages` accepts `{"user_id":"<uuid>"}` and opens
  or returns the one-to-one conversation for that pair. GET lists only your direct conversations.
  Both people must be current workspace members. Returned channel IDs work with the existing
  messaging, reaction, read-cursor, and WebSocket endpoints.
- DMs are excluded from channel discovery. Administrators cannot inspect, join, rename, archive,
  make public, or change their participants. Workspace removal revokes access; explicitly reopening
  the same pair after workspace rejoining restores participation and existing history.

## Realtime API

Connect to `GET /api/v1/realtime` with the `klack.realtime.v1` WebSocket subprotocol, the normal
access cookie, and the exact trusted browser Origin. Message writes remain on REST. Subscribe with:

```json
{"type":"subscribe","request_id":"<uuid>","workspace_id":"<uuid>","channel_id":"<uuid>"}
```

Only current explicit channel members receive `message.changed` snapshots. Access-token expiry,
session revocation, membership loss, listener failure, and slow-consumer limits close or revoke the
affected stream. After reconnecting, subscribe first, buffer events, reload REST history, and merge
by message ID and revision so there is no history-to-socket race.

## Voice call API

Calls are available only in private one-to-one direct conversations. Reads require browser
session authentication; POST requests also require exact Origin and session-bound CSRF.

| Method | Path (under `/api/v1`) | Purpose |
| --- | --- | --- |
| GET | `/calls?device_id=<uuid>` | Read calling availability and your current call inbox |
| POST | `/calls` | Start with `request_id`, `device_id`, `workspace_id`, and `channel_id` |
| POST | `/calls/{call_id}/{action}` | `accept`, `decline`, `end`, or `heartbeat`, with `device_id` |
| POST | `/calls/{call_id}/connection/token` | Obtain a temporary LiveKit URL/token with `device_id` |
| GET | `/workspaces/{workspace_id}/channels/{channel_id}/calls?device_id=<uuid>` | Read call history |

Reuse the caller-generated `request_id` when retrying creation. `device_id` identifies the browser
instance; acceptance, token issuance, and heartbeats bind the owning device and session. Another
tab belonging to a participant can end an abandoned call. History uses `before` and `next_before`,
defaults to 30 records, and accepts a maximum `limit` of 100.

Without all three LiveKit settings, the inbox reports `enabled: false` and creation returns 503.
Provider API keys stay on the server; only temporary participant credentials reach the browser.
See the [call lifecycle](architecture/README.md#voice-call-lifecycle) for expiry and cleanup.

