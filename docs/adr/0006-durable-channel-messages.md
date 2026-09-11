# ADR 0006: Add durable channel messages before realtime delivery

- Status: accepted
- Date: 2026-09-11

## Context

Channels now provide stable workspace containment, visibility, archival, and explicit membership.
Klack needs its first durable collaboration content without coupling message correctness to a live
connection. WebSocket connections can later reduce delivery latency, but they cannot replace
history pagination, reconnect recovery, authorization, or committed PostgreSQL state.

Channel discovery and channel-content access are deliberately different. Public-channel metadata
is visible to every workspace member, while posting and reading content requires explicit channel
membership. Workspace owners and administrators may inspect private-channel metadata for
governance, but that alone does not grant access to its messages.

## Decision

Add a `messaging` module following the existing domain, application, infrastructure, and API
dependency direction. It owns durable message rows and obtains authorization through a narrow
channel-content gateway rather than querying channel or workspace tables directly.

A message belongs to one workspace and channel and records its author, body, creation time, and
optional edit and deletion times. Live bodies contain 1 through 4,000 characters and cannot be
whitespace-only. Leading, trailing, and multiline formatting is otherwise preserved.

Explicit channel membership is required for all message operations. Private-channel existence
continues to be hidden from ordinary nonmembers. A user who may see channel metadata but is not a
channel member receives a permission failure when accessing content. Authorization is resolved
from PostgreSQL on every request rather than from access-token claims.

Archived channels retain readable history for their current members. They reject new messages and
edits, while authors may still delete their own content. This first slice has no moderator delete:
only the author may edit or delete a message, regardless of workspace role.

Deletion is soft and erases the body while retaining a tombstone and deletion timestamp. This
preserves ordering and stable references for future threads or realtime events. Messages remain
after an author leaves a channel or workspace; therefore author membership is checked at creation
time but is not a cascading message foreign key. Removing a workspace cascades all its channels
and messages.

History uses reverse-chronological keyset pagination ordered by `(created_at, id)`. The API accepts
the oldest message ID from the previous page as `before`, validates that it belongs to the
addressed channel, and returns `next_before` only when another page exists. Page size defaults to
50 and is bounded at 100.

Expose these routes below `/api/v1`:

- `POST /workspaces/{workspace_id}/channels/{channel_id}/messages`
- `GET /workspaces/{workspace_id}/channels/{channel_id}/messages`
- `PATCH /workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}`
- `DELETE /workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}`

Unsafe routes use the existing exact-Origin and session-bound CSRF requirements. Message and
channel mutations lock in the established order `workspace -> actor workspace membership ->
channel -> actor channel membership -> message`, omitting the final lock when creating a message.

## Consequences

- Message writes are acknowledged only after PostgreSQL commits them.
- Clients can load history and recover missed changes without an active realtime connection.
- Leaving or removal immediately revokes history access without destroying authored content.
- Deletion removes message text while preserving a durable position in conversation history.
- Future WebSocket delivery can publish committed create, edit, and delete events without changing
  the message model or making a socket the source of truth.
- Author responses expose only `author_user_id` until a user-profile/display-name capability exists.

## Deferred decisions

- WebSocket delivery, a committed event log or outbox, and cross-process fanout.
- Client-generated idempotency keys and offline-send reconciliation.
- Threads, reactions, attachments, mentions, rich text, link previews, and search.
- Moderator deletion, audit views, retention policies, legal holds, and content export.
- Unread state, read markers, notifications, typing indicators, and presence.
