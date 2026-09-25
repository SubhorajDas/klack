'use client';
import { useEffect, useState } from 'react';
import {
  Users,
  Plus,
  Copy,
  Check,
  Link as LinkIcon,
  Shield,
  Monitor,
  Mail,
  LogOut,
  UserRound,
} from 'lucide-react';
import { useMemberName } from '@/lib/member-names';
import { api, errorMessage } from '@/lib/api';
import {
  workspacePath,
  type User,
  type Workspace,
  type Membership,
  type Invitation,
  type Session,
} from '@/lib/types';
import { Alert, Avatar, Empty, Loading, Modal } from './ui';

export function People({
  user,
  workspace,
  role,
  leave,
}: {
  user: User;
  workspace: Workspace;
  role: Membership['role'];
  leave: () => void;
}) {
  const memberName = useMemberName();
  const [members, setMembers] = useState<Membership[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [tab, setTab] = useState('members');
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [invite, setInvite] = useState(false);
  const [inviteUrl, setInviteUrl] = useState('');
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState<Membership | null>(null);
  const [nextRole, setNextRole] = useState('');
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [revision, setRevision] = useState(0);
  const manager = role === 'owner' || role === 'admin';
  const path = workspacePath(workspace.id);
  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([
      api<{ memberships: Membership[] }>(`${path}/memberships`),
      manager
        ? api<{ invitations: Invitation[] }>(`${path}/invitations`)
        : Promise.resolve({ invitations: [] }),
    ])
      .then(([roster, links]) => {
        if (active) {
          setMembers(roster.memberships);
          setInvitations(links.invitations);
          setError('');
        }
      })
      .catch((failure) => {
        if (active) setError(errorMessage(failure));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, manager, revision]);
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
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>People</h1>
          <p>The people who make {workspace.name} happen.</p>
        </div>
        <div className="button-row">
          <button className="danger-outline" onClick={leave}>
            <LogOut size={17} /> Leave workspace
          </button>
          {manager && (
            <button
              className="primary"
              onClick={() => {
                setInvite(true);
                setInviteUrl('');
                setCopied(false);
              }}
            >
              <Plus size={17} />
              Invite people
            </button>
          )}
        </div>
      </div>
      <Alert>{!invite && !target && error}</Alert>
      <div className="tabs">
        <button className={tab === 'members' ? 'selected' : ''} onClick={() => setTab('members')}>
          <Users size={16} />
          Members <span>{members.length}</span>
        </button>
        {manager && (
          <button
            className={tab === 'invitations' ? 'selected' : ''}
            onClick={() => setTab('invitations')}
          >
            Invitations <span>{invitations.length}</span>
          </button>
        )}
      </div>
      {loading ? (
        <Loading />
      ) : tab === 'members' ? (
        <>
          <input
            className="people-search"
            aria-label="Search members"
            placeholder="Search by name or email…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Member</th>
                  <th>Role</th>
                  <th>Joined</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {members
                  .filter((member) =>
                    `${memberName(member.user_id, user)} ${member.user_id === user.id ? user.email : member.email || ''}`
                      .toLowerCase()
                      .includes(filter.toLowerCase()),
                  )
                  .map((member) => (
                    <tr key={member.user_id}>
                      <td>
                        <div className="person-cell">
                          <Avatar name={memberName(member.user_id, user)} small />
                          <span>
                            <strong>
                              {memberName(member.user_id, user)}
                              {member.user_id === user.id ? ' (you)' : ''}
                            </strong>
                            <small>
                              {member.user_id === user.id
                                ? user.email
                                : member.email || 'Email unavailable'}
                            </small>
                          </span>
                        </div>
                      </td>
                      <td>
                        <span
                          className={`badge capitalize ${member.role === 'owner' ? 'purple' : ''}`}
                        >
                          {member.role}
                        </span>
                      </td>
                      <td>{new Date(member.joined_at).toLocaleDateString()}</td>
                      <td>
                        {manager && (role === 'owner' || member.role === 'member') && (
                          <button
                            onClick={() => {
                              setTarget(member);
                              setNextRole(member.role);
                              setConfirmRemove(false);
                              setError('');
                            }}
                          >
                            Manage
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Invitation</th>
                <th>Expires</th>
                <th>Status</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {invitations.map((item) => (
                <tr key={item.id}>
                  <td>
                    <span className="table-channel">
                      <LinkIcon size={16} />
                      Invite {item.id.slice(0, 8)}
                    </span>
                  </td>
                  <td>{new Date(item.expires_at).toLocaleDateString()}</td>
                  <td>
                    <span className="badge">
                      {item.accepted_at
                        ? 'Accepted'
                        : item.revoked_at
                          ? 'Revoked'
                          : Date.parse(item.expires_at) < Date.now()
                            ? 'Expired'
                            : 'Available'}
                    </span>
                  </td>
                  <td>
                    {!item.accepted_at && !item.revoked_at && (
                      <button
                        disabled={busy}
                        onClick={() =>
                          void action(async () => {
                            await api(`${path}/invitations/${item.id}`, 'DELETE');
                            setRevision(revision + 1);
                          })
                        }
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!invitations.length && (
            <Empty title="Bring your teammates along">
              Create a single-use link and share it with a teammate.
            </Empty>
          )}
        </div>
      )}
      {invite && (
        <Modal title="Invite your teammates" close={() => setInvite(false)}>
          <div className="form-stack">
            <Alert>{error}</Alert>
            <div className="invite-illustration">
              <Users size={35} />
            </div>
            <p>
              Great things happen together. Create a link for someone to join{' '}
              <strong>{workspace.name}</strong> as a member.
            </p>
            <p className="muted">
              Each invitation can be used once. Share it directly with your teammate before it
              expires.
            </p>
            {inviteUrl ? (
              <>
                <label>
                  Invitation link
                  <input readOnly value={inviteUrl} onFocus={(event) => event.target.select()} />
                </label>
                <button
                  className="primary"
                  onClick={() =>
                    void action(async () => {
                      await navigator.clipboard.writeText(inviteUrl);
                      setCopied(true);
                    })
                  }
                >
                  {copied ? <Check size={17} /> : <Copy size={17} />}{' '}
                  {copied ? 'Copied' : 'Copy invite link'}
                </button>
                <small className="muted">
                  Save this link now. It won’t be shown again after closing this window.
                </small>
              </>
            ) : (
              <button
                className="primary"
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    const result = await api<{ invite_url: string }>(`${path}/invitations`, 'POST');
                    setInviteUrl(result.invite_url);
                    setRevision(revision + 1);
                  })
                }
              >
                <LinkIcon size={17} />
                {busy ? 'Creating…' : 'Create invite link'}
              </button>
            )}
          </div>
        </Modal>
      )}
      {target && (
        <Modal title="Manage member" close={() => setTarget(null)}>
          <div className="form-stack">
            <Alert>{error}</Alert>
            <div className="person-cell">
              <Avatar name={memberName(target.user_id, user)} />
              <strong>{memberName(target.user_id, user)}</strong>
            </div>
            <label>
              Workspace role
              <select
                value={nextRole}
                onChange={(event) => setNextRole(event.target.value)}
                disabled={role !== 'owner'}
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
                <option value="owner">Owner</option>
              </select>
            </label>
            <p className="muted">
              Changes take effect immediately. Every workspace must keep at least one owner.
            </p>
            {role === 'owner' && (
              <button
                className="primary"
                disabled={busy || target.role === nextRole}
                onClick={() =>
                  void action(async () => {
                    await api(`${path}/memberships/${target.user_id}`, 'PATCH', { role: nextRole });
                    if (target.user_id === user.id) window.location.reload();
                    else {
                      setTarget(null);
                      setRevision(revision + 1);
                    }
                  })
                }
              >
                Save role
              </button>
            )}
            <button
              className="danger-outline"
              disabled={busy}
              onClick={() => {
                setConfirmRemove(true);
              }}
            >
              Remove from workspace
            </button>
            {confirmRemove && (
              <div className="confirm-box">
                <p>Remove this member and their channel access?</p>
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() =>
                    void action(async () => {
                      await api(`${path}/memberships/${target.user_id}`, 'DELETE');
                      if (target.user_id === user.id) window.location.reload();
                      else {
                        setTarget(null);
                        setRevision(revision + 1);
                      }
                    })
                  }
                >
                  Confirm removal
                </button>
              </div>
            )}
          </div>
        </Modal>
      )}
    </section>
  );
}

export function Account({
  user,
  updateUser,
  signOut,
}: {
  user: User;
  updateUser: (user: User | null) => void;
  signOut: () => void;
}) {
  const [tab, setTab] = useState('profile');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [compact, setCompact] = useState(false);
  const [revoke, setRevoke] = useState<Session | null>(null);
  useEffect(() => {
    try {
      setCompact(localStorage.getItem('klack:compact') === 'true');
    } catch {}
  }, []);
  useEffect(() => {
    if (tab !== 'security') return;
    let active = true;
    setLoading(true);
    api<{ sessions: Session[] }>('/auth/sessions')
      .then((data) => {
        if (active) setSessions(data.sessions);
      })
      .catch((failure) => {
        if (active) setError(errorMessage(failure));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [tab]);
  async function action(operation: () => Promise<void>) {
    setError('');
    setNotice('');
    setBusy(true);
    try {
      await operation();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>Settings</h1>
          <p>Make yourself at home.</p>
        </div>
      </div>
      <div className="settings-layout">
        <nav aria-label="Settings sections">
          {[
            { id: 'profile', label: 'My profile', Icon: UserRound },
            { id: 'appearance', label: 'Appearance', Icon: Monitor },
            { id: 'security', label: 'Security', Icon: Shield },
          ].map(({ id, label, Icon }) => (
            <button
              key={id}
              className={tab === id ? 'selected' : ''}
              onClick={() => {
                setTab(id);
                setError('');
                setNotice('');
              }}
            >
              <Icon size={18} />
              {label}
            </button>
          ))}
          <button onClick={signOut}>
            <LogOut size={18} />
            Sign out
          </button>
        </nav>
        <div className="settings-content">
          <Alert>{error}</Alert>
          {notice && (
            <div className="success" role="status">
              <Check size={18} />
              {notice}
            </div>
          )}
          {tab === 'profile' ? (
            <>
              <div className="profile-banner">
                <Avatar name={user.email} />
              </div>
              <h2>{user.email.split('@')[0]}</h2>
              <p className="muted">Your Klack account</p>
              <dl className="profile-fields">
                <div>
                  <dt>
                    <Mail size={17} />
                    Email
                  </dt>
                  <dd>{user.email}</dd>
                </div>
                <div>
                  <dt>
                    <Shield size={17} />
                    Verification
                  </dt>
                  <dd>
                    <span className={`badge ${user.email_verified ? 'green' : ''}`}>
                      {user.email_verified ? 'Verified' : 'Not verified'}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>Member since</dt>
                  <dd>{new Date(user.created_at).toLocaleDateString()}</dd>
                </div>
              </dl>
              {!user.email_verified && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void action(async () => {
                      await api('/auth/email-verification/request', 'POST');
                      setNotice(
                        'Verification requested. If email delivery is configured, check your inbox for a link.',
                      );
                    })
                  }
                >
                  {busy ? 'Requesting…' : 'Send verification link'}
                </button>
              )}
            </>
          ) : tab === 'appearance' ? (
            <>
              <h2>Appearance</h2>
              <p className="muted">A little more room, or a little less scrolling.</p>
              <label className="setting-row">
                <span>
                  <strong>Compact messages</strong>
                  <small>Reduce spacing between messages on this browser.</small>
                </span>
                <input
                  type="checkbox"
                  checked={compact}
                  onChange={(event) => {
                    const value = event.target.checked;
                    setCompact(value);
                    document.documentElement.dataset.compact = String(value);
                    try {
                      localStorage.setItem('klack:compact', String(value));
                    } catch {}
                  }}
                />
              </label>
              <div className="setting-preview">
                <Avatar name={user.email} small />
                <span>
                  <strong>{user.email.split('@')[0]}</strong>
                  <p>Great ideas deserve a little space. ✨</p>
                </span>
              </div>
            </>
          ) : (
            <>
              <h2>Active sessions</h2>
              <p className="muted">
                Manage where you’re signed in. Sign out any session you don’t recognize.
              </p>
              {loading ? (
                <Loading />
              ) : (
                sessions.map((session) => (
                  <div className="session-row" key={session.id}>
                    <Monitor size={24} />
                    <div>
                      <strong>{session.current ? 'This browser' : 'Another browser'}</strong>
                      <small>{session.user_agent || 'Unknown device'}</small>
                      <small>Last active {new Date(session.last_seen_at).toLocaleString()}</small>
                    </div>
                    <button onClick={() => setRevoke(session)}>Sign out</button>
                  </div>
                ))
              )}
            </>
          )}
        </div>
      </div>
      {revoke && (
        <Modal title="Sign out this session?" close={() => setRevoke(null)}>
          <div className="form-stack">
            <Alert>{error}</Alert>
            <p>
              {revoke.current
                ? 'You will be signed out of this browser.'
                : 'This browser session will lose access to your account.'}
            </p>
            <button
              className="danger"
              disabled={busy}
              onClick={() =>
                void action(async () => {
                  await api(`/auth/sessions/${revoke.id}`, 'DELETE');
                  if (revoke.current) updateUser(null);
                  else setSessions(sessions.filter((session) => session.id !== revoke.id));
                  setRevoke(null);
                })
              }
            >
              Sign out session
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
