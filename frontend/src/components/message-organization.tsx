'use client';

import { useEffect, useRef, useState } from 'react';
import { Bookmark, Pin, Bell, BellOff } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { channelPath, type Channel, type Message, type User } from '@/lib/types';
import { useMemberName } from '@/lib/member-names';
import { Alert, Empty, Loading, Modal } from './ui';
import { RichMessage } from './rich-message';

type Marks = { pinned: string[]; saved: string[] };
type Item = { channel: Channel; message: Message };
type Collection = { items: Item[]; next_offset: number | null };
export function organizationChanged() {
  window.dispatchEvent(new Event('klack:organization'));
}

export function useMessageMarks(channel: Channel) {
  const path = channelPath(channel.workspace_id, channel.id);
  const [marks, setMarks] = useState<Marks | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const mutating = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    let active = true;
    let pending = false;
    async function load() {
      if (pending || mutating.current || document.visibilityState !== 'visible') return;
      pending = true;
      const version = ++generation.current;
      try {
        const result = await api<Marks>(`${path}/message-marks`);
        if (active && version === generation.current) {
          setMarks(result);
          setError('');
        }
      } catch (failure) {
        if (active && version === generation.current) {
          setMarks(null);
          setError(errorMessage(failure));
        }
      } finally {
        pending = false;
      }
    }
    void load();
    const timer = setInterval(load, 15000);
    window.addEventListener('klack:organization', load);
    window.addEventListener('klack:messages-changed', load);
    document.addEventListener('visibilitychange', load);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener('klack:organization', load);
      window.removeEventListener('klack:messages-changed', load);
      document.removeEventListener('visibilitychange', load);
    };
  }, [path]);
  async function toggle(message: Message, kind: 'pin' | 'save') {
    if (!marks || mutating.current) return;
    mutating.current = true;
    ++generation.current;
    setBusy(true);
    setError('');
    const key = kind === 'pin' ? 'pinned' : 'saved';
    const add = !marks[key].includes(message.id);
    let succeeded = false;
    try {
      await api(`${path}/messages/${message.id}/${kind}`, add ? 'PUT' : 'DELETE');
      succeeded = true;
      setMarks((current) =>
        current
          ? {
              ...current,
              [key]: add
                ? [...current[key], message.id]
                : current[key].filter((id) => id !== message.id),
            }
          : null,
      );
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      mutating.current = false;
      setBusy(false);
      if (succeeded) organizationChanged();
    }
  }
  return { marks, error, busy, toggle };
}

export function MessageMarkActions({
  message,
  channel,
  state,
}: {
  message: Message;
  channel: Channel;
  state: ReturnType<typeof useMessageMarks>;
}) {
  if (message.deleted_at) return null;
  const pinned = state.marks?.pinned.includes(message.id) || false;
  const saved = state.marks?.saved.includes(message.id) || false;
  return (
    <>
      <button
        className="icon-button"
        aria-label={pinned ? 'Unpin message' : 'Pin message'}
        title={pinned ? 'Unpin for everyone' : 'Pin for everyone'}
        aria-pressed={pinned}
        disabled={!state.marks || state.busy || !!channel.archived_at}
        onClick={() => void state.toggle(message, 'pin')}
      >
        <Pin size={16} />
      </button>
      <button
        className="icon-button"
        aria-label={saved ? 'Unsave message' : 'Save message'}
        title={saved ? 'Remove from your saved messages' : 'Save privately'}
        aria-pressed={saved}
        disabled={!state.marks || state.busy}
        onClick={() => void state.toggle(message, 'save')}
      >
        <Bookmark size={16} />
      </button>
    </>
  );
}

