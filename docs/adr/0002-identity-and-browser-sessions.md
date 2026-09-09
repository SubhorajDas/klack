# ADR 0002: Establish identity and secure browser sessions

- Status: accepted
- Date: 2026-08-23

## Context

Klack needs registration and independently revocable browser sessions before workspace
membership or any other product authorization can be built. Authentication credentials are a
high-value boundary: plaintext tokens must not become durable data, logout must take effect
immediately, and browser cookie transport needs explicit cross-site request protections.

There is no mail delivery, frontend, shared rate-limit store, or worker infrastructure yet.
Those absences constrain the first cohesive slice without justifying speculative services.

## Decision

Treat normalized email addresses as case-insensitive product identifiers. Validate syntax,
normalize internationalized domains, case-fold the whole address, and enforce database
uniqueness. Registration accepts passwords from 12 through 128 Unicode characters, hashes them
with Argon2id, and immediately creates an authenticated session. Email verification is modeled
as nullable account state but is not required until a delivery and verification flow exists.

Run Argon2 work outside the event loop behind a bounded process-level capacity limiter. Do not
abandon the worker thread on request cancellation, because doing so would release limiter
capacity while native hashing still consumes memory and CPU.

Put a 15-minute HS256 access JWT in a host-only, HttpOnly, SameSite=Lax cookie. Require issuer,
audience, token type, subject, session, token ID, issued-at, not-before, and expiry claims. Every
access-authenticated request also loads the durable user and session, so account disabling,
logout, and session revocation take effect immediately rather than waiting for JWT expiry.

Represent each login as a 30-day absolute-lifetime session. Refresh credentials are opaque
high-entropy values; PostgreSQL stores only domain-separated HMAC-SHA256 digests. Retain token
history and rotate under `SELECT FOR UPDATE`. A replay of a consumed token revokes the entire
session family and commits that revocation before returning an error. Parallel refresh calls
therefore deliberately invalidate the family; browser clients must single-flight refresh.

Enforce a durable per-session refresh cooldown, five minutes by default. A valid early request
returns `429 refresh_rate_limited` with `Retry-After` and does not consume the credential. Validate
configuration and derive an effective interval from each session's actual absolute lifetime so
no session can retain more than 10,000 refresh-token rows, including sessions created under older
configuration.

Use host-only Secure cookies in staging and production. Narrow the refresh cookie to the
versioned auth path and never return credentials in JSON or browser storage. Require one exact
configured `Origin` on unsafe auth requests, canonicalized with UTS #46/IDNA browser-compatible
host rules. Cookie-authenticated mutations additionally require a readable CSRF cookie and
matching `X-CSRF-Token` header whose digest is bound to the durable session.

Expose registration, login, refresh, current-user, logout, logout-all, active-session listing,
and individual session revocation below `/api/v1/auth`. Return non-cacheable Problem Details with
stable error codes and correlation IDs.

## Consequences

- PostgreSQL remains the source of truth for identities and immediate revocation.
- Normal authenticated requests incur a small session lookup; this buys deterministic revocation.
- Clients must single-flight refresh and honor `Retry-After` for early rotation attempts.
- Refresh history is bounded per session, but expired session/token rows still require a global
  retention cleanup mechanism.
- Changing email casing semantics or token-family behavior later requires an explicit migration
  and compatibility plan.
- Staging and production need two independent non-example secrets of at least 32 characters,
  HTTPS, and secure cookies.

## Deferred decisions

- Email verification, resend, password reset, and account recovery wait for mail delivery and
  background work.
- Shared login/registration throttling, active-session caps, and global expired-row cleanup wait
  for an edge/shared store and worker contract. The refresh cooldown is intentionally not
  described as complete abuse prevention.
- Key rotation or asymmetric access-token signing waits for a second verifier or operational need.
- Trusted proxy client-IP handling and user-friendly device naming wait for deployment topology
  and frontend requirements.

## Rejected alternatives

- Browser local storage was rejected because it exposes bearer credentials to script access.
- A single overwritten refresh hash was rejected because it cannot identify replayed old tokens.
- Stateless logout was rejected because access would remain valid until JWT expiry.
- Password composition rules were rejected in favor of length and modern password hashing.
- Adding Redis solely for this milestone was rejected until a shared limiter has a concrete
  deployment and failure-mode contract.
