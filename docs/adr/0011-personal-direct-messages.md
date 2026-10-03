# 0011: Personal DMs and account-wide unread counts

Date: 2026-10-04
Status: Accepted

## Context

A workspace-specific private history splits a relationship into multiple conversations and hides
unread messages until the user selects the originating workspace. Alerts should cover the account.

## Decision

A canonical participant pair identifies one personal DM globally. The browser uses `/dms` and
`/dms/:conversation` independently of workspace selection. New conversations require a shared
workspace; existing conversations remain accessible after workspace departures. Only the two
participants can access messages, files, reactions, read positions, and calls. Ordered identity
locks and a global unique pair constraint serialize creation from different workspaces.

Channel-backed storage and its original workspace locator remain for compatibility with existing
message, file, call, and realtime endpoints. The locator is not a DM authorization scope. There is
currently no workspace-deletion endpoint; a future deletion feature must relocate personal storage
before deleting a workspace. Channel memberships reference identities; PostgreSQL triggers retain
workspace-membership validation and departure cleanup for ordinary channels. Repository operations
also validate and revoke ordinary channel memberships.

The account Alerts inbox aggregates accessible unread channel and DM conversations. A lightweight
count endpoint uses the same unread predicate without loading message bodies. The bell counts all
unread messages, the DM rail icon counts personal unread messages, workspace icons count their channel
messages, and each channel/conversation row shows its own count. Own and deleted messages do not count.
The shell shares one count feed, refreshes every five seconds while visible and on focus/read/message
events, and propagates local events across browser tabs.

## Migration and consequences

Revision `20261004_0011` retains the oldest conversation for each pair and moves duplicate histories,
files, calls, and realtime records into it. Message IDs, bodies, authors, timestamps, quotes, reactions,
and file storage keys remain intact. Alias records resolve old conversation links. If two old histories
reused a retry identifier for the same author, only the earliest message retains that identifier;
all message records remain. Merged read positions use a conservative prefix before the first unread
incoming message, which can resurface previously read later messages rather than silently clearing
unread ones. Histories cannot be split reliably after new messages arrive, so downgrade requires
restoring a pre-upgrade backup. Apply the migration before serving the new backend.
