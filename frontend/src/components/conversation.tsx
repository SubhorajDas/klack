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
  Plus,
  X,
  MoreHorizontal,
  Pencil,
  Quote,
  Trash2,
  WifiOff,
  Check,
  CheckCheck,
  Archive,
  LogOut,
  MessageSquare,
  FileText,
  Pin,
} from 'lucide-react';
import { useMemberName } from '@/lib/member-names';
import { api, ApiError, errorMessage } from '@/lib/api';
import {
  channelPath,
  type Channel,
  type Message,
  type MessageQuote,
  type User,
  type Membership,
} from '@/lib/types';
import { useConversation } from '@/lib/use-conversation';
import { quoteMessage } from '@/lib/messages';
import { readersFor, typingLabel } from '@/lib/conversation-activity';
import { Alert, Avatar, UserAvatar, OnlineLabel, Empty, Loading, Modal } from './ui';
import { CallHistory, useCalls } from './calls';
import { FilePicker, FilesPanel, type QueuedFile } from './files';
import type { Editor } from '@tiptap/core';
import { RichEditor } from './rich-editor';
import { RichMessage } from './rich-message';
import {
  attachmentKeys,
  emptyDocument,
  legacyDocument,
  messageDocument,
  plainText,
  replaceAttachmentKeys,
  type RichDocument,
} from '@/lib/rich-text';

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
  const peerId =
    channel.direct_key?.split(':').find((id) => id !== user.id.replaceAll('-', '')) || '';
  const [tab, setTab] = useState('messages');
  const replyAction = useRef<(message: Message) => void>(() => {});
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [jumpTarget, setJumpTarget] = useState<string | null>(null);
  const [details, setDetails] = useState<Message | null>(null);
  const [settings, setSettings] = useState(false);
  const [editing, setEditing] = useState<Message | null>(null);
  const [deleting, setDeleting] = useState<Message | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const lastId = useRef('');
  const markedRead = useRef('');
  const markingRead = useRef(false);
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
  async function jumpTo(id: string) {
    setError('');
    if (filter && !visible.some((m) => m.id === id)) {
      setError('Clear the search to view the original message.');
      return;
    }
    atBottom.current = false;
    if (!chat.messages.some((m) => m.id === id) && !(await chat.jump(id))) return;
    setJumpTarget(id);
  }
  useEffect(() => {
    if (!jumpTarget) return;
    const element = document.getElementById(`message-${jumpTarget}`);
    if (!element) return;
    element.scrollIntoView({ block: 'center', behavior: 'smooth' });
    setHighlighted(jumpTarget);
    setJumpTarget(null);
  }, [chat.messages, jumpTarget]);
  useEffect(() => {
    if (!highlighted) return;
    const timer = setTimeout(() => setHighlighted(null), 1800);
    return () => clearTimeout(timer);
  }, [highlighted]);
  async function markRead() {
    const newest = chat.messages.at(-1);
    if (
      !newest ||
      !chat.ready ||
      markingRead.current ||
      newest.id === markedRead.current ||
      document.visibilityState !== 'visible' ||
      !atBottom.current ||
      tab !== 'messages' ||
      chat.browsingHistory ||
      filter
    )
      return;
    markingRead.current = true;
    try {
      await api(`${channelPath(channel.workspace_id, channel.id)}/read-cursor`, 'PUT', {
        message_id: newest.id,
      });
      markedRead.current = newest.id;
      window.dispatchEvent(new Event('klack:read'));
    } catch {
      /* Retry when the conversation is visible again. */
    } finally {
      markingRead.current = false;
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
  }, [chat.messages, chat.ready, tab, filter, chat.browsingHistory]);
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
                {channel.archived_at ? (
                  'Archived · Your conversations are still here.'
                ) : channel.direct_key ? (
                  <OnlineLabel userId={peerId} />
                ) : (
                  'A place for ideas, feedback, and the details.'
                )}
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
            <Alert>{error}</Alert>
            {chat.browsingHistory && (
              <div className="history-banner">
                <span>Viewing earlier messages</span>
                <button
                  disabled={chat.paging}
                  onClick={async () => {
                    if (await chat.latest()) {
                      atBottom.current = true;
                      requestAnimationFrame(() =>
                        list.current?.scrollTo({ top: list.current.scrollHeight }),
                      );
                    }
                  }}
                >
                  Back to latest
                </button>
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
                      <article
                        id={`message-${message.id}`}
                        className={`message ${message.deleted_at ? 'deleted' : ''} ${highlighted === message.id ? 'message-highlight' : ''}`}
                      >
                        <UserAvatar userId={message.author_user_id} name={name} />
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
                            {channel.direct_key &&
                              message.author_user_id === user.id &&
                              !message.deleted_at && (
                                <span
                                  className={`message-receipt ${readersFor(message, chat.readers, user.id).length ? 'read' : ''}`}
                                  role="img"
                                  aria-label={
                                    readersFor(message, chat.readers, user.id).length
                                      ? 'Read'
                                      : 'Sent'
                                  }
                                  title={
                                    readersFor(message, chat.readers, user.id).length
                                      ? 'Read'
                                      : 'Sent'
                                  }
                                >
                                  <CheckCheck size={17} />
                                </span>
                              )}
                          </header>
                          {!message.deleted_at && message.quote && (
                            <QuotePreview
                              quote={message.quote}
                              user={user}
                              onClick={() => void jumpTo(message.quote!.id)}
                            />
                          )}
                          <RichMessage message={message} />
                          <Reactions
                            message={message}
                            user={user}
                            path={chat.path}
                            disabled={!!channel.archived_at}
                            changed={(m) => chat.merge([m])}
                          />
                        </div>
                        <div className="message-actions">
                          {!message.deleted_at && !channel.archived_at && (
                            <button
                              className="icon-button"
                              aria-label="Reply"
                              title="Quote and reply"
                              onClick={() => replyAction.current(message)}
                            >
                              <Quote size={16} />
                            </button>
                          )}
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
            <div className="typing-indicator" role="status" aria-live="polite">
              {typingLabel(
                chat.typingUsers.filter((id) => id !== user.id).map((id) => memberName(id, user)),
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
                typing={chat.typing}
                user={user}
                channel={channel}
                sent={(message) => {
                  atBottom.current = true;
                  chat.merge([message]);
                  if (chat.browsingHistory) void chat.latest();
                }}
                path={chat.path}
                messages={chat.messages}
                registerReply={(action) => {
                  replyAction.current = action;
                }}
              />
            )}
          </>
        )}
      </section>
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
            <UserAvatar
              userId={selected.author_user_id}
              name={memberName(selected.author_user_id, user)}
            />
            <h3>{memberName(selected.author_user_id, user)}</h3>
            <time>{new Date(selected.created_at).toLocaleString()}</time>
            <RichMessage message={selected} />
            {!channel.direct_key && selected.author_user_id === user.id && !selected.deleted_at && (
              <div className="message-readers">
                <h3>Read by</h3>
                {chat.receiptsError ? (
                  <p className="muted">{chat.receiptsError}</p>
                ) : readersFor(selected, chat.readers, user.id).length ? (
                  readersFor(selected, chat.readers, user.id).map((reader) => (
                    <div className="message-reader" key={reader.user_id}>
                      <Avatar name={memberName(reader.user_id, user)} small />
                      <span>{memberName(reader.user_id, user)}</span>
                    </div>
                  ))
                ) : (
                  <p className="muted">No one else has read this message yet.</p>
                )}
              </div>
            )}
            <div className="detail-note">
              <Quote size={22} />
              <strong>Keep the conversation going</strong>
              <button
                onClick={() => {
                  replyAction.current(selected);
                  setDetails(null);
                }}
              >
                Reply
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
          <EditRichMessage
            message={editing}
            busy={busy}
            error={error}
            save={async (document, body) => {
              setBusy(true);
              setError('');
              try {
                const message = await api<Message>(`${chat.path}/${editing.id}`, 'PATCH', {
                  body,
                  document,
                });
                chat.merge([message]);
                setEditing(null);
              } catch (failure) {
                setError(errorMessage(failure));
              } finally {
                setBusy(false);
              }
            }}
          />
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

