export type User = { id: string; email: string; email_verified: boolean; created_at: string };
export type Workspace = { id: string; name: string; created_at: string };
export type Membership = {
  user_id: string;
  workspace_id: string;
  role: 'owner' | 'admin' | 'member';
  joined_at: string;
  display_name?: string | null;
  email?: string | null;
};
export type Channel = {
  id: string;
  workspace_id: string;
  name: string;
  visibility: 'public' | 'private';
  direct_key?: string | null;
  is_member: boolean;
  archived_at: string | null;
};
export type Attachment = { id: string; filename: string; size: number; content_type: string };
export type MessageQuote = {
  id: string;
  author_user_id: string;
  body: string | null;
  deleted_at: string | null;
  revision: number;
  attachment_count: number;
};
export type Message = {
  id: string;
  workspace_id: string;
  channel_id: string;
  author_user_id: string;
  body: string | null;
  created_at: string;
  edited_at: string | null;
  deleted_at: string | null;
  client_message_id: string | null;
  revision: number;
  reply_to_message_id?: string | null;
  reactions?: [string, string][];
  quote?: MessageQuote | null;
  attachments?: Attachment[];
  document?: import('./rich-text').RichDocument | null;
};
export type MessagePage = { messages: Message[]; next_before: string | null };
export type Session = {
  id: string;
  current: boolean;
  user_agent: string | null;
  last_seen_at: string;
  expires_at: string;
};
export type Invitation = {
  id: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
};

export const workspacePath = (id: string) => `/workspaces/${id}`;
export const channelPath = (workspace: string, channel: string) =>
  `${workspacePath(workspace)}/channels/${channel}`;
export const memberName = (id: string, user: User, members: Membership[] = []) =>
  id === user.id
    ? user.email.split('@')[0]
    : members.find((member) => member.user_id.replaceAll('-', '') === id.replaceAll('-', ''))
        ?.display_name || `Member ${id.slice(0, 8)}`;
