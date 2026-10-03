# Architecture decision records

Use this directory for decisions that are expensive to reverse. Each record should capture
the context, decision, consequences, and rejected alternatives.

For a view of the implemented system across decisions, start with the
[architecture overview](../architecture/README.md). Setup and operational procedures belong in
the [development](../development.md) and [operations](../operations.md) guides.

- [0001: Adopt a modular monolith](0001-modular-monolith.md)
- [0002: Establish identity and secure browser sessions](0002-identity-and-browser-sessions.md)
- [0003: Complete identity security follow-ups](0003-identity-security-followups.md)
- [0004: Establish workspaces, memberships, and invitation links](0004-workspaces-memberships-and-invitation-links.md)
- [0005: Establish channels and channel memberships](0005-channels-and-channel-memberships.md)
- [0006: Add durable channel messages before realtime delivery](0006-durable-channel-messages.md)
- [0007: Deliver committed message changes over authenticated WebSockets](0007-committed-realtime-message-delivery.md)
- [0008: Threads, reactions, read positions, and direct conversations](0008-conversation-interactions.md)
- [0009: Private voice calls with LiveKit](0009-private-voice-calls.md)

- [0010: Inline quoted replies](0010-inline-quoted-replies.md)

- [0011: Personal DMs and account-wide unread counts](0011-personal-direct-messages.md)
