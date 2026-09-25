# 0009: Private voice calls with LiveKit

Status: Accepted

LiveKit handles WebRTC microphone tracks; Klack owns private-DM authorization, invitations,
state transitions, history, and participant exclusivity. The backend alone holds provider
credentials. Tokens permit a single identity to join one random call room, publish microphone
audio, and subscribe. They expire after 60 seconds. Recording, data publishing, video, screen
sharing, and channel/group calls are not enabled by the issued grants.

Calls transition from ringing to active, declined, cancelled, or missed. Ringing expires after
45 seconds. Active participants renew a 60-second lease every ten seconds while connected.
Session revocation, disabled accounts, membership loss, and expired leases end the call.
History survives leaving a call, but reading it still requires conversation membership.
Call creation uses its caller-generated UUID as an idempotency key.

Workspace locks coordinate with membership removal. Ordered user locks and unique user seats
prevent simultaneous calls across conversations and workspaces. Call-row locks serialize accept,
decline, expiry, and end. A recipient may answer on only one browser instance; tokens and
heartbeats require the answering instance and authenticated session. A participant can end a
call from another tab to recover from a closed or reloaded tab.

The global React provider outlives conversation navigation and owns media cleanup. A two-second
authenticated HTTP inbox makes incoming calls visible across workspaces without coupling media
to channel message subscriptions. This deliberately favors a small, independently testable
first slice; user-addressed WebSocket events and offline push can replace polling later.
There are no notifications when the app is closed, and browser suspension may expire leases.

Every API process runs an idempotent maintenance loop. Bounded, skip-locked batches sweep active
calls and delete ended rooms. Media cleanup is durable and retried; deletion repeats during the
remaining join-token lifetime so delayed clients cannot keep recreating an ended room. Cloud
API failures do not roll back local hang-up or release of participant seats. With provider
outages, server-enforced media removal waits for cleanup to recover; cooperative clients stop
audio as soon as call state ends or control connectivity is lost.

Migration 20260925_0008 adds calls and participant seats without changing message storage.
Deploy migrations before the API and frontend. Without all three LiveKit settings, the inbox
reports calling disabled and call creation returns 503. Credentials are never returned to the
frontend; the connection endpoint returns only a temporary participant token and server URL.