function QuotePreview({
  quote,
  user,
  onClick,
}: {
  quote: MessageQuote;
  user: User;
  onClick?: () => void;
}) {
  const memberName = useMemberName();
  const content = (
    <>
      <strong>{memberName(quote.author_user_id, user)}</strong>
      <span>
        {quote.deleted_at
          ? 'Message deleted'
          : quote.body ||
            (quote.attachment_count === 1 ? 'Attachment' : `${quote.attachment_count} attachments`)}
      </span>
    </>
  );
  return onClick ? (
    <button
      type="button"
      className="message-quote"
      aria-label="View original message"
      onClick={onClick}
    >
      {content}
    </button>
  ) : (
    <div className="message-quote">{content}</div>
  );
}

function EditRichMessage({
  message,
  busy,
  error,
  save,
}: {
  message: Message;
  busy: boolean;
  error: string;
  save: (document: RichDocument, body: string) => Promise<void>;
}) {
  const [document, setDocument] = useState(() => messageDocument(message));
  const files: QueuedFile[] = (message.attachments || []).map((file) => ({
    key: file.id,
    filename: file.filename,
    size: file.size,
    attachment: file,
  }));
  const body = plainText(document).trim();
  const keys = attachmentKeys(document);
  const intact = keys.length === files.length && files.every((file) => keys.includes(file.key));
  return (
    <form
      className="form-stack"
      onSubmit={(event) => {
        event.preventDefault();
        if (intact && body.length <= 4000) void save(document, body);
      }}
    >
      <Alert>{error}</Alert>
      <RichEditor
        document={document}
        change={setDocument}
        label="Message"
        placeholder="Edit message…"
        locked={busy}
        files={files}
        preserveFiles
        path={`${channelPath(message.workspace_id, message.channel_id)}/files`}
      />
      {!intact && (
        <Alert>
          Keep the existing attachments in this message. Undo to restore a removed attachment.
        </Alert>
      )}
      {body.length > 4000 && <Alert>Keep the message within 4,000 characters.</Alert>}
      <button
        className="primary"
        disabled={busy || !intact || body.length > 4000 || (!body && !files.length)}
      >
        {busy ? 'Saving…' : 'Save changes'}
      </button>
    </form>
  );
}

