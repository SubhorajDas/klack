'use client';
import { useEffect, useState } from 'react';
import { MessageCircle, Plus, Search, Lock } from 'lucide-react';
import { useMemberName } from '@/lib/member-names';
import { api, errorMessage } from '@/lib/api';
import { workspacePath, type Channel, type Membership, type User } from '@/lib/types';
import { Conversation } from './conversation';
import { Alert, Avatar, Empty, Loading, Modal } from './ui';

export function DirectMessages({
  workspace,
  user,
  selectedId,
  select,
  filter,
}: {
  workspace: string;
  user: User;
  selectedId?: string;
  select: (id?: string) => void;
  filter: string;
}) {
  const memberName = useMemberName();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [members, setMembers] = useState<Membership[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState('');
  const [attempt, setAttempt] = useState(0);
  const path = `${workspacePath(workspace)}/direct-messages`;
  function named(channel: Channel): Channel {
    const peer =
      channel.direct_key?.split(':').find((id) => id !== user.id.replaceAll('-', '')) || '';
    const member = members.find((m) => m.user_id.replaceAll('-', '') === peer);
    return { ...channel, name: memberName(member?.user_id || peer, user) };
  }
  useEffect(() => {
    let stopped = false;
    setLoading(true);
    Promise.all([
      api<{ channels: Channel[] }>(path),
      api<{ memberships: Membership[] }>(`${workspacePath(workspace)}/memberships`),
    ])
      .then(([dms, people]) => {
        if (!stopped) {
          setChannels(dms.channels);
          setMembers(people.memberships);
          setError('');
        }
      })
      .catch((e) => {
        if (!stopped) setError(errorMessage(e));
      })
      .finally(() => {
        if (!stopped) setLoading(false);
      });
    const refresh = () => {
      if (document.visibilityState !== 'visible') return;
      api<{ channels: Channel[] }>(path)
        .then((data) => {
          if (!stopped) setChannels(data.channels);
        })
        .catch(() => {
          /* Keep the last successful list during a temporary outage. */
        });
    };
    const timer = setInterval(refresh, 15000);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [workspace, path, attempt]);
  const selected = channels.find((c) => c.id === selectedId);
  const visible = channels.filter((c) =>
    named(c)
      .name.toLowerCase()
      .includes((selectedId ? query : filter || query).toLowerCase()),
  );
  return (
    <div className={`dm-layout ${selectedId ? 'dm-selected' : ''}`}>
      <aside className="dm-inbox" aria-label="Direct conversations">
        <header>
          <h1>Direct messages</h1>
          <button
            className="icon-button"
            aria-label="New direct message"
            onClick={() => setCreating(true)}
          >
            <Plus size={20} />
          </button>
        </header>
        <div className="dm-search">
          <Search size={16} />
          <input
            aria-label="Search direct conversations"
            placeholder="Find a conversation"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {!creating && error && (
          <div className="form-stack">
            <Alert>{error}</Alert>
            <button onClick={() => setAttempt(attempt + 1)}>Retry</button>
          </div>
        )}
        {loading ? (
          <Loading />
        ) : (
          <div className="dm-list">
            {visible.map((c) => (
              <button
                key={c.id}
                aria-current={c.id === selectedId ? 'page' : undefined}
                onClick={() => select(c.id)}
              >
                <Avatar name={named(c).name} />
                <span>
                  <strong>{named(c).name}</strong>
                  <small>Private conversation</small>
                </span>
                <Unread workspace={workspace} channel={c.id} />
              </button>
            ))}
            {!visible.length && (
              <p className="muted">
                {query || filter
                  ? 'No conversations found.'
                  : 'Your conversations will appear here.'}
              </p>
            )}
          </div>
        )}
        <button className="soft-button" onClick={() => setCreating(true)}>
          <Plus size={16} />
          Start a conversation
        </button>
        <p className="dm-privacy">
          <Lock size={13} />
          Only the two of you can read these messages.
        </p>
      </aside>
      <div className="dm-conversation">
        {selected ? (
          <Conversation
            key={selected.id}
            channel={named(selected)}
            user={user}
            manager={false}
            filter={filter}
            update={(value) =>
              setChannels((current) => current.map((c) => (c.id === value.id ? value : c)))
            }
            browse={() => select()}
          />
        ) : loading ? (
          <Loading />
        ) : (
          <Empty
            title={selectedId ? 'Conversation unavailable' : 'A little more personal'}
            action={
              <button
                className="primary"
                onClick={() => (selectedId ? select() : setCreating(true))}
              >
                <MessageCircle size={17} />
                {selectedId ? 'Back to conversations' : 'Start a conversation'}
              </button>
            }
          >
            {selectedId
              ? 'This conversation could not be found in your workspace.'
              : 'Choose a teammate to share an idea, ask a question, or catch up privately.'}
          </Empty>
        )}
      </div>
      {creating && (
        <Modal title="New direct message" close={() => setCreating(false)}>
          <form
            className="form-stack"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError('');
              const userId = new FormData(e.currentTarget).get('user');
              try {
                const channel = await api<Channel>(path, 'POST', { user_id: userId });
                setChannels((current) => [channel, ...current.filter((c) => c.id !== channel.id)]);
                setCreating(false);
                select(channel.id);
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Alert>{error}</Alert>
            <p className="muted">Start a private conversation with someone in your workspace.</p>
            <label>
              Start a conversation
              <select name="user" required defaultValue="">
                <option value="" disabled>
                  Choose a teammate
                </option>
                {members
                  .filter((m) => m.user_id !== user.id)
                  .map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {memberName(m.user_id, user)}
                    </option>
                  ))}
              </select>
            </label>
            <button
              className="primary"
              disabled={busy || !members.some((m) => m.user_id !== user.id)}
            >
              {busy ? 'Opening…' : 'Open conversation'}
            </button>
            {members.length === 1 && (
              <p className="muted">Invite a teammate from People to start messaging.</p>
            )}
          </form>
        </Modal>
      )}
    </div>
  );
}

export function Unread({ workspace, channel }: { workspace: string; channel: string }) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let stopped = false;
    let debounce: ReturnType<typeof setTimeout>;
    let sequence = 0;
    const refresh = () => {
      if (document.visibilityState !== 'visible') return;
      const current = ++sequence;
      api<{ unread_count: number }>(`${workspacePath(workspace)}/channels/${channel}/read-cursor`)
        .then((r) => {
          if (!stopped && current === sequence) setCount(r.unread_count);
        })
        .catch(() => {
          /* A failed refresh must not falsely clear an unread badge. */
        });
    };
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{ channel?: string }>).detail;
      if (detail?.channel && detail.channel !== channel) return;
      clearTimeout(debounce);
      debounce = setTimeout(refresh, 100);
    };
    refresh();
    const timer = setInterval(refresh, 15000);
    window.addEventListener('klack:read', changed);
    window.addEventListener('klack:messages-changed', changed);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      stopped = true;
      clearInterval(timer);
      clearTimeout(debounce);
      window.removeEventListener('klack:read', changed);
      window.removeEventListener('klack:messages-changed', changed);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [workspace, channel]);
  return count > 0 ? (
    <span className="badge purple" aria-label={`${count} unread messages`}>
      {count > 99 ? '99+' : count}
    </span>
  ) : null;
}
