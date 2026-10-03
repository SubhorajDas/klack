# 0010: Inline quoted replies

Status: Accepted

Separate thread panels made conversations awkward to follow. Quoted replies now appear
in the same chronological history as all other messages in channels and private DMs.
Any message can be quoted, including another reply; the quote remains shallow.

Migration `20261003_0010` renames the existing parent reference, foreign key, and index to
`reply_to_message_id`. Existing thread replies retain their IDs, timestamps, reactions,
attachments, and read positions. Several replies to one root each quote that same root.
No message is copied or recreated. History and live-delivery filters no longer hide replies.

Quote previews are resolved from the current original in batches and bounded to 240 text
characters. Deletion erases quoted text and attachment previews. The frontend reconciles
original revisions across both message events and quoted previews so stale snapshots cannot
restore deleted text. Sending a reply does not mutate the original or its revision.

The composer saves the quote reference with its existing tab-local draft. Changing or
cancelling the quote preserves typed text and attachments. An uncertain send locks the
quote with the rest of its payload until the original retry ID has resolved delivery.

Clicking a quote scrolls to the original and highlights it. An original outside loaded
history is retrieved through an authorized, bounded `around` window, with older pagination
and a return-to-latest action. Browsing an earlier window does not acknowledge newer messages.

Deploy the migration, API, and frontend together. The request/response contract replaces
`parent_message_id` and thread counts with `reply_to_message_id` and a shallow `quote`.
