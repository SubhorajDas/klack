-- Read-only audit. Supply psql -v run_id=...; no credentials are read or printed.
WITH workspaces AS (
  SELECT * FROM workspace_workspaces WHERE name LIKE 'K6 ' || :'run_id' || ' - %'
), main_user AS (
  SELECT created_by_user_id AS id FROM workspaces WHERE name LIKE '% - shared hub'
), channels AS (
  SELECT c.* FROM channel_channels c JOIN workspaces w ON c.workspace_id=w.id
), messages AS (
  SELECT m.* FROM message_messages m JOIN workspaces w ON m.workspace_id=w.id
), invitations AS (
  SELECT i.* FROM workspace_invitations i JOIN workspaces w ON i.workspace_id=w.id
)
SELECT json_build_object(
  'workspaces', (SELECT count(*) FROM workspaces),
  'public_channels', (SELECT count(*) FROM channels WHERE direct_key IS NULL),
  'dm_conversations', (SELECT count(*) FROM channels WHERE direct_key IS NOT NULL),
  'messages', (SELECT count(*) FROM messages),
  'thread_replies', (SELECT count(*) FROM messages WHERE parent_message_id IS NOT NULL),
  'dm_messages', (SELECT count(*) FROM messages m JOIN channels c ON m.channel_id=c.id WHERE c.direct_key IS NOT NULL),
  'message_authors', (SELECT count(DISTINCT author_user_id) FROM messages),
  'reactions', (SELECT count(*) FROM message_reactions r JOIN messages m ON r.message_id=m.id),
  'invitations_created', (SELECT count(*) FROM invitations),
  'invitations_accepted', (SELECT count(*) FROM invitations WHERE accepted_at IS NOT NULL),
  'invitations_pending', (SELECT count(*) FROM invitations WHERE accepted_at IS NULL AND revoked_at IS NULL),
  'main_workspaces_visible', (SELECT count(*) FROM workspace_memberships m JOIN workspaces w ON m.workspace_id=w.id WHERE m.user_id=(SELECT id FROM main_user)),
  'main_messages_sent', (SELECT count(*) FROM messages WHERE author_user_id=(SELECT id FROM main_user)),
  'main_dm_conversations', (SELECT count(*) FROM channels c JOIN channel_memberships m ON c.id=m.channel_id WHERE c.direct_key IS NOT NULL AND m.user_id=(SELECT id FROM main_user)),
  'main_invitations_accepted', (SELECT count(*) FROM invitations WHERE accepted_by_user_id=(SELECT id FROM main_user))
);