export function MessageCollection({
  channel,
  user,
  query = '',
  open,
}: {
  channel?: Channel;
  user: User;
  query?: string;
  open: (channel: Channel, message: Message) => void;
}) {
  const name = useMemberName();
  const path = channel
    ? `${channelPath(channel.workspace_id, channel.id)}/pins`
    : '/saved-messages';
  const [data, setData] = useState<Collection>({ items: [], next_offset: null });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const mutating = useRef(false);
  const version = useRef(0);
  const loadedCount = useRef(50);
  useEffect(() => {
    let active = true;
    let pending = false;
    async function load() {
      if (pending || mutating.current || document.visibilityState !== 'visible') return;
      pending = true;
      const current = ++version.current;
      try {
        let result = await api<Collection>(path);
        while (result.next_offset !== null && result.items.length < loadedCount.current) {
          const next = await api<Collection>(`${path}?offset=${result.next_offset}`);
          result = { ...next, items: [...result.items, ...next.items] };
        }
        if (active && current === version.current) {
          setData(result);
          setError('');
        }
      } catch (failure) {
        if (active && current === version.current) {
          setData({ items: [], next_offset: null });
          setError(errorMessage(failure));
        }
      } finally {
        pending = false;
        if (active) setLoading(false);
      }
    }
    void load();
    const timer = setInterval(load, 15000);
    window.addEventListener('klack:organization', load);
    window.addEventListener('klack:messages-changed', load);
    document.addEventListener('visibilitychange', load);
    return () => {
      active = false;
      ++version.current;
      clearInterval(timer);
      window.removeEventListener('klack:organization', load);
      window.removeEventListener('klack:messages-changed', load);
      document.removeEventListener('visibilitychange', load);
    };
  }, [path, attempt]);
  async function remove(item: Item) {
    if (mutating.current) return;
    let succeeded = false;
    mutating.current = true;
    ++version.current;
    setBusy(true);
    setError('');
    try {
      await api(
        `${channelPath(item.channel.workspace_id, item.channel.id)}/messages/${item.message.id}/${channel ? 'pin' : 'save'}`,
        'DELETE',
      );
      succeeded = true;
      setData((current) => ({
        ...current,
        items: current.items.filter((row) => row.message.id !== item.message.id),
      }));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      mutating.current = false;
      setBusy(false);
      if (succeeded) organizationChanged();
    }
  }
  async function older() {
    if (mutating.current || data.next_offset === null) return;
    mutating.current = true;
    ++version.current;
    setBusy(true);
    setError('');
    try {
      const next = await api<Collection>(`${path}?offset=${data.next_offset}`);
      loadedCount.current = Math.max(50, data.items.length + next.items.length);
      setData((current) => ({
        ...next,
        items: [
          ...current.items,
          ...next.items.filter(
            (row) => !current.items.some((old) => old.message.id === row.message.id),
          ),
        ],
      }));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      mutating.current = false;
      setBusy(false);
    }
  }
  const visible = data.items.filter(({ message }) =>
    (message.body || '').toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <section className="page collection-page">
      <div className="page-heading">
        <div>
          <h1>{channel ? 'Pinned messages' : 'Saved messages'}</h1>
          <p>
            {channel
              ? 'Shared references for everyone in this conversation.'
              : 'Your private collection across channels and direct messages.'}
          </p>
        </div>
        <button disabled={loading || busy} onClick={() => setAttempt((value) => value + 1)}>
          Refresh
        </button>
      </div>
      <Alert>{error}</Alert>
      {loading ? (
        <Loading label="Loading messages…" />
      ) : visible.length ? (
        <ul className="collection-list">
          {visible.map((item) => (
            <li key={item.message.id} className="collection-card">
              <div className="collection-context">
                <strong>{name(item.message.author_user_id, user)}</strong>
                <span>{item.channel.direct_key ? 'Direct message' : `#${item.channel.name}`}</span>
                <time>{new Date(item.message.created_at).toLocaleString()}</time>
              </div>
              <RichMessage message={item.message} />
              <div className="button-row">
                <button onClick={() => open(item.channel, item.message)}>
                  View in conversation
                </button>
                <button
                  disabled={busy || (!!channel && !!channel.archived_at)}
                  onClick={() => void remove(item)}
                >
                  {channel ? 'Unpin' : 'Remove from saved'}
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        !error && (
          <Empty
            title={
              query
                ? 'No matching messages'
                : channel
                  ? 'No pinned messages yet'
                  : 'No saved messages yet'
            }
          >
            {channel
              ? 'Use the pin action on a message to keep it here.'
              : 'Use the bookmark action on a message to save it privately.'}
          </Empty>
        )
      )}
      {data.next_offset !== null && (
        <button disabled={busy} onClick={() => void older()}>
          {busy ? 'Loading…' : 'Load more'}
        </button>
      )}
    </section>
  );
}

type Mode = 'all' | 'mentions' | 'muted';
export function ConversationNotifications({ channel }: { channel: Channel }) {
  const path = `${channelPath(channel.workspace_id, channel.id)}/notifications`;
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setMode(null);
    api<{ mode: Mode }>(path)
      .then((data) => {
        if (active) {
          setMode(data.mode);
          setError('');
        }
      })
      .catch((failure) => {
        if (active) setError(errorMessage(failure));
      });
    return () => {
      active = false;
    };
  }, [path, open, attempt]);
  async function save(next: Mode) {
    setBusy(true);
    setError('');
    try {
      const result = await api<{ mode: Mode }>(path, 'PUT', { mode: next });
      setMode(result.mode);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button
        className="icon-button"
        aria-label="Conversation notifications"
        title="Conversation notifications"
        onClick={() => setOpen(true)}
      >
        {mode === 'muted' ? <BellOff size={19} /> : <Bell size={19} />}
      </button>
      {open && (
        <Modal title="Conversation notifications" close={() => setOpen(false)}>
          <div className="form-stack">
            <p>
              Choose which unread messages appear in Alerts. Message history and unread counts
              remain available.
            </p>
            <Alert>{error}</Alert>
            {!mode && !error && <Loading />}
            {(['all', 'mentions', 'muted'] as const).map((value) => (
              <label className="notification-choice" key={value}>
                <input
                  type="radio"
                  name="notification-mode"
                  checked={mode === value}
                  disabled={busy || !mode}
                  onChange={() => void save(value)}
                />
                <span>
                  <strong>
                    {value === 'all'
                      ? 'All messages'
                      : value === 'mentions'
                        ? 'Mentions only'
                        : 'Mute conversation'}
                  </strong>
                  <small>
                    {value === 'all'
                      ? 'Show every unread message.'
                      : value === 'mentions'
                        ? 'Only messages that explicitly @mention you.'
                        : 'Hide this conversation from Alerts.'}
                  </small>
                </span>
              </label>
            ))}
            {error && <button onClick={() => setAttempt((value) => value + 1)}>Retry</button>}
            <small role="status">
              {busy ? 'Saving…' : mode ? 'Preference saved across your devices.' : ''}
            </small>
          </div>
        </Modal>
      )}
    </>
  );
}

export function NotificationPause({ changed }: { changed: () => void }) {
  const [until, setUntil] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const paused = !!until && new Date(until).getTime() > Date.now();
  useEffect(() => {
    let active = true;
    api<{ paused_until: string | null }>('/notification-pause')
      .then((data) => {
        if (active) {
          setUntil(data.paused_until);
          setLoaded(true);
          setError('');
        }
      })
      .catch((failure) => {
        if (active) setError(errorMessage(failure));
      });
    return () => {
      active = false;
    };
  }, [attempt]);
  useEffect(() => {
    if (!paused || !until) return;
    const timer = setTimeout(
      () => {
        setUntil(null);
        changed();
      },
      Math.max(0, new Date(until).getTime() - Date.now()) + 100,
    );
    return () => clearTimeout(timer);
  }, [until, paused, changed]);
  async function save(minutes: number) {
    setBusy(true);
    setError('');
    try {
      const result = await api<{ paused_until: string | null }>('/notification-pause', 'PUT', {
        minutes,
      });
      setUntil(result.paused_until);
      changed();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="notification-pause">
      <label>
        {paused ? `Alerts paused until ${new Date(until!).toLocaleString()}` : 'Pause alerts'}
        <select
          aria-label="Pause alerts"
          disabled={!loaded || busy}
          value=""
          onChange={(event) => void save(Number(event.target.value))}
        >
          <option value="" disabled>
            {busy ? 'Saving…' : 'Choose duration'}
          </option>
          <option value="30">30 minutes</option>
          <option value="60">1 hour</option>
          <option value="120">2 hours</option>
          <option value="480">8 hours</option>
          <option value="1440">24 hours</option>
        </select>
      </label>
      {paused && (
        <button disabled={busy} onClick={() => void save(0)}>
          Resume alerts
        </button>
      )}
      <small>Applies to Alerts across your devices. Calls and unread counts stay available.</small>
      <Alert>{error}</Alert>
      {error && <button onClick={() => setAttempt((value) => value + 1)}>Retry</button>}
    </div>
  );
}
