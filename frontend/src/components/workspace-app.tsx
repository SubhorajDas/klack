'use client';
import { useEffect, useState, type FormEvent } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import {
  Home,
  MessageCircle,
  Bell,
  Bookmark,
  Search,
  Hash,
  Plus,
  ChevronDown,
  Users,
  Settings,
  HelpCircle,
  Menu,
  X,
  Lock,
  ArrowUpRight,
  Sparkles,
  LogOut,
  ArrowRight,
  LayoutGrid,
} from 'lucide-react';
import { api, ApiError, errorMessage } from '@/lib/api';
import {
  workspacePath,
  type Channel,
  type User,
  type Workspace,
  type Membership,
} from '@/lib/types';
import { Alert, Avatar, Empty, Loading, Logo, Modal } from './ui';
import { MemberNamesProvider } from '@/lib/member-names';
import { DirectMessages, Unread } from './direct-messages';
import { Conversation } from './conversation';
import { People, Account } from './management';
import { Alerts } from './alerts';

type View = 'home' | 'channels' | 'people' | 'settings' | 'dms' | 'activity' | 'saved' | 'channel';
type Dialog =
  'workspace' | 'create-workspace' | 'create-channel' | 'join' | 'help' | 'leave-workspace' | null;
export function WorkspaceApp({
  user,
  setUser,
}: {
  user: User;
  setUser: (user: User | null) => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const parts = pathname.split('/');
  const routeWorkspace = parts[1] === 'w' ? parts[2] : undefined;
  const routeChannel = parts[3] === 'channel' ? parts[4] : undefined;
  const routeDirect = parts[3] === 'dms' ? parts[4] : undefined;
  const routeView = (pathname === '/settings' ? 'settings' : parts[3]) as View | undefined;
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState('');
  const [channels, setChannels] = useState<Channel[]>([]);
  const [role, setRole] = useState<Membership['role']>('member');
  const [view, setView] = useState<View>('home');
  const [channelId, setChannelId] = useState('');
  const [loading, setLoading] = useState(true);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState<Dialog>(pathname === '/join' ? 'join' : null);
  const [mobile, setMobile] = useState(false);
  const [filter, setFilter] = useState('');
  const [joinedOnly, setJoinedOnly] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const workspace = workspaces.find((item) => item.id === workspaceId);
  const channel = channels.find((item) => item.id === channelId);
  const manager = role === 'owner' || role === 'admin';

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<{ workspaces: Workspace[] }>('/workspaces')
      .then((data) => {
        if (cancelled) return;
        setWorkspaces(data.workspaces);
        setWorkspaceId((current) =>
          data.workspaces.some((item) => item.id === (routeWorkspace || current))
            ? routeWorkspace || current
            : data.workspaces[0]?.id || '',
        );
        setError('');
      })
      .catch((failure) => {
        if (!cancelled) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refresh, routeWorkspace]);
  useEffect(() => {
    if (!workspaceId) {
      setChannels([]);
      setRole('member');
      setWorkspaceLoading(false);
      return;
    }
    let cancelled = false;
    setWorkspaceLoading(true);
    setChannels([]);
    setRole('member');
    setError('');
    Promise.all([
      api<{ channels: Channel[] }>(`${workspacePath(workspaceId)}/channels?include_archived=true`),
      api<Membership>(`${workspacePath(workspaceId)}/memberships/me`),
    ])
      .then(([data, membership]) => {
        if (!cancelled) {
          setChannels(data.channels);
          setRole(membership.role);
        }
      })
      .catch((failure) => {
        if (!cancelled) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!cancelled) setWorkspaceLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, refresh]);
  useEffect(() => {
    setView(
      routeChannel
        ? 'channel'
        : ['home', 'channels', 'people', 'settings', 'dms', 'activity', 'saved'].includes(
              routeView || '',
            )
          ? routeView!
          : 'home',
    );
    setChannelId(routeChannel || '');
    if (pathname === '/join') setDialog('join');
  }, [pathname, routeChannel, routeView]);

  function navigate(next: View, id?: string) {
    setMobile(false);
    setFilter('');
    if (next === 'settings') {
      router.push('/settings');
      return;
    }
    if (!workspace) {
      router.push('/');
      return;
    }
    router.push(`/w/${workspaceId}/${next}${id ? `/${id}` : ''}`);
  }
  function switchWorkspace(id: string) {
    setWorkspaceId(id);
    setDialog(null);
    router.push(`/w/${id}/home`);
  }
  function channelUpdated(value: Channel) {
    setChannels((current) => current.map((item) => (item.id === value.id ? value : item)));
  }
  function workspaceLeft() {
    const remaining = workspaces.filter((item) => item.id !== workspaceId);
    const next = remaining[0]?.id || '';
    setWorkspaces(remaining);
    setWorkspaceId(next);
    setChannels([]);
    setRole('member');
    setChannelId('');
    setFilter('');
    setView('home');
    setMobile(false);
    setDialog(null);
    router.replace(next ? `/w/${next}/home` : '/');
    setRefresh((value) => value + 1);
  }
  async function signOut() {
    try {
      await api('/auth/logout', 'POST');
      setUser(null);
      router.replace('/login');
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }
  const visibleChannels = channels.filter(
    (item) =>
      (!joinedOnly || item.is_member) && item.name.toLowerCase().includes(filter.toLowerCase()),
  );
  const navItems = [
    { key: 'home', label: 'Home', icon: Home },
    { key: 'dms', label: 'Direct messages', icon: MessageCircle },
    { key: 'activity', label: 'Alerts', icon: Bell },
    { key: 'saved', label: 'Saved', icon: Bookmark },
  ] as const;
  if (loading && !workspaces.length)
    return (
      <main className="boot">
        <Logo />
        <Loading label="Finding your workspaces…" />
      </main>
    );
  return (
    <MemberNamesProvider key={workspaceId} workspace={workspaceId}>
      <div className="app-shell">
        {mobile && (
          <button
            className="mobile-shade"
            aria-label="Close navigation"
            onClick={() => setMobile(false)}
          />
        )}
        <aside className={`sidebar ${mobile ? 'mobile-open' : ''}`}>
          <button className="workspace-switch" onClick={() => setDialog('workspace')}>
            <span className="logo">{workspace?.name[0]?.toUpperCase() || 'K'}</span>
            <strong>{workspace?.name || 'Your workspaces'}</strong>
            <ChevronDown size={17} />
          </button>
          <nav aria-label="Main navigation">
            {navItems
              .filter((item) => workspace || item.key === 'home')
              .map((item) => (
                <button
                  key={item.key}
                  className={`nav-item ${view === item.key ? 'active' : ''}`}
                  onClick={() => navigate(item.key)}
                >
                  <item.icon size={20} />
                  {item.label}
                  {item.key === 'saved' && <span className="nav-soon">Soon</span>}
                </button>
              ))}
            {workspace && (
              <button
                className={`nav-item ${view === 'channels' ? 'active' : ''}`}
                onClick={() => navigate('channels')}
              >
                <LayoutGrid size={20} />
                Browse channels
              </button>
            )}
          </nav>
          {workspace && (
            <>
              <div className="sidebar-section">
                <span>
                  <ChevronDown size={14} />
                  Channels
                </span>
                {manager && (
                  <button
                    className="icon-button"
                    aria-label="Create channel"
                    onClick={() => setDialog('create-channel')}
                  >
                    <Plus size={17} />
                  </button>
                )}
              </div>
              <div className="channel-list">
                {channels
                  .filter((item) => item.is_member && !item.archived_at)
                  .map((item) => (
                    <button
                      key={item.id}
                      className={`nav-item channel-link ${view === 'channel' && item.id === channelId ? 'active' : ''}`}
                      onClick={() => navigate('channel', item.id)}
                    >
                      {item.visibility === 'private' ? <Lock size={17} /> : <Hash size={19} />}
                      <span>{item.name}</span>
                      <Unread workspace={workspaceId} channel={item.id} />
                    </button>
                  ))}
                {!channels.some((item) => item.is_member) && (
                  <p className="sidebar-hint">Your conversations will feel at home here.</p>
                )}
                <button
                  className="nav-item channel-link subtle"
                  onClick={() => navigate('channels')}
                >
                  <Plus size={19} />
                  Add channels
                </button>
              </div>
              <div className="sidebar-section">
                <span>WORKSPACE</span>
              </div>
              <button
                className={`nav-item ${view === 'people' ? 'active' : ''}`}
                onClick={() => navigate('people')}
              >
                <Users size={19} />
                People
              </button>
              <button
                className={`nav-item ${view === 'settings' ? 'active' : ''}`}
                onClick={() => navigate('settings')}
              >
                <Settings size={19} />
                Settings
              </button>
            </>
          )}
          <div className="sidebar-bottom">
            <div className="workspace-note">
              <Sparkles size={18} />
              <p>
                A space for good ideas.
                <br />
                <span>And even better teamwork.</span>
              </p>
            </div>
            <button className="user-menu" onClick={() => navigate('settings')}>
              <Avatar name={user.email} small />
              <span>
                <strong>{user.email.split('@')[0]}</strong>
                <small>My account</small>
              </span>
              <Settings size={17} />
            </button>
          </div>
        </aside>
        <main className="app-main">
          <header className="topbar">
            <button
              className="icon-button mobile-menu"
              aria-label="Open navigation"
              onClick={() => setMobile(true)}
            >
              <Menu size={22} />
            </button>
            {workspace ? (
              <div className="topbar-search">
                <Search size={17} />
                <input
                  aria-label={
                    view === 'channel' || (view === 'dms' && routeDirect)
                      ? 'Search loaded messages'
                      : view === 'activity'
                        ? 'Search alerts'
                        : view === 'dms'
                          ? 'Search direct messages'
                          : 'Search channels'
                  }
                  placeholder={
                    view === 'channel' || (view === 'dms' && routeDirect)
                      ? 'Search messages in this conversation'
                      : view === 'activity'
                        ? 'Find an unread conversation'
                        : view === 'dms'
                          ? 'Find a direct conversation'
                          : 'Find a channel in your workspace'
                  }
                  value={filter}
                  onChange={(event) => {
                    setFilter(event.target.value);
                    if (
                      view !== 'channel' &&
                      view !== 'channels' &&
                      view !== 'dms' &&
                      view !== 'activity'
                    ) {
                      setView('channels');
                      router.push(`/w/${workspaceId}/channels`);
                    }
                  }}
                />
                {filter && (
                  <button
                    className="icon-button"
                    aria-label="Clear search"
                    onClick={() => setFilter('')}
                  >
                    <X size={15} />
                  </button>
                )}
              </div>
            ) : (
              <div style={{ flex: 1 }} />
            )}
            <div className="topbar-actions">
              <button className="icon-button" aria-label="Help" onClick={() => setDialog('help')}>
                <HelpCircle size={20} />
              </button>
              <button
                className="icon-button"
                aria-label="Account settings"
                onClick={() => navigate('settings')}
              >
                <Settings size={20} />
              </button>
              <button
                className="profile-button"
                aria-label="Your profile"
                onClick={() => navigate('settings')}
              >
                <Avatar name={user.email} small />
              </button>
            </div>
          </header>
          {error && (
            <div className="page-alert">
              <Alert>{error}</Alert>
              <button onClick={() => setRefresh(refresh + 1)}>Retry</button>
            </div>
          )}
          {view === 'settings' ? (
            <Account user={user} updateUser={setUser} signOut={signOut} />
          ) : !workspace ? (
            <section className="page home-page">
              <div className="page-heading">
                <div>
                  <span className="eyebrow">WELCOME TO KLACK</span>
                  <h1>Your team starts here, {user.email.split('@')[0]}</h1>
                  <p>Create a workspace or join your team to start chatting.</p>
                </div>
              </div>
              <div className="welcome-banner">
                <div>
                  <span className="pill">YOUR FIRST WORKSPACE</span>
                  <h2>
                    A shared space for
                    <br />
                    your people and ideas.
                  </h2>
                  <p>Workspaces bring your team's channels, messages, and people together.</p>
                  <button onClick={() => setDialog('create-workspace')}>
                    <Plus size={17} /> Create a workspace
                  </button>
                </div>
                <div className="banner-art" aria-hidden="true">
                  <MessageCircle size={110} strokeWidth={1} />
                  <span>hello, team ✨</span>
                  <div>better together</div>
                </div>
              </div>
              <div className="section-heading">
                <div>
                  <h2>Already have a team on Klack?</h2>
                  <p>Ask a workspace owner or admin for an invitation link.</p>
                </div>
              </div>
              <button onClick={() => setDialog('join')}>
                Join with an invite <ArrowRight size={17} />
              </button>
            </section>
          ) : workspaceLoading ? (
            <Loading label="Opening workspace…" />
          ) : view === 'channel' ? (
            channel ? (
              <Conversation
                key={`${user.id}:${workspaceId}:${channelId}`}
                user={user}
                channel={channel}
                manager={manager}
                filter={filter}
                update={channelUpdated}
                browse={() => navigate('channels')}
              />
            ) : (
              <Empty
                title="Channel unavailable"
                action={<button onClick={() => navigate('channels')}>Browse channels</button>}
              >
                This channel may be private, removed, or outside your workspace.
              </Empty>
            )
          ) : view === 'home' ? (
            <section className="page home-page">
              <div className="page-heading">
                <div>
                  <span className="eyebrow">YOUR WORKSPACE, AT A GLANCE</span>
                  <h1>
                    Welcome back, {user.email.split('@')[0]} <span className="wave">✦</span>
                  </h1>
                  <p>A focused team makes great things happen.</p>
                </div>
                <span className="date-label">
                  {new Date().toLocaleDateString(undefined, {
                    weekday: 'long',
                    month: 'long',
                    day: 'numeric',
                  })}
                </span>
              </div>
              <div className="welcome-banner">
                <div>
                  <span className="pill">BETTER TOGETHER</span>
                  <h2>
                    Great work starts
                    <br />
                    with a conversation.
                  </h2>
                  <p>Find your people. Share an idea. Move things forward.</p>
                  <button onClick={() => navigate('channels')}>
                    Explore channels <ArrowRight size={17} />
                  </button>
                </div>
                <div className="banner-art" aria-hidden="true">
                  <MessageCircle size={110} strokeWidth={1} />
                  <span>hello, team ✨</span>
                  <div># good-ideas</div>
                </div>
              </div>
              <div className="section-heading">
                <h2>
                  Your channels{' '}
                  <span className="count">{channels.filter((item) => item.is_member).length}</span>
                </h2>
                <button className="text-button" onClick={() => navigate('channels')}>
                  Browse all <ArrowUpRight size={15} />
                </button>
              </div>
              <div className="home-channels">
                {channels
                  .filter((item) => item.is_member)
                  .map((item) => (
                    <button key={item.id} onClick={() => navigate('channel', item.id)}>
                      <span className="channel-symbol">
                        {item.visibility === 'private' ? <Lock size={21} /> : <Hash size={23} />}
                      </span>
                      <span>
                        <strong>{item.name}</strong>
                        <small>
                          {item.archived_at
                            ? 'Archived · read only'
                            : `${item.visibility === 'private' ? 'Private' : 'Public'} channel`}
                        </small>
                      </span>
                      <ArrowUpRight size={18} />
                    </button>
                  ))}
                {!channels.some((item) => item.is_member) && (
                  <Empty title="Find your first conversation">
                    Browse public channels or create a space for your team.
                  </Empty>
                )}
              </div>
              <div className="home-footer">
                <span>
                  <Logo /> A little less noise. A lot more together.
                </span>
                <button className="text-button" onClick={() => navigate('people')}>
                  Meet your workspace <ArrowRight size={15} />
                </button>
              </div>
            </section>
          ) : view === 'channels' ? (
            <section className="page">
              <div className="page-heading">
                <div>
                  <h1>Browse channels</h1>
                  <p>Discover a space to share ideas and collaborate with your team.</p>
                </div>
                {manager && (
                  <button className="primary" onClick={() => setDialog('create-channel')}>
                    <Plus size={17} />
                    Create channel
                  </button>
                )}
              </div>
              <div className="tabs">
                <button
                  className={!joinedOnly ? 'selected' : ''}
                  onClick={() => setJoinedOnly(false)}
                >
                  All channels <span>{channels.length}</span>
                </button>
                <button
                  className={joinedOnly ? 'selected' : ''}
                  onClick={() => setJoinedOnly(true)}
                >
                  Joined <span>{channels.filter((item) => item.is_member).length}</span>
                </button>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Channel</th>
                      <th>Visibility</th>
                      <th>Status</th>
                      <th aria-label="Actions" />
                    </tr>
                  </thead>
                  <tbody>
                    {visibleChannels.map((item) => (
                      <tr key={item.id}>
                        <td>
                          <span className="table-channel">
                            {item.visibility === 'private' ? (
                              <Lock size={17} />
                            ) : (
                              <Hash size={19} />
                            )}
                            <strong>{item.name}</strong>
                          </span>
                        </td>
                        <td>
                          <span className="capitalize">{item.visibility}</span>
                        </td>
                        <td>
                          <span className={`badge ${item.is_member ? 'purple' : ''}`}>
                            {item.archived_at
                              ? 'Archived'
                              : item.is_member
                                ? 'Joined'
                                : 'Not joined'}
                          </span>
                        </td>
                        <td>
                          <button
                            className={item.is_member ? 'soft-button' : ''}
                            onClick={() => navigate('channel', item.id)}
                          >
                            {item.is_member ? 'Open' : 'View'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!visibleChannels.length && (
                <Empty title={filter ? 'No channels found' : 'Make room for a new conversation'}>
                  {filter
                    ? 'Try a different name or clear your search.'
                    : 'Channels keep your team’s conversations organized.'}
                </Empty>
              )}
            </section>
          ) : view === 'dms' ? (
            <DirectMessages
              key={workspaceId}
              workspace={workspaceId}
              user={user}
              selectedId={routeDirect}
              filter={filter}
              select={(id) => {
                setFilter('');
                router.push(`/w/${workspaceId}/dms${id ? `/${id}` : ''}`);
              }}
            />
          ) : view === 'activity' ? (
            <Alerts
              key={`${user.id}:${workspaceId}`}
              workspace={workspaceId}
              user={user}
              query={filter}
              open={(item) => navigate(item.direct_key ? 'dms' : 'channel', item.id)}
            />
          ) : view === 'people' ? (
            <People
              key={workspaceId}
              user={user}
              workspace={workspace}
              role={role}
              leave={() => setDialog('leave-workspace')}
            />
          ) : (
            <Empty
              title={
                {
                  saved: 'Keep the good stuff close',
                }[view]
              }
              action={
                <button className="primary" onClick={() => navigate('channels')}>
                  Explore channels <ArrowRight size={17} />
                </button>
              }
            >
              {
                {
                  saved:
                    'Saved messages are coming soon. Your channel conversations are always there when you need them.',
                }[view]
              }
            </Empty>
          )}
        </main>
        {dialog && (
          <Modal
            title={
              {
                workspace: 'Your workspaces',
                'create-workspace': 'Create a workspace',
                'create-channel': 'Create a channel',
                join: 'Join your team',
                help: 'Welcome to Klack',
                'leave-workspace': 'Leave workspace',
              }[dialog]
            }
            close={() => setDialog(null)}
          >
            {dialog === 'workspace' ? (
              <div className="form-stack">
                {workspaces.map((item) => (
                  <button
                    className="workspace-option"
                    key={item.id}
                    onClick={() => switchWorkspace(item.id)}
                  >
                    <span className="logo">{item.name[0]}</span>
                    <strong>{item.name}</strong>
                    <ArrowRight size={18} />
                  </button>
                ))}
                <button onClick={() => setDialog('create-workspace')}>
                  <Plus size={17} />
                  Create a workspace
                </button>
                <button onClick={() => setDialog('join')}>Join with an invite</button>
                {workspace && (
                  <button className="danger-outline" onClick={() => setDialog('leave-workspace')}>
                    <LogOut size={17} /> Leave workspace
                  </button>
                )}
                <button className="text-button" onClick={signOut}>
                  <LogOut size={17} />
                  Sign out
                </button>
              </div>
            ) : dialog === 'leave-workspace' ? (
              workspace && (
                <LeaveWorkspace
                  workspace={workspace}
                  role={role}
                  done={workspaceLeft}
                  cancel={() => setDialog(null)}
                />
              )
            ) : dialog === 'help' ? (
              <div className="help-copy">
                <p>
                  Choose a channel, join the conversation, and send your first message. Messages
                  update live while you’re connected.
                </p>
                <p>
                  Workspace owners and admins can create channels and share invitations from People.
                  You can edit or delete your own messages.
                </p>
                <p>Check Alerts for unread channel messages and private conversations.</p>
                <button className="primary" onClick={() => setDialog(null)}>
                  Got it
                </button>
              </div>
            ) : (
              <CreationForm
                key={dialog}
                kind={dialog}
                workspaceId={workspaceId}
                done={(value) => {
                  setDialog(null);
                  setRefresh(refresh + 1);
                  if (dialog === 'create-workspace' || dialog === 'join') switchWorkspace(value);
                  else navigate('channel', value);
                }}
              />
            )}
          </Modal>
        )}
      </div>
    </MemberNamesProvider>
  );
}

function CreationForm({
  kind,
  workspaceId,
  done,
}: {
  kind: 'create-workspace' | 'create-channel' | 'join';
  workspaceId: string;
  done: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [visibility, setVisibility] = useState('public');
  const [initialToken] = useState(
    () => new URLSearchParams(window.location.hash.slice(1)).get('token') || '',
  );
  useEffect(() => {
    if (kind === 'join' && initialToken)
      window.history.replaceState(null, '', window.location.pathname);
  }, [kind, initialToken]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    const data = new FormData(event.currentTarget);
    try {
      if (kind === 'join') {
        let token = String(data.get('token')).trim();
        try {
          token = new URLSearchParams(new URL(token).hash.slice(1)).get('token') || token;
        } catch {
          /* Raw tokens are also accepted. */
        }
        const membership = await api<Membership>('/workspace-invitations/accept', 'POST', {
          token,
        });
        done(membership.workspace_id);
      } else if (kind === 'create-workspace') {
        const result = await api<Workspace>('/workspaces', 'POST', { name: data.get('name') });
        done(result.id);
      } else {
        const result = await api<Channel>(`${workspacePath(workspaceId)}/channels`, 'POST', {
          name: data.get('name'),
          visibility,
        });
        done(result.id);
      }
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="form-stack" onSubmit={submit}>
      <p className="muted">
        {kind === 'join'
          ? 'Paste the single-use invitation shared by your workspace admin.'
          : kind === 'create-channel'
            ? 'Give your team a space for a topic, project, or a good idea.'
            : 'A shared home for your people and projects.'}
      </p>
      <Alert>{error}</Alert>
      {kind === 'join' ? (
        <label>
          Invitation link or token
          <input
            name="token"
            defaultValue={initialToken}
            required
            autoFocus
            placeholder="Paste your invitation"
          />
        </label>
      ) : (
        <label>
          {kind === 'create-workspace' ? 'Workspace name' : 'Channel name'}
          <input
            name="name"
            required
            maxLength={kind === 'create-workspace' ? 100 : 80}
            autoFocus
            placeholder={kind === 'create-workspace' ? 'e.g. Design team' : 'e.g. product-design'}
          />
        </label>
      )}
      {kind === 'create-channel' && (
        <fieldset className="visibility-options">
          <legend>Visibility</legend>
          {['public', 'private'].map((value) => (
            <label key={value}>
              <input
                type="radio"
                checked={visibility === value}
                onChange={() => setVisibility(value)}
                name="visibility"
                value={value}
              />
              <span>
                <strong className="capitalize">{value}</strong>
                <small>
                  {value === 'public'
                    ? 'Anyone in your workspace can find and join this channel.'
                    : 'Only added members can read and participate.'}
                </small>
              </span>
            </label>
          ))}
        </fieldset>
      )}
      <button className="primary wide" disabled={busy}>
        {busy
          ? 'Please wait…'
          : kind === 'join'
            ? 'Join workspace'
            : kind === 'create-channel'
              ? 'Create channel'
              : 'Create workspace'}
      </button>
    </form>
  );
}

function LeaveWorkspace({
  workspace,
  role,
  done,
  cancel,
}: {
  workspace: Workspace;
  role: Membership['role'];
  done: () => void;
  cancel: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function leave() {
    setBusy(true);
    setError('');
    try {
      await api(`${workspacePath(workspace.id)}/leave`, 'POST');
      done();
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.code === 'owner_invariant_violation'
          ? 'You are the last owner. Make another member an owner from People before leaving this workspace.'
          : errorMessage(failure),
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="form-stack">
      <p>
        Leave <strong>{workspace.name}</strong>? You will lose access to its channels and
        conversations. You will need a new invitation to rejoin.
      </p>
      <p className="muted">Your account and your other workspaces will stay available.</p>
      {role === 'owner' && (
        <p className="muted">At least one other member must be an owner before you can leave.</p>
      )}
      <Alert>{error}</Alert>
      <div className="button-row">
        <button disabled={busy} onClick={cancel}>
          Cancel
        </button>
        <button className="danger" disabled={busy} onClick={leave}>
          {busy ? 'Leaving…' : 'Leave workspace'}
        </button>
      </div>
    </div>
  );
}
