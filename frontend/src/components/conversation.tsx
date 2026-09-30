'use client';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  ArrowLeft,
  Hash,
  Lock,
  Users,
  Phone,
  Info,
  Send,
  Smile,
  X,
  MoreHorizontal,
  Pencil,
  Trash2,
  WifiOff,
  Check,
  Archive,
  LogOut,
  MessageSquare,
  FileText,
  Pin,
} from 'lucide-react';
import { useMemberName } from '@/lib/member-names';
import { api, ApiError, errorMessage } from '@/lib/api';
import { channelPath, type Channel, type Message, type User, type Membership } from '@/lib/types';
import { useConversation } from '@/lib/use-conversation';
import { Alert, Avatar, Empty, Loading, Modal } from './ui';
import { CallHistory, useCalls } from './calls';
import { FilePicker, FilesPanel, MessageAttachments, type QueuedFile } from './files';

export function Conversation({
  user,
  channel,
  manager,
  filter,
  update,
  browse,
}: {
  user: User;
  channel: Channel;
  manager: boolean;
  filter: string;
  update: (value: Channel) => void;
  browse: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function join() {
    setBusy(true);
    setError('');
    try {
      await api(`${channelPath(channel.workspace_id, channel.id)}/memberships/me`, 'PUT');
      update({ ...channel, is_member: true });
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  if (!channel.is_member)
    return (
      <>
        <div className="channel-header">
          <h1>
            <Hash />
            {channel.name}
          </h1>
        </div>
        <Alert>{error}</Alert>
        <Empty
          title={
            channel.visibility === 'public'
              ? `Join #${channel.name}`
              : 'This conversation is private'
          }
          action={
            channel.visibility === 'public' && !channel.archived_at ? (
              <button className="primary" disabled={busy} onClick={join}>
                {busy ? 'Joining…' : 'Join channel'}
              </button>
            ) : (
              <button onClick={browse}>Browse channels</button>
            )
          }
        >
          {channel.archived_at
            ? 'This channel is archived. Existing members can still read its history.'
            : channel.visibility === 'public'
              ? 'Join this channel to read messages and be part of the conversation.'
              : 'A workspace owner or admin can add you to this channel.'}
        </Empty>
      </>
    );
  return (
    <JoinedConversation
      user={user}
      channel={channel}
      manager={manager}
      filter={filter}
      update={update}
      browse={browse}
    />
  );
}

function JoinedConversation({
  user,
  channel,
  manager,
  filter,
  update,
  browse,
}: {
  user: User;
  channel: Channel;
  manager: boolean;
  filter: string;
  update: (value: Channel) => void;
  browse: () => void;
}) {
  const memberName = useMemberName();
  const chat = useConversation(channel.workspace_id, channel.id);
  const calls = useCalls();
  const [tab, setTab] = useState('messages');
  const [thread, setThread] = useState<Message | null>(null);
  const [details, setDetails] = useState<Message | null>(null);
  const [settings, setSettings] = useState(false);
  const [editing, setEditing] = useState<Message | null>(null);
  const [deleting, setDeleting] = useState<Message | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const lastId = useRef('');
  const visible = chat.messages.filter(
    (message) =>
      !filter ||
      message.body?.toLowerCase().includes(filter.toLowerCase()) ||
      message.attachments?.some((file) =>
        file.filename.toLowerCase().includes(filter.toLowerCase()),
      ),
  );
  const selected = details ? chat.messages.find((message) => message.id === details.id) : null;
  useEffect(() => {
    const newest = chat.messages.at(-1)?.id;
    if (newest !== lastId.current && atBottom.current)
      list.current?.scrollTo({ top: list.current.scrollHeight });
    lastId.current = newest || '';
  }, [chat.messages]);
  async function markRead() {
    const newest = chat.messages.at(-1);
    if (
      !newest ||
      document.visibilityState !== 'visible' ||
      !atBottom.current ||
      tab !== 'messages' ||
      thread ||
      filter
    )
      return;
    try {
      await api(`${channelPath(channel.workspace_id, channel.id)}/read-cursor`, 'PUT', {
        message_id: newest.id,
      });
      window.dispatchEvent(new Event('klack:read'));
    } catch {
      /* Retry when the conversation is visible again. */
    }
  }
  useEffect(() => {
    const timer = setTimeout(() => void markRead(), 700);
    const visible = () => void markRead();
    document.addEventListener('visibilitychange', visible);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', visible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.messages, tab, filter, thread]);
  async function remove() {
    if (!deleting) return;
    setBusy(true);
    setError('');
    try {
      await api(`${chat.path}/${deleting.id}`, 'DELETE');
      chat.merge([
        {
          ...deleting,
          body: null,
          deleted_at: new Date().toISOString(),
          revision: deleting.revision + 1,
        },
      ]);
      setDeleting(null);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="conversation-layout">
      <section className="conversation">
        <header className="channel-header">
          <div className="channel-title">
            <button
              className={`icon-button channel-back ${channel.direct_key ? 'direct-back' : ''}`}
              onClick={browse}
              aria-label={channel.direct_key ? 'Back to direct messages' : 'Back to channels'}
            >
              <ArrowLeft size={20} />
            </button>
            <div>
              <h1>
                {channel.visibility === 'private' ? <Lock size={24} /> : <Hash size={26} />}{' '}
                {channel.name}
              </h1>
              <p>
                {channel.archived_at
                  ? 'Archived · Your conversations are still here.'
                  : channel.direct_key
                    ? 'A private conversation, just between the two of you.'
                    : 'A place for ideas, feedback, and the details.'}
              </p>
            </div>
          </div>
          <div className="channel-header-actions">
            <button
              className="icon-button"
              aria-label="Channel details and members"
              disabled={!!channel.direct_key}
              onClick={() => setSettings(true)}
            >
              <Users size={20} />
            </button>
            {channel.direct_key && (
              <button
                className="call-button"
                disabled={!calls.enabled || !!calls.call || calls.busy}
                title={calls.enabled ? 'Start a voice call' : 'Voice calls are unavailable'}
                onClick={() => void calls.start(channel)}
              >
                <Phone size={18} />
                Start call
              </button>
            )}
            <button
              className="icon-button"
              aria-label="Channel settings"
              disabled={!!channel.direct_key}
              onClick={() => setSettings(true)}
            >
              <MoreHorizontal size={22} />
            </button>
          </div>
        </header>
        <div className="conversation-tabs">
          <div className="tabs">
            {[
              { id: 'messages', label: 'Messages', Icon: MessageSquare },
              ...(channel.direct_key ? [{ id: 'calls', label: 'Calls', Icon: Phone }] : []),
              { id: 'files', label: 'Files', Icon: FileText },
              { id: 'pinned', label: 'Pinned', Icon: Pin },
            ].map(({ id, label, Icon }) => (
              <button key={id} className={tab === id ? 'selected' : ''} onClick={() => setTab(id)}>
                <Icon size={15} />
                {label}
                {id === 'pinned' && <span className="soon-dot" />}
              </button>
            ))}
          </div>
          <span
            className={`connection-state ${chat.status === 'Connected' ? 'connected' : ''}`}
            role="status"
          >
            <span />
            {chat.status}
          </span>
        </div>
        {tab === 'calls' ? (
          <CallHistory key={channel.id} channel={channel} user={user} />
        ) : tab === 'files' ? (
          chat.status === 'Access unavailable' ? (
            <Empty title="Access unavailable">You no longer have access to these files.</Empty>
          ) : (
            <FilesPanel key={channel.id} channel={channel} />
          )
        ) : tab !== 'messages' ? (
          <Empty title="Keep important messages in view">Pinned messages are coming soon.</Empty>
        ) : (
          <>
            {chat.status === 'Reconnecting' && (
              <div className="reconnect-banner">
                <WifiOff size={17} />
                <span>
                  Connection lost. Reconnecting…{' '}
                  <small>Your draft is saved in this browser tab.</small>
                </span>
              </div>
            )}
            {chat.error && (
              <div className="page-alert">
                <Alert>{chat.error}</Alert>
              </div>
            )}
            <div
              className="message-list"
              ref={list}
              role="log"
              aria-label={`Messages in ${channel.name}`}
              onScroll={() => {
                const element = list.current!;
                atBottom.current =
                  element.scrollHeight - element.scrollTop - element.clientHeight < 100;
                if (atBottom.current) void markRead();
              }}
            >
              {chat.next && (
                <button
                  className="load-older"
                  disabled={chat.paging}
                  onClick={async () => {
                    const element = list.current;
                    const previous = element?.scrollHeight || 0;
                    await chat.older();
                    requestAnimationFrame(() => {
                      if (element) element.scrollTop += element.scrollHeight - previous;
                    });
                  }}
                >
                  {chat.paging ? 'Loading…' : 'Load older messages'}
                </button>
              )}
              {chat.loading ? (
                <Loading label="Loading conversation…" />
              ) : !visible.length ? (
                <Empty title={filter ? 'No messages found' : 'Start the conversation'}>
                  {filter
                    ? 'Search covers the messages loaded in this conversation. Try another phrase or load older messages.'
                    : `This is the beginning of #${channel.name}. Share an idea, ask a question, or just say hello.`}
                </Empty>
              ) : (
                visible.map((message, index) => {
                  const date = new Date(message.created_at).toLocaleDateString(undefined, {
                    weekday: 'long',
                    month: 'long',
                    day: 'numeric',
                  });
                  const previousDate = index
                    ? new Date(visible[index - 1].created_at).toDateString()
                    : '';
                  const name = memberName(message.author_user_id, user);
                  return (
                    <div key={message.id}>
                      {new Date(message.created_at).toDateString() !== previousDate && (
                        <div className="day-divider">
                          <span>{date}</span>
                        </div>
                      )}
                      <article className={`message ${message.deleted_at ? 'deleted' : ''}`}>
                        <Avatar name={name} />
                        <div className="message-content">
                          <header>
                            <strong>{name}</strong>
                            {message.author_user_id === user.id && (
                              <span className="you-label">you</span>
                            )}
                            <time dateTime={message.created_at}>
                              {new Date(message.created_at).toLocaleTimeString([], {
                                hour: 'numeric',
                                minute: '2-digit',
                              })}
                            </time>
                            {message.edited_at && !message.deleted_at && <small>edited</small>}
                          </header>
                          <p>{message.deleted_at ? 'This message was deleted.' : message.body}</p>
                          <MessageAttachments message={message} />
                          <Reactions
                            message={message}
                            user={user}
                            path={chat.path}
                            disabled={!!channel.archived_at}
                            changed={(m) => chat.merge([m])}
                          />
                          <button
                            className="text-button"
                            onClick={() => {
                              setDetails(null);
                              setThread(message);
                            }}
                          >
                            <MessageSquare size={14} />{' '}
                            {message.reply_count
                              ? `${message.reply_count} ${message.reply_count === 1 ? 'reply' : 'replies'}`
                              : 'Reply in thread'}
                          </button>
                        </div>
                        <div className="message-actions">
                          <button
                            className="icon-button"
                            aria-label="Message details"
                            onClick={() => setDetails(message)}
                          >
                            <Info size={16} />
                          </button>
                          {message.author_user_id === user.id && !message.deleted_at && (
                            <>
                              {!channel.archived_at && (
                                <button
                                  className="icon-button"
                                  aria-label="Edit message"
                                  onClick={() => {
                                    setError('');
                                    setEditing(message);
                                  }}
                                >
                                  <Pencil size={16} />
                                </button>
                              )}
                              <button
                                className="icon-button"
                                aria-label="Delete message"
                                onClick={() => {
                                  setError('');
                                  setDeleting(message);
                                }}
                              >
                                <Trash2 size={16} />
                              </button>
                            </>
                          )}
                        </div>
                      </article>
                    </div>
                  );
                })
              )}
            </div>
            {channel.archived_at ? (
              <div className="archived-banner">
                <Archive size={18} />
                This channel is archived. Message history is available to read.
              </div>
            ) : chat.status === 'Access unavailable' ? (
              <div className="archived-banner">
                Your access has changed. <button onClick={browse}>Browse channels</button>
              </div>
            ) : (
              <Composer
                user={user}
                channel={channel}
                sent={(message) => {
                  atBottom.current = true;
                  chat.merge([message]);
                }}
                path={chat.path}
              />
            )}
          </>
        )}
      </section>
      {thread && chat.status !== 'Access unavailable' && (
        <ThreadPanel
          key={thread.id}
          root={chat.messages.find((m) => m.id === thread.id) || thread}
          channel={channel}
          user={user}
          close={() => setThread(null)}
        />
      )}
      {selected && (
        <aside className="detail-panel">
          <header>
            <h2>Message details</h2>
            <button
              className="icon-button"
              aria-label="Close message details"
              onClick={() => setDetails(null)}
            >
              <X size={21} />
            </button>
          </header>
          <div className="detail-content">
            <Avatar name={memberName(selected.author_user_id, user)} />
            <h3>{memberName(selected.author_user_id, user)}</h3>
            <time>{new Date(selected.created_at).toLocaleString()}</time>
            <p className="message-body">
              {selected.deleted_at ? 'This message was deleted.' : selected.body}
            </p>
            <MessageAttachments message={selected} />
            <div className="detail-note">
              <MessageSquare size={22} />
              <strong>Keep the conversation going</strong>
              <button
                onClick={() => {
                  setThread(selected);
                  setDetails(null);
                }}
              >
                Open thread
              </button>
            </div>
          </div>
        </aside>
      )}
      {settings && !channel.direct_key && (
        <Modal title={`About #${channel.name}`} close={() => setSettings(false)}>
          <ChannelSettings
            user={user}
            channel={channel}
            manager={manager}
            update={update}
            done={() => setSettings(false)}
          />
        </Modal>
      )}
      {editing && (
        <Modal title="Edit message" close={() => setEditing(null)}>
          <form
            className="form-stack"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setError('');
              try {
                const message = await api<Message>(`${chat.path}/${editing.id}`, 'PATCH', {
                  body: new FormData(event.currentTarget).get('body'),
                });
                chat.merge([message]);
                setEditing(null);
              } catch (failure) {
                setError(errorMessage(failure));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Alert>{error}</Alert>
            <label>
              Message
              <textarea
                name="body"
                defaultValue={editing.body || ''}
                maxLength={4000}
                required={!editing.attachments?.length}
                autoFocus
                rows={5}
              />
            </label>
            <button className="primary" disabled={busy}>
              {busy ? 'Saving…' : 'Save changes'}
            </button>
          </form>
        </Modal>
      )}
      {deleting && (
        <Modal title="Delete this message?" close={() => setDeleting(null)}>
          <div className="form-stack">
            <Alert>{error}</Alert>
            <p>
              This will remove the message’s content for everyone in the channel. This can’t be
              undone.
            </p>
            <blockquote>{deleting.body}</blockquote>
            <div className="button-row">
              <button onClick={() => setDeleting(null)}>Cancel</button>
              <button className="danger" disabled={busy} onClick={remove}>
                {busy ? 'Deleting…' : 'Delete message'}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

type Draft = { body: string; id: string; attempted: boolean; files?: QueuedFile[] };
function Composer({
  user,
  channel,
  sent,
  path,
  parent,
}: {
  user: User;
  channel: Channel;
  sent: (message: Message) => void;
  path: string;
  parent?: string;
}) {
  const storageKey = `klack:draft:${user.id}:${channel.workspace_id}:${channel.id}:${parent || 'main'}`;
  const [draft, setDraft] = useState<Draft>(() => {
    try {
      const stored = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
      if (stored && typeof stored.body === 'string' && typeof stored.id === 'string')
        return {
          ...stored,
          files: (stored.files || []).map((file: QueuedFile) =>
            file.attachment
              ? file
              : {
                  ...file,
                  progress: undefined,
                  error: 'Select this file again to finish uploading.',
                },
          ),
        };
    } catch {}
    return { body: '', id: crypto.randomUUID(), attempted: false };
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [emoji, setEmoji] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const sending = useRef(false);
  const acceptFiles = useRef<(files: File[]) => void>(() => {});
  const files = draft.files || [];
  const filesReady = files.every((file) => !!file.attachment);
  const canSend = filesReady && (!!draft.body.trim() || files.length > 0);
  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(draft));
    } catch {
      /* Drafting still works when browser storage is unavailable. */
    }
  }, [draft, storageKey]);
  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!canSend || sending.current) return;
    sending.current = true;
    setBusy(true);
    setError('');
    const pending = { ...draft, attempted: true };
    setDraft(pending);
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(pending));
    } catch {}
    try {
      const message = await api<Message>(path, 'POST', {
        body: draft.body,
        client_message_id: draft.id,
        ...(files.length ? { attachment_ids: files.map((file) => file.attachment!.id) } : {}),
        ...(parent ? { parent_message_id: parent } : {}),
      });
      sent(message);
      const empty = { body: '', id: crypto.randomUUID(), attempted: false };
      setDraft(empty);
      try {
        sessionStorage.removeItem(storageKey);
      } catch {}
      input.current?.focus();
    } catch (failure) {
      setError(errorMessage(failure));
      // A definite rejection did not commit a message; allow the draft to be corrected.
      if (
        failure instanceof ApiError &&
        failure.status >= 400 &&
        failure.status < 500 &&
        (failure.status !== 409 || failure.code === 'file_error')
      )
        setDraft({ ...draft, attempted: false });
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }
  return (
    <div
      className="composer-wrap"
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes('Files')) event.preventDefault();
      }}
      onDrop={(event) => {
        event.preventDefault();
        acceptFiles.current(Array.from(event.dataTransfer.files));
      }}
      onPaste={(event) => {
        const pasted = Array.from(event.clipboardData.files);
        if (pasted.length) {
          event.preventDefault();
          acceptFiles.current(pasted);
        }
      }}
    >
      {(error || (draft.attempted && !busy)) && (
        <div className="send-error" role="alert">
          <span>
            {error ||
              'This draft has an unconfirmed send. Retry to check delivery without creating a duplicate.'}
          </span>
          <button onClick={() => void submit()} disabled={busy}>
            Retry send
          </button>
        </div>
      )}
      <form className={`composer ${error ? 'has-error' : ''}`} onSubmit={submit}>
        <div className="composer-toolbar">
          <MessageSquare size={16} />
          <strong>Message</strong>
          <span>Make a little progress, together.</span>
        </div>
        <textarea
          ref={input}
          aria-label={`Message ${channel.name}`}
          placeholder={
            parent ? 'Reply in thread' : `Message ${channel.direct_key ? '' : '#'}${channel.name}`
          }
          maxLength={4000}
          value={draft.body}
          readOnly={draft.attempted}
          onChange={(event) => setDraft({ ...draft, body: event.target.value })}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <FilePicker
          path={`${channelPath(channel.workspace_id, channel.id)}/files`}
          entries={files}
          locked={draft.attempted || busy}
          registerInput={(accept) => {
            acceptFiles.current = accept;
          }}
          change={(change) =>
            setDraft((current) => ({
              ...current,
              files: typeof change === 'function' ? change(current.files || []) : change,
            }))
          }
        />
        <div className="composer-bottom">
          <div className="emoji-control">
            <button
              type="button"
              className="icon-button"
              disabled={draft.attempted}
              aria-label="Add emoji"
              aria-expanded={emoji}
              onClick={() => setEmoji(!emoji)}
            >
              <Smile size={21} />
            </button>
            {emoji && (
              <div className="emoji-picker">
                {['👋', '👍', '💜', '🎉', '✨', '🚀', '✅', '🙏'].map((value) => (
                  <button
                    type="button"
                    key={value}
                    onClick={() => {
                      setDraft({ ...draft, body: `${draft.body}${value}`.slice(0, 4000) });
                      setEmoji(false);
                      input.current?.focus();
                    }}
                  >
                    {value}
                  </button>
                ))}
              </div>
            )}
            <span className="composer-hint">Shift + Enter for a new line</span>
          </div>
          <div className="send-controls">
            {draft.body.length > 3600 && <small>{draft.body.length}/4000</small>}
            <button
              className="primary send-button"
              aria-label={draft.attempted ? 'Retry send' : 'Send message'}
              disabled={busy || !canSend}
            >
              <Send size={18} />
              <span>{busy ? 'Sending' : draft.attempted ? 'Retry' : 'Send'}</span>
            </button>
          </div>
        </div>
      </form>
      <div className="composer-footnote">
        <Lock size={11} />
        Only members of this channel can read and send messages.
      </div>
    </div>
  );
}

function ChannelSettings({
  user,
  channel,
  manager,
  update,
  done,
}: {
  user: User;
  channel: Channel;
  manager: boolean;
  update: (value: Channel) => void;
  done: () => void;
}) {
  const memberName = useMemberName();
  const [members, setMembers] = useState<{ user_id: string }[]>([]);
  const [roster, setRoster] = useState<Membership[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<'archive' | 'leave' | null>(null);
  const path = channelPath(channel.workspace_id, channel.id);
  useEffect(() => {
    let active = true;
    Promise.all([
      api<{ memberships: { user_id: string }[] }>(`${path}/memberships`),
      manager
        ? api<{ memberships: Membership[] }>(`/workspaces/${channel.workspace_id}/memberships`)
        : Promise.resolve({ memberships: [] }),
    ])
      .then(([data, workspace]) => {
        if (active) {
          setMembers(data.memberships);
          setRoster(workspace.memberships);
        }
      })
      .catch((failure) => {
        if (active) setError(errorMessage(failure));
      });
    return () => {
      active = false;
    };
  }, [path, manager, channel.workspace_id]);
  async function action(operation: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await operation();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="form-stack">
      <Alert>{error}</Alert>
      <p className="muted">
        {channel.visibility === 'public'
          ? 'Public channel · Anyone in the workspace can join.'
          : 'Private channel · Only added members can participate.'}
      </p>
      {manager && (
        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault();
            const name = new FormData(event.currentTarget).get('name');
            void action(async () => {
              update(await api<Channel>(path, 'PATCH', { name }));
              done();
            });
          }}
        >
          <label>
            Channel name
            <input name="name" defaultValue={channel.name} required maxLength={80} />
          </label>
          <button disabled={busy}>Save name</button>
        </form>
      )}
      <h3>
        Members <span className="count">{members.length}</span>
      </h3>
      <div className="member-mini-list">
        {members.map((member) => (
          <div key={member.user_id}>
            <Avatar name={memberName(member.user_id, user)} small />
            <span>{memberName(member.user_id, user)}</span>
            {manager && member.user_id !== user.id && (
              <button
                className="icon-button"
                aria-label={`Remove ${memberName(member.user_id, user)} from channel`}
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await api(`${path}/memberships/${member.user_id}`, 'DELETE');
                    setMembers(members.filter((item) => item.user_id !== member.user_id));
                  })
                }
              >
                <X size={16} />
              </button>
            )}
          </div>
        ))}
      </div>
      {manager &&
        roster.some((member) => !members.some((item) => item.user_id === member.user_id)) && (
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              const id = String(new FormData(event.currentTarget).get('user'));
              void action(async () => {
                await api(`${path}/memberships/${id}`, 'PUT');
                setMembers([...members, { user_id: id }]);
              });
            }}
          >
            <select name="user" aria-label="Workspace member to add">
              {roster
                .filter((member) => !members.some((item) => item.user_id === member.user_id))
                .map((member) => (
                  <option key={member.user_id} value={member.user_id}>
                    {memberName(member.user_id, user)}
                  </option>
                ))}
            </select>
            <button disabled={busy}>Add member</button>
          </form>
        )}
      {confirm ? (
        <div className="confirm-box">
          <p>
            {confirm === 'leave'
              ? 'Leave this channel? You’ll lose access to its messages until you rejoin or are added again.'
              : 'Archive this channel? Members will still be able to read messages, but cannot send new ones.'}
          </p>
          <div className="button-row">
            <button onClick={() => setConfirm(null)}>Cancel</button>
            <button
              className="danger"
              disabled={busy}
              onClick={() =>
                void action(async () => {
                  if (confirm === 'leave') {
                    await api(`${path}/memberships/me`, 'DELETE');
                    update({ ...channel, is_member: false });
                  } else update(await api<Channel>(`${path}/archive`, 'POST'));
                  done();
                })
              }
            >
              Confirm
            </button>
          </div>
        </div>
      ) : (
        <div className="button-row">
          {manager && (
            <button
              disabled={busy}
              onClick={() =>
                channel.archived_at
                  ? void action(async () => {
                      update(await api<Channel>(`${path}/unarchive`, 'POST'));
                      done();
                    })
                  : setConfirm('archive')
              }
            >
              <Archive size={16} />
              {channel.archived_at ? 'Restore channel' : 'Archive channel'}
            </button>
          )}
          <button disabled={busy} onClick={() => setConfirm('leave')}>
            <LogOut size={16} />
            Leave channel
          </button>
        </div>
      )}
    </div>
  );
}

