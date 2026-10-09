'use client';
import { useEffect, useState } from 'react';
import { MessageCircle, Plus, Search, Lock } from 'lucide-react';
import { useMemberName } from '@/lib/member-names';
import { api, ApiError, errorMessage } from '@/lib/api';
import { useCachedApiData } from '@/lib/use-cached-data';
import { type Channel, type Membership, type User } from '@/lib/types';
import { useUnreadCounts, UnreadBadge } from '@/lib/unread-counts';
import { Conversation } from './conversation';
import { Alert, UserAvatar, OnlineLabel, Empty, Loading, Modal } from './ui';

export function DirectMessages({
  user,
  selectedId,
  select,
  filter,
}: {
  user: User;
  selectedId?: string;
  select: (id?: string) => void;
  filter: string;
}) {
  const memberName = useMemberName();
  const path = '/direct-messages';
  const cachedDms = useCachedApiData<{ channels: Channel[] }>(path);
  const cachedMembers = useCachedApiData<{ memberships: Membership[] }>('/contacts');
  const [channelList, setChannels] = useState<Channel[]>(cachedDms?.channels ?? []);
  const [memberList, setMembers] = useState<Membership[]>(cachedMembers?.memberships ?? []);
  const channels = cachedDms?.channels ?? channelList;
  const members = cachedMembers?.memberships ?? memberList;
  const [error, setError] = useState('');
  const [fetching, setLoading] = useState(true);
  const loading = fetching && !cachedDms && !channelList.length;
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState('');
  const [attempt, setAttempt] = useState(0);
  function named(channel: Channel): Channel {
    const peer =
      channel.direct_key?.split(':').find((id) => id !== user.id.replaceAll('-', '')) || '';
    const member = members.find((m) => m.user_id.replaceAll('-', '') === peer);
    return { ...channel, name: memberName(member?.user_id || peer, user) };
  }
  const peerId = (channel: Channel) =>
    channel.direct_key?.split(':').find((id) => id !== user.id.replaceAll('-', '')) || '';
  useEffect(() => {
    let stopped = false;
    setLoading(true);
    Promise.all([
      api<{ channels: Channel[] }>(path),
      api<{ memberships: Membership[] }>('/contacts'),
    ])
      .then(([dms, people]) => {
        if (!stopped) {
          setChannels(dms.channels);
          setMembers(people.memberships);
          setError('');
        }
      })
      .catch((e) => {
        if (!stopped) {
          setError(errorMessage(e));
          if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
            setChannels([]);
            setMembers([]);
          }
        }
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
        .catch((failure) => {
          if (!stopped && failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
            setChannels([]);
            setMembers([]);
          }
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
  }, [path, attempt]);
  useEffect(() => {
    if (!selectedId || loading || channels.some((c) => c.id === selectedId)) return;
    let stopped = false;
    api<Channel>(`/direct-messages/${selectedId}`)
      .then((channel) => {
        if (stopped) return;
        setChannels((current) => [channel, ...current.filter((c) => c.id !== channel.id)]);
        if (channel.id !== selectedId) select(channel.id);
      })
      .catch(() => {
        /* Unavailable conversations keep the normal empty state. */
      });
    return () => {
      stopped = true;
    };
  }, [selectedId, loading, channels, select]);
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
                <UserAvatar userId={peerId(c)} name={named(c).name} />
                <span>
                  <strong>{named(c).name}</strong>
                  <small>
                    <OnlineLabel userId={peerId(c)} />
                  </small>
                </span>
                <Unread channel={c.id} />
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
              ? 'This conversation could not be found.'
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
            <p className="muted">
              Start a private conversation with a teammate from any of your workspaces.
            </p>
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

export function Unread({ channel }: { workspace?: string; channel: string }) {
  const counts = useUnreadCounts();
  return <UnreadBadge count={counts.channels[channel] || 0} />;
}