type Draft = {
  body: string;
  document?: RichDocument;
  id: string;
  attempted: boolean;
  files?: QueuedFile[];
  quote?: MessageQuote;
};
function Composer({
  typing,
  user,
  channel,
  sent,
  path,
  messages,
  registerReply,
}: {
  typing: (active: boolean) => void;
  user: User;
  channel: Channel;
  sent: (message: Message) => void;
  path: string;
  messages: Message[];
  registerReply: (action: (message: Message) => void) => void;
}) {
  const storageKey = `klack:draft:${user.id}:${channel.workspace_id}:${channel.id}:main`;
  const [draft, setDraft] = useState<Draft>(() => {
    try {
      const stored = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
      if (stored && typeof stored.body === 'string' && typeof stored.id === 'string')
        return {
          ...stored,
          document:
            stored.document ||
            (stored.attempted
              ? undefined
              : legacyDocument(
                  stored.body,
                  (stored.files || []).map((file: QueuedFile) => file.key),
                )),
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
    return { body: '', document: emptyDocument(), id: crypto.randomUUID(), attempted: false };
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [emoji, setEmoji] = useState(false);
  const input = useRef<Editor | null>(null);
  const fileActions = useRef({ retry: (_key: string) => {}, remove: (_key: string) => {} });
  const sending = useRef(false);
  const acceptFiles = useRef<(files: File[]) => void>(() => {});
  const files = draft.files || [];
  const effectiveDocument =
    draft.document ||
    legacyDocument(
      draft.body,
      files.map((file) => file.key),
    );
  const placedKeys = attachmentKeys(effectiveDocument);
  const placedFiles = placedKeys.map((key) => files.find((file) => file.key === key));
  const filesReady = placedFiles.every((file) => !!file?.attachment);
  const canSend =
    filesReady &&
    (!!draft.body.trim() || placedKeys.length > 0) &&
    draft.body.length <= 4000 &&
    JSON.stringify(effectiveDocument).length <= 64000;
  const editorFiles = (
    <FilePicker
      inline
      path={`${channelPath(channel.workspace_id, channel.id)}/files`}
      entries={files}
      locked={draft.attempted || busy}
      registerInput={(accept) => {
        acceptFiles.current = accept;
      }}
      registerActions={(actions) => {
        fileActions.current = actions;
      }}
      added={(added) => {
        input.current
          ?.chain()
          .focus()
          .insertContent([
            ...added.map((file) => ({ type: 'attachment', attrs: { id: file.key } })),
            { type: 'paragraph' },
          ])
          .run();
      }}
      change={(change) =>
        setDraft((current) => ({
          ...current,
          files: typeof change === 'function' ? change(current.files || []) : change,
        }))
      }
    />
  );
  useEffect(() => {
    registerReply((message) => {
      if (draft.attempted || sending.current) {
        setError('Retry the unconfirmed send before changing its quote.');
        return;
      }
      setDraft((current) => ({ ...current, quote: quoteMessage(message) }));
      input.current?.commands.focus();
    });
  });
  useEffect(() => {
    if (!draft.quote) return;
    const original = messages.find((m) => m.id === draft.quote!.id);
    if (original && original.revision > draft.quote.revision)
      setDraft((current) => ({ ...current, quote: quoteMessage(original) }));
  }, [messages, draft.quote]);
  useEffect(() => {
    const id = draft.quote?.id;
    if (!id) return;
    let active = true;
    // A restored draft can quote an original outside the newest history page.
    api<{ messages: Message[] }>(`${path}?around=${id}&limit=2`)
      .then((page) => {
        const original = page.messages.find((m) => m.id === id);
        if (active && original)
          setDraft((current) =>
            current.quote?.id === id && original.revision > current.quote.revision
              ? { ...current, quote: quoteMessage(original) }
              : current,
          );
      })
      .catch(() => {
        /* Sending still validates the quote against current channel access. */
      });
    return () => {
      active = false;
    };
  }, [path, draft.quote?.id]);
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
    typing(false);
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
        ...(draft.document
          ? {
              document: replaceAttachmentKeys(
                draft.document,
                new Map(placedFiles.map((file) => [file!.key, file!.attachment!.id])),
              ),
            }
          : {}),
        client_message_id: draft.id,
        ...(placedFiles.length
          ? { attachment_ids: placedFiles.map((file) => file!.attachment!.id) }
          : {}),
        ...(draft.quote ? { reply_to_message_id: draft.quote.id } : {}),
      });
      sent(message);
      for (const file of files)
        if (!placedKeys.includes(file.key) && file.attachment)
          void api(
            `${channelPath(channel.workspace_id, channel.id)}/files/${file.attachment.id}`,
            'DELETE',
          ).catch(() => {});
      const empty = {
        body: '',
        document: emptyDocument(),
        id: crypto.randomUUID(),
        attempted: false,
      };
      setDraft(empty);
      try {
        sessionStorage.removeItem(storageKey);
      } catch {}
      input.current?.commands.focus();
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
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) typing(false);
      }}
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes('Files')) event.preventDefault();
      }}
      onDropCapture={(event) => {
        const dropped = Array.from(event.dataTransfer.files);
        if (!dropped.length || draft.attempted || busy) return;
        event.preventDefault();
        event.stopPropagation();
        const position = input.current?.view.posAtCoords({
          left: event.clientX,
          top: event.clientY,
        });
        if (position) input.current?.commands.setTextSelection(position.pos);
        acceptFiles.current(dropped);
      }}
      onPasteCapture={(event) => {
        const pasted = Array.from(event.clipboardData.files);
        if (pasted.length) {
          event.preventDefault();
          event.stopPropagation();
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
        {draft.quote && (
          <div className="composer-quote" aria-label="Replying to message">
            <QuotePreview quote={draft.quote} user={user} />
            <button
              type="button"
              className="icon-button"
              aria-label="Cancel reply"
              disabled={draft.attempted || busy}
              onClick={() => {
                setDraft((current) => ({ ...current, quote: undefined }));
                input.current?.commands.focus();
              }}
            >
              <X size={18} />
            </button>
          </div>
        )}
        <RichEditor
          document={effectiveDocument}
          label={`Message ${channel.name}`}
          placeholder={
            draft.quote
              ? 'Write a reply…'
              : `Message ${channel.direct_key ? '' : '#'}${channel.name}`
          }
          locked={draft.attempted || busy}
          files={files}
          path={`${channelPath(channel.workspace_id, channel.id)}/files`}
          register={(editor) => {
            input.current = editor;
          }}
          retry={(key) => fileActions.current.retry(key)}
          remove={(key) => fileActions.current.remove(key)}
          change={(document, body) => {
            if (!draft.attempted) typing(!!body.trim());
            setDraft((current) => (current.attempted ? current : { ...current, document, body }));
          }}
          send={() => void submit()}
        />
        {draft.body.length > 4000 && (
          <div className="editor-limit" role="alert">
            Message is too long. Keep it within 4,000 characters.
          </div>
        )}
        <div className="composer-bottom">
          <div className="emoji-control">
            <button
              type="button"
              className="icon-button"
              disabled={draft.attempted || busy}
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
                      input.current
                        ?.chain()
                        .focus()
                        .insertContent({ type: 'text', text: value })
                        .run();
                      setEmoji(false);
                      input.current?.commands.focus();
                    }}
                  >
                    {value}
                  </button>
                ))}
              </div>
            )}
            <span className="composer-hint">
              Shift + Enter for a new line · Ctrl/⌘ + Enter to send
            </span>
          </div>
          <div className="send-controls">
            {editorFiles}
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
  const [pickerOpen, setPickerOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const picker = useRef<HTMLDivElement>(null);
  const emojis = ['👍', '❤️', '😂', '🎉', '👀', '✅'];

  useEffect(() => {
    if (!pickerOpen) return;
    picker.current?.querySelector('button')?.focus();
    const outside = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setPickerOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setPickerOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [pickerOpen]);

  async function toggle(emoji: string) {
    const mine = (message.reactions || []).some((r) => r[0] === emoji && r[1] === user.id);
    setBusy(true);
    setError('');
    try {
      changed(
        await api<Message>(
          `${path}/${message.id}/reactions/${encodeURIComponent(emoji)}`,
          mine ? 'DELETE' : 'PUT',
        ),
      );
      if (pickerOpen) {
        setPickerOpen(false);
        trigger.current?.focus();
      }
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  if (message.deleted_at) return null;
  return (
    <div className="reaction-control" ref={container}>
      <div className="reactions" aria-label="Message reactions">
        {emojis.map((emoji) => {
          const rows = (message.reactions || []).filter((r) => r[0] === emoji);
          if (!rows.length) return null;
          const mine = rows.some((r) => r[1] === user.id);
          return (
            <button
              key={emoji}
              className="reaction-chip"
              aria-label={`${mine ? 'Remove' : 'Add'} ${emoji} reaction`}
              aria-pressed={mine}
              disabled={disabled || busy}
              onClick={() => void toggle(emoji)}
            >
              {emoji}
              {rows.length ? ` ${rows.length}` : ''}
            </button>
          );
        })}
        <button
          ref={trigger}
          className="reaction-add"
          aria-label="Add reaction"
          aria-expanded={pickerOpen}
          aria-haspopup="dialog"
          disabled={disabled}
          onClick={() => setPickerOpen(!pickerOpen)}
        >
          <Smile size={18} />
          <Plus size={10} className="reaction-plus" />
        </button>
      </div>
      {pickerOpen && (
        <div ref={picker} className="reaction-picker" role="dialog" aria-label="Choose a reaction">
          {emojis.map((emoji) => (
            <button
              key={emoji}
              aria-label={`${(message.reactions || []).some((r) => r[0] === emoji && r[1] === user.id) ? 'Remove' : 'Add'} ${emoji} reaction`}
              disabled={disabled || busy}
              onClick={() => void toggle(emoji)}
            >
              {emoji}
            </button>
          ))}
        </div>
      )}
      <Alert>{error}</Alert>
    </div>
  );
}