function Reactions({
  message,
  user,
  path,
  disabled,
  changed,
}: {
  message: Message;
  user: User;
  path: string;
  disabled: boolean;
  changed: (m: Message) => void;
}) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (message.deleted_at) return null;
  return (
    <div className="reactions" aria-label="Message reactions">
      {['👍', '❤️', '😂', '🎉', '👀', '✅'].map((emoji) => {
        const rows = (message.reactions || []).filter((r) => r[0] === emoji);
        const mine = rows.some((r) => r[1] === user.id);
        return (
          <button
            key={emoji}
            aria-label={`${mine ? 'Remove' : 'Add'} ${emoji} reaction`}
            aria-pressed={mine}
            disabled={disabled || busy}
            onClick={async () => {
              setBusy(true);
              setError('');
              try {
                changed(
                  await api<Message>(
                    `${path}/${message.id}/reactions/${encodeURIComponent(emoji)}`,
                    mine ? 'DELETE' : 'PUT',
                  ),
                );
              } catch (failure) {
                setError(errorMessage(failure));
              } finally {
                setBusy(false);
              }
            }}
          >
            {emoji}
            {rows.length ? ` ${rows.length}` : ''}
          </button>
        );
      })}
      <Alert>{error}</Alert>
    </div>
  );
}

function ThreadPanel({
  root,
  channel,
  user,
  close,
}: {
  root: Message;
  channel: Channel;
  user: User;
  close: () => void;
}) {
  const memberName = useMemberName();
  const chat = useConversation(channel.workspace_id, channel.id, root.id);
  const replies = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  async function markRead() {
    const newest = chat.messages.at(-1);
    if (!newest || !atBottom.current || document.visibilityState !== 'visible') return;
    try {
      await api(`${channelPath(channel.workspace_id, channel.id)}/read-cursor`, 'PUT', {
        message_id: newest.id,
      });
      window.dispatchEvent(new Event('klack:read'));
    } catch {
      /* A visible refresh will retry. */
    }
  }
  useEffect(() => {
    if (atBottom.current) replies.current?.scrollTo({ top: replies.current.scrollHeight });
    const timer = setTimeout(() => void markRead(), 700);
    const visible = () => void markRead();
    document.addEventListener('visibilitychange', visible);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', visible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.messages]);
  const [editing, setEditing] = useState<Message | null>(null);
  const [deleting, setDeleting] = useState<Message | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <aside className="detail-panel thread-panel">
      <header>
        <h2>Thread</h2>
        <button className="icon-button" aria-label="Close thread" onClick={close}>
          <X size={21} />
        </button>
      </header>
      <div
        className="thread-messages"
        role="log"
        aria-label="Thread replies"
        ref={replies}
        onScroll={() => {
          const element = replies.current!;
          atBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 60;
          if (atBottom.current) void markRead();
        }}
      >
        {chat.status !== 'Access unavailable' && (
          <>
            <strong>{memberName(root.author_user_id, user)}</strong>
            <p className="message-body">
              {root.deleted_at ? 'This message was deleted.' : root.body}
            </p>
            <MessageAttachments message={root} />
          </>
        )}
        <hr />
        <Alert>{chat.error || error}</Alert>
        {chat.next && (
          <button
            disabled={chat.paging}
            onClick={async () => {
              const element = replies.current;
              const height = element?.scrollHeight || 0;
              const top = element?.scrollTop || 0;
              atBottom.current = false;
              await chat.older();
              requestAnimationFrame(() => {
                if (element) element.scrollTop = top + element.scrollHeight - height;
              });
            }}
          >
            Load older replies
          </button>
        )}
        {chat.loading ? (
          <Loading label="Loading replies…" />
        ) : (
          !chat.messages.length && <p className="muted">Be the first to reply.</p>
        )}
        {chat.messages.map((m) => (
          <article key={m.id} className="thread-reply">
            <strong>{memberName(m.author_user_id, user)}</strong>
            <small> · {new Date(m.created_at).toLocaleString()}</small>
            <p className="message-body">{m.deleted_at ? 'This message was deleted.' : m.body}</p>
            <MessageAttachments message={m} />
            <Reactions
              message={m}
              user={user}
              path={chat.path}
              disabled={!!channel.archived_at}
              changed={(m) => chat.merge([m])}
            />
            {m.author_user_id === user.id && !m.deleted_at && (
              <div className="button-row">
                {!channel.archived_at && <button onClick={() => setEditing(m)}>Edit reply</button>}
                <button onClick={() => setDeleting(m)}>Delete reply</button>
              </div>
            )}
          </article>
        ))}
      </div>
      {!channel.archived_at && chat.status !== 'Access unavailable' && (
        <Composer
          user={user}
          channel={channel}
          path={chat.path}
          parent={root.id}
          sent={(m) => chat.merge([m])}
        />
      )}
      {(editing || deleting) && (
        <Modal
          title={editing ? 'Edit reply' : 'Delete reply?'}
          close={() => {
            setEditing(null);
            setDeleting(null);
          }}
        >
          <form
            className="form-stack"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError('');
              try {
                if (editing)
                  chat.merge([
                    await api<Message>(`${chat.path}/${editing.id}`, 'PATCH', {
                      body: new FormData(e.currentTarget).get('body'),
                    }),
                  ]);
                else if (deleting) {
                  await api(`${chat.path}/${deleting.id}`, 'DELETE');
                  chat.merge([
                    {
                      ...deleting,
                      body: null,
                      deleted_at: new Date().toISOString(),
                      revision: deleting.revision + 1,
                    },
                  ]);
                }
                setEditing(null);
                setDeleting(null);
              } catch (failure) {
                setError(errorMessage(failure));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Alert>{error}</Alert>
            {editing ? (
              <textarea
                name="body"
                required={!editing.attachments?.length}
                maxLength={4000}
                defaultValue={editing.body || ''}
              />
            ) : (
              <p>This permanently removes the reply’s content.</p>
            )}
            <button className="primary" disabled={busy}>
              {busy ? 'Saving…' : editing ? 'Save reply' : 'Delete reply'}
            </button>
          </form>
        </Modal>
      )}
    </aside>
  );
}
