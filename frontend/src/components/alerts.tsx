'use client';

import { useEffect, useRef, useState } from 'react';
import {
  Bell,
  Check,
  CheckCheck,
  Hash,
  MessageCircle,
  RefreshCw,
  ArrowUpRight,
} from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { channelPath, workspacePath, type Channel, type Message, type User } from '@/lib/types';
import { useMemberName } from '@/lib/member-names';
import { Alert, Avatar, Empty, Loading } from './ui';

type InboxAlert = { channel: Channel; message: Message; unread_count: number };
type Filter = 'all' | 'channels' | 'dms';

export function Alerts({
  workspace,
  user,
  query,
  open,
}: {
  workspace: string;
  user: User;
  query: string;
  open: (channel: Channel) => void;
}) {
  const name = useMemberName();
  const [items, setItems] = useState<InboxAlert[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);
  const mutating = useRef(false);
  useEffect(() => {
    let stopped = false;
    let pending = false;
    async function refresh() {
      if (pending || mutating.current || document.visibilityState !== 'visible') return;
      pending = true;
      const current = ++generation.current;
      try {
        const data = await api<{ alerts: InboxAlert[] }>(`${workspacePath(workspace)}/alerts`);
        if (!stopped && current === generation.current) {
          setItems(data.alerts);
          setError('');
        }
      } catch (failure) {
        if (!stopped && current === generation.current) setError(errorMessage(failure));
      } finally {
        pending = false;
        if (!stopped) setLoading(false);
      }
    }
    void refresh();
    const timer = setInterval(refresh, 15000);
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('klack:messages-changed', refresh);
    window.addEventListener('klack:read', refresh);
    return () => {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', refresh);
      window.removeEventListener('klack:messages-changed', refresh);
      window.removeEventListener('klack:read', refresh);
    };
  }, [workspace, attempt]);

  function title(channel: Channel) {
    const peer = channel.direct_key?.split(':').find((id) => id !== user.id.replaceAll('-', ''));
    return peer ? name(peer, user) : channel.name;
  }
  const visible = items.filter(
    ({ channel, message }) =>
      (filter === 'all' || (filter === 'dms' ? !!channel.direct_key : !channel.direct_key)) &&
      `${title(channel)} ${name(message.author_user_id, user)} ${message.body || ''} ${message.attachments?.map((a) => a.filename).join(' ') || ''}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  async function markRead(targets: InboxAlert[]) {
    if (mutating.current) return;
    mutating.current = true;
    ++generation.current;
    setBusy(true);
    setError('');
    const results = await Promise.allSettled(
      targets.map(async (item) => {
        const result = await api<{ unread_count: number }>(
          `${channelPath(workspace, item.channel.id)}/read-cursor`,
          'PUT',
          { message_id: item.message.id },
        );
        setItems((current) =>
          current.flatMap((entry) =>
            entry.channel.id !== item.channel.id
              ? [entry]
              : result.unread_count
                ? [{ ...entry, unread_count: result.unread_count }]
                : [],
          ),
        );
        window.dispatchEvent(
          new CustomEvent('klack:read', { detail: { channel: item.channel.id } }),
        );
      }),
    );
    const failed = results.find((result) => result.status === 'rejected');
    if (failed?.status === 'rejected') setError(errorMessage(failed.reason));
    mutating.current = false;
    setBusy(false);
    // Fetch again on the next scheduled refresh, preserving any mutation error for retry.
  }
  return (
    <section className="page alerts-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">STAY IN THE LOOP</span>
          <h1>
            Alerts <span className="count">{items.length}</span>
          </h1>
          <p>Unread conversations, all in one place. Pick up where your team left off.</p>
        </div>
        <button
          disabled={busy || loading || !visible.length}
          onClick={() => void markRead(visible)}
        >
          <CheckCheck size={17} /> {busy ? 'Marking as read…' : 'Mark shown as read'}
        </button>
      </div>
      <div className="alerts-summary">
        <span className="alerts-bell">
          <Bell size={23} />
        </span>
        <div>
          <h2>
            {items.length
              ? `${items.reduce((sum, item) => sum + item.unread_count, 0)} unread messages`
              : 'A little space to focus'}
          </h2>
          <p>
            {items.length
              ? `Across ${items.length} conversation${items.length === 1 ? '' : 's'} in this workspace.`
              : 'New messages from your channels and teammates will appear here.'}
          </p>
        </div>
      </div>
      <div className="alerts-toolbar">
        <div className="alerts-filters" aria-label="Filter alerts">
          {(['all', 'channels', 'dms'] as const).map((value) => (
            <button
              key={value}
              aria-pressed={filter === value}
              className={filter === value ? 'selected' : ''}
              onClick={() => setFilter(value)}
            >
              {value === 'all'
                ? 'All alerts'
                : value === 'channels'
                  ? 'Channels'
                  : 'Direct messages'}
            </button>
          ))}
        </div>
        <button
          className="icon-button"
          aria-label="Refresh alerts"
          disabled={busy || loading}
          onClick={() => {
            setLoading(true);
            setAttempt((value) => value + 1);
          }}
        >
          <RefreshCw size={17} />
        </button>
      </div>
      {error && (
        <div className="form-stack">
          <Alert>{error}</Alert>
          <button onClick={() => setAttempt((value) => value + 1)}>Retry</button>
        </div>
      )}
      {loading ? (
        <Loading label="Checking your alerts…" />
      ) : visible.length ? (
        <ul className="alerts-list" aria-label="Unread conversations">
          {visible.map((item) => (
            <li className="alert-card" key={item.channel.id}>
              <Avatar name={name(item.message.author_user_id, user)} />
              <div className="alert-content">
                <div className="alert-context">
                  {item.channel.direct_key ? <MessageCircle size={15} /> : <Hash size={15} />}
                  <strong>{title(item.channel)}</strong>
                  {item.channel.archived_at && <span>Archived</span>}
                  <time dateTime={item.message.created_at}>
                    {new Date(item.message.created_at).toLocaleString(undefined, {
                      month: 'short',
                      day: 'numeric',
                      hour: 'numeric',
                      minute: '2-digit',
                    })}
                  </time>
                </div>
                <p className="alert-author">
                  {name(item.message.author_user_id, user)}
                  {item.message.parent_message_id ? ' replied in a thread' : ' sent a message'}
                </p>
                <p className="alert-preview">
                  {item.message.body ||
                    item.message.attachments?.map((a) => a.filename).join(', ') ||
                    'Shared a file'}
                </p>
                <div className="alert-actions">
                  <button className="text-button" onClick={() => open(item.channel)}>
                    Open conversation <ArrowUpRight size={15} />
                  </button>
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => void markRead([item])}
                  >
                    <Check size={15} /> Mark as read
                  </button>
                </div>
              </div>
              <span className="badge purple" aria-label={`${item.unread_count} unread messages`}>
                {item.unread_count}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        !error && (
          <Empty title={query || filter !== 'all' ? 'No matching alerts' : 'You’re all caught up'}>
            {query || filter !== 'all'
              ? 'Try another filter or clear your search.'
              : 'When a new message arrives, you’ll find it here. Enjoy the quiet.'}
          </Empty>
        )
      )}
    </section>
  );
}
