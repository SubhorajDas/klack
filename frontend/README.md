# Klack frontend

Next.js App Router, React, and TypeScript frontend based on the supplied desktop/mobile UI references. All application data comes from the existing FastAPI API. Apply backend migrations through `20261004_0012` before starting the frontend.

See the [full-app quick start](../README.md#quick-start), [API contracts](../docs/api.md), and
[system architecture](../docs/architecture/README.md) for the backend and runtime context.

## Source map and state ownership

| Location | Responsibility |
| --- | --- |
| `src/app/[[...path]]/page.tsx` | App Router catch-all page |
| `src/components/app.tsx` | Session bootstrap, authentication routing, signed-in call-provider lifetime |
| `src/components/workspace-app.tsx` | Workspace shell, navigation, and unread refresh |
| `src/components/conversation.tsx` | Messages, quoted-reply UI, composer, tab-local drafts, and retry-safe sends |
| `src/components/rich-editor.tsx` | Tiptap formatting, Markdown input, code blocks, and attachment nodes |
| `src/components/rich-message.tsx` | Safe rich message rendering, highlighted code, and inline file cards |
| `src/components/direct-messages.tsx` | DM inbox, selection, and search |
| `src/components/calls.tsx` | Global call inbox, controls, LiveKit media, and heartbeat cleanup |
| `src/lib/api.ts` | HTTP requests, CSRF headers, and session recovery |
| `src/lib/api-cache.ts` | Browser-only Redux metadata cache and concurrent request sharing |
| `src/lib/use-conversation.ts` | History loading, pagination, realtime reconciliation, and reconnects |
| `src/lib/messages.ts` | Merge by ID/revision and order messages |
| `src/lib/conversation-activity.ts` | Receipt ordering and typing labels |
| `next.config.ts` | Same-origin API forwarding |
| `tests/` | Browser scenarios and isolated HTTP/WebSocket fixtures |

PostgreSQL-backed API responses own durable state. Message WebSockets deliver snapshots, while
call notifications use a separate HTTP inbox and voice media connects directly to LiveKit.
The global call provider survives conversation navigation; reloading the page ends its media
connection. Drafts and uncertain sends are tab-local and isolated by conversation and user.
For request and recovery flows, see the [architecture guide](../docs/architecture/README.md).

Conversation activity uses the existing authorized socket. One typist is named; multiple typists
show a count, deduplicated across tabs, and expire automatically. Your channel message info lists
who has read it. DMs show grey double ticks after sending and blue double ticks after the peer reads.
Receipts recover on reconnect, visibility changes, and a 15-second poll. A visible conversation
marks its latest message read only at the bottom, outside search and history browsing.

The signed-in shell maintains a presence connection on every screen. Online dots appear in
channel messages, DMs, and People; DM headers show Online/Offline. Status is scoped to contacts,
deduplicated across tabs/devices by Redis leases, and refreshed by live signals and a 15-second
snapshot. A Redis outage displays Status unavailable rather than assuming users are offline.

Workspace, channel, DM, member, and invitation lists share a Redux Toolkit cache through
the existing API helper. Identical requests (including query parameters and session-recovery
policy) reuse successful responses for 10 seconds and share in-flight requests. After that,
visited screens render the retained snapshot immediately while fetching fresh data in the
background. Loading placeholders are reserved for screens without cached data. The cache
holds at most 100 entries in memory; it is not persisted across reloads. Metadata mutations
invalidate metadata, while messages, read cursors, files, and calls preserve it. Sign-out and
authorization failures clear all cached data, and pending responses cannot repopulate a
cleared cache. Failed requests are retried on the next read. Conversations retain the latest
50 messages as a navigation preview, isolated by workspace/channel, and always load
an authoritative HTTP snapshot alongside live reconciliation on every visit. Revocation
removes the preview. Session and membership checks, message history requests, alerts, unread
counts, files, and calls always fetch fresh data. Existing 15-second roster and DM polling
therefore continues to reach the backend. A full reload still verifies the session before
displaying workspace data.

## Run locally

Use Node.js 22.12+ (tested with 24.11). From this directory:

```powershell
npm ci
npm run dev
```

Open **http://127.0.0.1:3000**. Use that exact host rather than `localhost`.

Before starting the backend, set these existing settings in the repository-root `.env`:

```dotenv
AUTH_TRUSTED_ORIGIN=http://127.0.0.1:3000
AUTH_PUBLIC_WEB_ORIGIN=http://127.0.0.1:3000
```

Restart/recreate the API after changing its environment (`docker compose up -d --force-recreate api` if the stack is already running). Otherwise start the backend using the root README. This is a configuration change, not a backend implementation change. Exact-origin security stays enabled; the frontend does not rewrite or bypass Origin checks. Swagger mutations at port 8000 will no longer match the trusted origin while it points at the frontend.

The Next.js server forwards `/api/v1/*` to `http://127.0.0.1:8000`, including WebSocket upgrades. Set `API_ORIGIN` in `frontend/.env.local` to change the upstream. Cookies retain their HttpOnly, SameSite and path attributes. The CSRF cookie is read immediately before each mutation. Access/refresh credentials are never stored in JavaScript or browser storage.

The backend stack must be running for sign-in. No demo account is automatically created. Use registration or the explicitly enabled development seed described in the root README.

## Included

- Responsive sign-in, registration, password recovery, emailed verification links.
- Workspace home, switching, creation and invitation acceptance (`/join#token=…`).
- Channel browsing, public joining, private creation, naming, archival/restoration, member management and leaving.
- Channel history, older-page loading, sending, editing and content-erasing deletion.
- Rich composition with bold, italic, strike, links, lists, quotes, inline code, and highlighted code blocks. Markdown shortcuts work while typing; plain Markdown can be pasted or inserted from the toolbar. The paperclip sits beside Send and inserts files at the cursor, with text above and below each card. Upload progress, retry, removal, file paste/drop, and attachment dragging remain inside the composer.
- Rich drafts and sent messages preserve formatting and attachment order across reloads. Enter sends ordinary text, Shift+Enter inserts a line break, and Ctrl/Cmd+Enter sends from code blocks and lists; Enter inside those blocks continues writing. Editing preserves the existing files and lets users move their cards, but adding or removing files from an already sent message is not supported.
- Inline quoted replies with shared history, saved draft quotes, reactions, and editing/deletion.
- Private one-to-one DMs, persistent read positions, and unread badges.
- Global Alerts inbox at `/activity`, with unread conversation previews from all workspaces and personal DMs, channel/DM filters, search, and individual or filtered bulk mark-as-read. Refreshes every 15 seconds while visible and after observed read/message events. Quoted replies are included; opening an alert opens its conversation. Read state persists on the server.
- One-to-one LiveKit voice calls with a global incoming popup, accept/decline, mute/unmute, hang up, and a paginated Calls history tab. Configure LiveKit only in the backend's root `.env`. The signed-in call inbox polls every two seconds; closed browsers do not receive notifications.
- Responsive DM inbox with URL-addressable conversations, member picker, and conversation/message search. Reloading a DM URL reopens that conversation.
- Unread badges refresh after observed message/read events and every 15 seconds while the page is visible. Temporary request failures preserve the last known count.
- Live socket subscription, heartbeat replies, reconnect with bounded backoff and session recovery.
- Subscribe-before-snapshot reconciliation by ID and revision, including edits/deletions arriving during history loading. Reconnecting reloads the newest page; older pages can be loaded again.
- Retry-safe sends using the original `client_message_id`. Drafts and uncertain sends survive reloads in this tab, isolated by user/workspace/channel. Definitive API rejections unlock the draft for correction. Network/5xx outcomes keep the original payload until delivery is resolved.
- Workspace people, role changes, member removal, single-use invitation links and revocation.
- Account details, email verification requests, active session revocation, and a browser-local compact message preference.
- Search within loaded channel messages and filtering available channels.

The references also contain features that the backend does not support yet: pinned/saved messages, group/video calls, Google/SSO, editable profiles, channel descriptions and notification preferences. These are marked as upcoming where shown; no successful backend action is simulated. Initials are used in place of profile photos. The conversation on the sign-in illustration is decorative sample copy.

## Verification

```powershell
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Browser tests launch an isolated HTTP/WebSocket fixture on port 8100 and a Next.js server on port 3100. They exercise the actual Next proxy, cookie/CSRF transport, live reconciliation, retry IDs, editing/deleting, reconnect/revocation, invites, channel creation, mobile navigation, tab-local drafts, quoted replies and jumps to older originals, reactions, persistent unread cursors, and DM deep links/search. They do not use real accounts or contact the production API. Screenshots and failure traces are written under the ignored `test-results/` directory. Backend integration still requires the real running FastAPI/PostgreSQL stack.

## Production

Run `npm run build` then `npm start` behind an HTTPS reverse proxy. Set the backend's exact trusted/public origins to the deployed frontend origin and enable its secure cookies. Forward `/api/v1/realtime` with WebSocket upgrade support to the backend; hosts without persistent WebSocket proxy support need a dedicated same-origin reverse proxy. Do not deploy this as a static export. `API_ORIGIN` is read when the Next configuration is loaded, so use the correct value at build and server start. Follow the official [Next.js self-hosting guide](https://nextjs.org/docs/app/guides/self-hosting) for deployment hardening.

Personal DMs use `/dms` and `/dms/:conversation`, with one history per pair across workspaces.
The rail lists all workspaces; its plus opens create/join actions. DM, workspace, and channel
badges share `/unread-counts`; the bell displays their combined unread-message total. Counts
refresh every five seconds while visible, on focus, and after read/message events across tabs.
Workspace counts exclude personal DMs. People stays in the header and account actions in the profile.
