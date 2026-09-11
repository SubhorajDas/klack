# ADR 0004: Establish workspaces, memberships, and invitation links

- Status: accepted
- Date: 2026-09-09

## Context

Klack has durable browser authentication but no workspace authorization boundary. Workspace
creation, membership changes, and invitation acceptance must remain correct across API replicas
and concurrent requests before channels or messaging can be introduced.

Email verification and recovery actions already use an optional SMTP-backed outbox, but no email
delivery service is currently configured. Workspace membership therefore cannot depend on email
verification or email delivery. There is also no public user directory or other verified contact
identifier that can target an invitation to a particular person.

## Decision

Add a `workspaces` feature module inside the modular monolith. It owns workspace, membership, and
invitation tables and follows the existing domain, application, infrastructure, and API dependency
direction. It may reference identity user IDs through database foreign keys but must not create
cross-module ORM relationships.

Any authenticated, enabled identity may create a workspace. Creation inserts the workspace and an
`owner` membership for its creator in one transaction. Workspace names are trimmed Unicode text
from 1 through 100 characters, are not globally unique, and are addressed by UUID.

Use three roles: `owner`, `admin`, and `member`. Multiple owners are allowed, but every extant
workspace must retain at least one. Owners may rename a workspace, manage roles and memberships,
and manage invitations. Admins may create and revoke member invitations and remove ordinary
members. Members may read workspace membership and leave. Invitation links grant only the
`member` role; promotion is a separate owner action.

Resolve membership from PostgreSQL rather than access-token claims. Reads hide both missing
workspaces and outsider access behind `workspace_not_found`. Mutations lock the workspace first,
then re-read the actor membership, then lock any target membership or invitation. All cooperating
workspace mutation paths use this order. This makes authorization changes linearizable and
serializes the application-enforced last-owner invariant.

Represent an invitation as a seven-day, single-use, revocable bearer link. Its credential contains
a UUID selector and a high-entropy secret. PostgreSQL stores only a purpose-bound HMAC-SHA256
digest under an independent workspace invitation secret. The creation or rotation response returns
the link exactly once with `Cache-Control: no-store`; it is never logged. The browser-facing link
places the token in the URL fragment, and the frontend submits it in a strict JSON request after
authentication. Exact-origin and session-bound CSRF validation protect acceptance and every other
cookie-authenticated mutation.

Invitation links are not bound to an email address or user ID. The first authenticated non-member
to present an active link receives the membership. Rotation revokes the prior invitation and issues
a new credential. Acceptance locks and revalidates durable invitation state before atomically
creating the unique membership and consuming the invitation.

No invitation email outbox or delivery worker is added in this milestone. Email notifications may
later deliver the same bearer link, but delivery is not part of authorization and must not become a
prerequisite for workspace use.

## Consequences

- Workspace authorization changes take effect on the next database-backed request.
- A manually shared invitation can be forwarded; possession of the active bearer link is the
  invitation authority. Short lifetime, single use, revocation, rotation, and member-only grants
  limit that risk.
- The last-owner guarantee depends on every application mutation taking the workspace lock. Direct
  ad-hoc SQL can violate it; a deferred PostgreSQL constraint trigger may be added if operations
  later require a database-hard guarantee.
- PostgreSQL uniqueness remains the final guard against duplicate memberships even when friendly
  application checks race.
- Account verification and SMTP configuration do not block workspace creation or joining.
- Direct user invitations require a future trusted user directory or verified delivery channel.

## Deferred decisions

- Workspace archival/deletion and ownership recovery.
- Custom roles, granular permissions, guest access, domains, and seat limits.
- Email or in-product invitation notifications.
- Public usernames, profiles, and a searchable user directory. Until a workspace-safe display
  identity is designed, membership responses intentionally expose stable user IDs rather than
  unverified email addresses.
- Invitation-history pagination, an active-link cap, rate limiting, and bounded retention cleanup.
  The first release exposes the complete per-workspace history and therefore is not intended for
  untrusted high-volume invitation generation.
- Channels, messaging, and realtime behavior.

## Rejected alternatives

- Requiring verified email was rejected because no delivery service currently lets users complete
  verification.
- Addressing invitations to unverified email strings was rejected because an account can claim an
  address without proving control of it.
- Returning reusable or plaintext-stored invitation secrets was rejected because database or log
  readers would gain workspace access.
- Adding workspace purposes or foreign keys to identity-owned email-action tables was rejected
  because invitations are workspace state and do not require email delivery.
