# Klack frontend

Next.js App Router, React, and TypeScript frontend based on the supplied desktop/mobile UI references. All application data comes from the existing FastAPI API. Apply backend migrations through `20260925_0008` before starting the frontend.

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
- Paginated thread replies with independent drafts, reactions, and reply editing/deletion.
- Private one-to-one DMs, persistent read positions, and unread badges.
- One-to-one LiveKit voice calls with a global incoming popup, accept/decline, mute/unmute, hang up, and a paginated Calls history tab. Configure LiveKit only in the backend's root `.env`. The signed-in call inbox polls every two seconds; closed browsers do not receive notifications.
- Responsive DM inbox with URL-addressable conversations, member picker, and conversation/message search. Reloading a DM URL reopens that conversation.
- Unread badges refresh after observed message/read events and every 15 seconds while the page is visible. Temporary request failures preserve the last known count.
- Live socket subscription, heartbeat replies, reconnect with bounded backoff and session recovery.
- Subscribe-before-snapshot reconciliation by ID and revision, including edits/deletions arriving during history loading. Reconnecting reloads the newest page; older pages can be loaded again.
- Retry-safe sends using the original `client_message_id`. Drafts and uncertain sends survive reloads in this tab, isolated by user/workspace/channel/thread. Definitive API rejections unlock the draft for correction. Network/5xx outcomes keep the original payload until delivery is resolved.
- Workspace people, role changes, member removal, single-use invitation links and revocation.
- Account details, email verification requests, active session revocation, and a browser-local compact message preference.
- Search within loaded channel messages and filtering available channels.

The references also contain features that the backend does not support yet: uploads, pinned/saved messages, presence, group/video calls, Google/SSO, editable profiles, channel descriptions and notification preferences. These are marked as upcoming where shown; no successful backend action is simulated. Other members are labeled by shortened IDs because roster/message APIs do not return names or avatars. Call participants use their email's local part. Initials are used in place of invented profile photos. The conversation on the sign-in illustration is decorative sample copy.

## Verification

```powershell
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Browser tests launch an isolated HTTP/WebSocket fixture on port 8100 and a Next.js server on port 3100. They exercise the actual Next proxy, cookie/CSRF transport, live reconciliation, retry IDs, editing/deleting, reconnect/revocation, invites, channel creation, mobile navigation, tab-local drafts, thread pagination/editing, reactions, persistent unread cursors, and DM deep links/search. They do not use real accounts or contact the production API. Screenshots and failure traces are written under the ignored `test-results/` directory. Backend integration still requires the real running FastAPI/PostgreSQL stack.

## Production

Run `npm run build` then `npm start` behind an HTTPS reverse proxy. Set the backend's exact trusted/public origins to the deployed frontend origin and enable its secure cookies. Forward `/api/v1/realtime` with WebSocket upgrade support to the backend; hosts without persistent WebSocket proxy support need a dedicated same-origin reverse proxy. Do not deploy this as a static export. `API_ORIGIN` is read when the Next configuration is loaded, so use the correct value at build and server start. Follow the official [Next.js self-hosting guide](https://nextjs.org/docs/app/guides/self-hosting) for deployment hardening.
