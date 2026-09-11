# ADR 0005: Establish channels and channel memberships

- Status: accepted
- Date: 2026-09-11

## Context

Klack has a durable workspace authorization boundary but no way to divide a workspace into
topic-specific collaboration areas. Messaging, realtime delivery, and presence need a stable
channel identity, discoverability policy, and membership boundary before they can be introduced.

Channel visibility and channel membership answer different questions. Visibility determines who
can discover channel metadata. Membership represents an explicit join or subscription and will
later be the prerequisite for posting and accessing channel content. Treating every workspace
member as an implicit member of every public channel would erase that distinction and make future
notification, unread-state, and posting behavior ambiguous.

## Decision

Add a `channels` feature module inside the modular monolith. It owns channel and channel-membership
tables and follows the existing domain, application, infrastructure, and API dependency direction.
It may reference workspace and identity IDs through database foreign keys but must not create
cross-module ORM relationships.

Every channel belongs to exactly one workspace and has a canonical lowercase slug from 1 through
80 characters. A slug is unique within its workspace, including while a channel is archived.
Channels are addressed by UUID. Creating a channel atomically adds its creator as the first
explicit channel member. Workspace creation does not create an automatic `general` channel.

Use two visibility values:

- `public`: every current workspace member may discover the channel. Discovery does not make the
  user a channel member; they must explicitly join before future posting or subscription behavior
  applies.
- `private`: ordinary workspace members may discover the channel only when they are explicit
  channel members. Workspace owners and administrators may see private-channel metadata for
  governance, but that visibility alone must not grant access to future channel content.

Channel membership has no channel-local role. Durable workspace roles remain the authorization
source. Workspace owners and administrators may create, update, archive, unarchive, and manage
channels and their memberships. An administrator may not remove a workspace owner or another
administrator from a channel. Workspace members may join public channels themselves and leave
channels they have joined; they cannot self-join a private channel. The same hidden-not-found
behavior used at the workspace boundary prevents an ordinary nonmember from probing private
channel metadata.

Changing a channel from public to private, or back again, retains all explicit memberships. A
visibility change never implicitly joins or removes users. Channel removal is represented by a
soft archive with actor and timestamp metadata. The API does not hard-delete channels, so future
messages cannot be orphaned and an archived channel can be restored without reconstructing its
memberships.

Channel-membership rows carry `workspace_id` as well as `channel_id` and `user_id`. Composite
foreign keys enforce that both the channel and user membership belong to that same workspace.
Removing a workspace membership cascades its channel memberships, and removing a workspace
cascades its channels and channel memberships. PostgreSQL uniqueness is the final guard against
duplicate channel slugs and duplicate memberships.

Resolve workspace roles and channel membership from PostgreSQL rather than access-token claims.
Every channel mutation acquires cooperating locks in this order, skipping targets that do not
apply to the operation:

1. Workspace.
2. Actor workspace membership.
3. Channel.
4. Target workspace membership.
5. Target channel membership.

Expose the following routes below `/api/v1`:

- `POST /workspaces/{workspace_id}/channels`
- `GET /workspaces/{workspace_id}/channels`
- `GET /workspaces/{workspace_id}/channels/{channel_id}`
- `PATCH /workspaces/{workspace_id}/channels/{channel_id}`
- `POST /workspaces/{workspace_id}/channels/{channel_id}/archive`
- `POST /workspaces/{workspace_id}/channels/{channel_id}/unarchive`
- `GET /workspaces/{workspace_id}/channels/{channel_id}/memberships`
- `GET /workspaces/{workspace_id}/channels/{channel_id}/memberships/me`
- `PUT /workspaces/{workspace_id}/channels/{channel_id}/memberships/me`
- `DELETE /workspaces/{workspace_id}/channels/{channel_id}/memberships/me`
- `PUT /workspaces/{workspace_id}/channels/{channel_id}/memberships/{user_id}`
- `DELETE /workspaces/{workspace_id}/channels/{channel_id}/memberships/{user_id}`

Use `PUT` for membership creation so joining or adding an existing member is safely retryable. All
cookie-authenticated mutations retain the existing exact-Origin and session-bound CSRF
requirements.

## Consequences

- Public channels can be browsed without silently subscribing every workspace member.
- Private-channel metadata is protected from ordinary workspace members while owners and
  administrators retain the minimum governance view needed to manage the workspace.
- Removing a user from a workspace also removes every channel membership for that workspace at
  the database boundary.
- Future message authorization can require explicit channel membership without redefining public
  channel discovery.
- A rename retains the channel's stable UUID, while archival does not free its slug for reuse.
- The creator cannot accidentally create a private channel that nobody belongs to.
- Application lock ordering is part of the concurrency contract; direct ad-hoc mutations can
  bypass the service-level authorization guarantees and are unsupported.

## Deferred decisions

- Messages, threads, files, reactions, search, and retention policies.
- Realtime delivery, presence, typing indicators, notifications, and unread state.
- Channel-local roles, per-channel permission overrides, guests, and external collaborators.
- Automatic default channels and required-membership channels.
- Channel discovery pagination and full-text search.
- Frontend channel browsing and management flows.

## Rejected alternatives

- Automatically treating every workspace member as a member of every public channel was rejected
  because discovery, subscription, and future posting access are separate concerns.
- Allowing ordinary members to self-join private channels was rejected because it would make
  `private` equivalent to undiscoverable-but-public.
- Giving owners and administrators implicit access to future private-channel content was rejected
  because governance metadata visibility does not require participation in confidential
  conversations.
- Hard deletion was rejected because future messages and audit history need a stable parent.
- Creating `general` during workspace creation was rejected because it would couple the existing
  workspace transaction to the new channels module before required/default channels are designed.
