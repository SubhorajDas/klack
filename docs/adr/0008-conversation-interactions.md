# 0008: Threads, reactions, read positions, and direct conversations

Status: Accepted; thread behavior superseded by [0010](0010-inline-quoted-replies.md).

Threads use a nullable, same-channel foreign key to a root message. Application validation
rejects replies to replies. Root history excludes replies; thread history uses the existing
stable timestamp/ID pagination. Deleted roots remain as tombstones and preserve their replies.
Each new reply increments the root revision and emits a committed message snapshot signal.
Reaction changes use the same revision and event transaction, with a unique message/user/emoji
key for retry safety. History loads reaction and reply summaries in batches.

Read positions belong to channel memberships and reference a message in that channel. The
existing workspace/channel locks serialize updates; comparisons of timestamp and ID prevent
backward moves. Unread counts exclude deleted messages and the reader's own messages. A cursor
covers the complete chronological conversation, including replies. Read positions are private;
they are not broadcast as read receipts. The frontend refreshes unread counts periodically.

A direct conversation is a private channel with a unique workspace/canonical-user-pair key.
This preserves the existing explicit-membership content authorization and realtime transport.
The normal channel discovery and management interfaces cannot expose or mutate direct
conversations, even for administrators. Participant creation is serialized by the workspace
lock, which also coordinates workspace membership removal. Membership removal revokes access
and removes the user's read cursor. Explicitly opening the pair after rejoining restores the
same conversation and its history. Group DMs and self-DMs are outside this slice.

Migration 20260925_0007 is additive. Existing channel messages become root messages, existing
channels have no direct pair key, and users without a read cursor have all other authors' live
messages counted as unread. Deploy the migration before deploying the new API/frontend.

Workspace-scoped DM identity and workspace-removal revocation are superseded by [ADR 0011](0011-personal-direct-messages.md).
