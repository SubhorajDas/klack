# ADR 0003: Complete identity verification, recovery, throttling, and maintenance

- Status: accepted
- Date: 2026-08-24

## Context

ADR 0002 established registration, password login, and independently revocable browser sessions,
while explicitly deferring email verification and recovery, shared abuse controls, active-session
caps, and global retention cleanup. Those controls now have concrete API, PostgreSQL, and worker
boundaries. They must work across API replicas without adding a second state service, avoid
persisting bearer action tokens in plaintext, and remain safe under concurrent workers.

## Decision

Use single-use, purpose-bound email action tokens for verification and password recovery. The
browser receives the raw token only through email. PostgreSQL stores an HMAC-SHA256 digest and a
random selector; verification locks durable token and user state before consumption. Recovery
validates and hashes the new password outside a database transaction, re-locks and revalidates the
action, marks the email verified, changes the password, and revokes every active session.

Queue email atomically with the account action in a transactional outbox. Because delivery needs
the raw bearer value, encrypt the queued payload with AES-256-GCM using key material derived from
the independent action secret and bind the ciphertext to recipient and purpose. A separate worker
claims eligible rows using `FOR UPDATE SKIP LOCKED`, commits a bounded lease, performs SMTP outside
the transaction, then records success or a sanitized failure code. Failures use capped exponential
backoff; expired, used, or revoked actions are never claimed.

Keep login, registration, recovery, and verification throttles in PostgreSQL. Consume both a
purpose-specific subject bucket and an IP bucket in deterministic order. Store only
domain-separated HMAC keys, never raw email addresses, IP addresses, or action tokens. Serialize
bucket mutation with PostgreSQL transaction advisory locks and return a committed `429` with
`Retry-After` when either bucket blocks. This shared state is deliberately fixed-window and
PostgreSQL-backed; no Redis dependency is introduced for identity alone.

Limit active sessions per user, ten by default. Login locks all currently active sessions in a
stable database order and revokes the oldest excess session families before inserting the new
session. Registration creates the first session directly. Revocation includes every still-active
refresh credential in the affected family.

Run global cleanup in the same separate maintenance process. Delete old sent outbox rows, expired
or consumed action rows, expired or long-revoked sessions (cascading refresh history), and stale
rate-limit buckets. Drain each category in configured bounded batches and commit every batch. Keep
a seven-day retention period by default so recent operational state remains inspectable.

Expose the maintenance process as `klack-identity-worker` with continuous, one-shot, delivery-only,
and cleanup-only modes. SMTP is optional at application startup: when absent, the continuous worker
still performs cleanup and leaves encrypted outbox work queued for a configured delivery worker.
Staging and production require an independent action secret and an HTTPS public web origin.

## Consequences

- Verification, password recovery, throttling, session caps, and cleanup work across API replicas.
- PostgreSQL is both the identity source of truth and the shared coordination point; authentication
  remains available only while it can durably consume its throttle buckets.
- Email provider latency cannot hold account or action transactions open.
- Rotating the action secret invalidates outstanding action-token verification and prevents old
  encrypted outbox payloads from being delivered; rotation therefore needs an explicit drain or
  revocation procedure.
- Operators must run at least one maintenance worker and configure SMTP for actual email delivery.
- Cleanup is retention-based rather than immediate, preserving a short audit/debugging window while
  bounding unreferenced history globally.

## Rejected alternatives

- Plaintext action tokens in the outbox were rejected because database readers would gain active
  account-recovery credentials.
- Sending SMTP in the API transaction was rejected because provider latency and retries would hold
  database resources and make registration success ambiguous.
- Per-process in-memory throttles were rejected because they are bypassed by replica changes and do
  not provide a shared `Retry-After` contract.
- Adding Redis solely for these limits was rejected because PostgreSQL already provides durable
  locking and the expected authentication volume does not justify another critical dependency.
- Deleting expired state in request handlers was rejected because cleanup work and latency would be
  coupled to unrelated user traffic.