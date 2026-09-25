# ADR 0007: Deliver committed message changes over authenticated WebSockets

- Status: accepted
- Date: 2026-09-11

## Context

Durable channel messages and REST history already exist. Clients need low-latency delivery across
multiple API processes without making a socket, process-local memory, or an external broker the
source of truth. A connection may outlive a short access token or a channel membership, and a
browser WebSocket handshake carries cookies but cannot set the existing CSRF header.

## Decision

Keep message creation, editing, and deletion on the existing REST API. Add a client-generated UUID
for retry-safe creation and a monotonically increasing revision for state convergence. Reusing a
client identifier with the same body returns the original message; different content is rejected.

Append a body-free `message.changed` event and call PostgreSQL `pg_notify` in the same transaction
as each real message change. PostgreSQL releases the notification only after commit. Every API
process listens on a dedicated connection and forwards the current durable message snapshot to its
own local sockets. Event rows contain identifiers and revisions, never message text, and are
deleted in bounded retention batches.

Expose `/api/v1/realtime` using the `klack.realtime.v1` WebSocket subprotocol. Authenticate the
HttpOnly access cookie, require the exact trusted Origin, and reject credentials in URLs or message
frames. Clients explicitly subscribe with both workspace and channel IDs. Subscription and
delivery authorization resolve current durable session, workspace membership, and explicit channel
membership. Authorization is periodically rechecked, access-token expiry closes the connection,
and a failed PostgreSQL listener closes local sockets so clients cannot silently miss live events.

Use bounded per-process connection counts, subscription counts, frame sizes, and outbound queues.
Slow consumers are disconnected with a retryable close code. Delivery is duplicate-tolerant rather
than exactly once; clients converge by message ID and revision.

On connection or listener loss, clients reconnect, subscribe, buffer events, refresh REST history,
and merge the buffered snapshots. PostgreSQL and REST remain the recovery path.

## Consequences

- A message event cannot precede the transaction that made its referenced state durable.
- Every API replica receives cross-process signals without requiring Redis at the current scale.
- Membership and session revocation take effect on delivery and within the configured periodic
  recheck interval even when no messages arrive.
- Listener failure is visible as a reconnect instead of an undetected delivery gap.
- Message bodies have one durable copy and deletion does not leave text in the realtime event table.
- Realtime database reads increase with locally subscribed recipients; a measured throughput
  problem may justify a batch authorization projection or external broker later.

## Rejected alternatives

- WebSocket message mutations were rejected because they would duplicate the established REST
  authorization, CSRF, validation, transaction, and error contracts.
- Process-local publication was rejected because it cannot reach sockets on another API replica.
- Uncommitted post-response publication was rejected because crashes create missing or phantom
  events around the commit boundary.
- Adding Redis immediately was rejected because PostgreSQL already provides transactional
  notification and current requirements do not justify another mandatory service.
- Putting message bodies in the event table was rejected because it duplicates sensitive content
  and weakens content-erasing deletion.
