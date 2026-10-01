'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { ConnectionState, Room, RoomEvent, Track } from 'livekit-client';
import { Mic, MicOff, Phone, PhoneIncoming, PhoneMissed, PhoneOff, X } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { channelPath, type Channel, type User } from '@/lib/types';
import { Alert, Empty, Loading, Modal } from './ui';

export type VoiceCall = {
  id: string;
  workspace_id: string;
  channel_id: string;
  caller_id: string;
  callee_id: string;
  peer_name: string;
  status: 'ringing' | 'active' | 'declined' | 'missed' | 'cancelled' | 'ended';
  created_at: string;
  answered_at: string | null;
  ended_at: string | null;
  owned: boolean;
};
type Calls = {
  call: VoiceCall | null;
  enabled: boolean;
  busy: boolean;
  device: string;
  revision: number;
  start: (channel: Channel) => Promise<void>;
};
const Context = createContext<Calls | null>(null);
export function useCalls() {
  const value = useContext(Context);
  if (!value) throw new Error('CallsProvider is required');
  return value;
}
const live = (call: VoiceCall) => call.status === 'ringing' || call.status === 'active';

async function checkMicrophone() {
  if (!navigator.mediaDevices?.getUserMedia)
    throw new Error('Your browser needs HTTPS or localhost to use the microphone.');
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());
  } catch {
    throw new Error('Allow microphone access in your browser, then try again.');
  }
}

export function CallsProvider({ user, children }: { user: User; children: ReactNode }) {
  const [device] = useState(() => crypto.randomUUID());
  const [call, setCall] = useState<VoiceCall | null>(null);
  const current = useRef<VoiceCall | null>(null);
  const generation = useRef(0);
  const acting = useRef(false);
  const endedLocally = useRef(new Set<string>());
  const lastControl = useRef(Date.now());
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0);
  const [muted, setMuted] = useState(false);
  const [connection, setConnection] = useState('Connecting…');
  const [audioBlocked, setAudioBlocked] = useState(false);
  const [now, setNow] = useState(Date.now());
  const room = useRef<Room | null>(null);
  const audio = useRef<HTMLDivElement>(null);
  const pendingStart = useRef<{ channel: string; id: string } | null>(null);

  const update = useCallback((next: VoiceCall | null) => {
    const previous = current.current;
    if (previous?.id !== next?.id || previous?.status !== next?.status) setRevision((n) => n + 1);
    if (previous && (!next || !live(next))) {
      setNotice(
        next?.status === 'declined'
          ? 'Call declined.'
          : next?.status === 'missed'
            ? 'No answer. The call was marked as missed.'
            : 'Call ended.',
      );
    }
    current.current = next && live(next) ? next : null;
    setCall(current.current);
  }, []);

  const control = useCallback(
    async (id: string, action: string) => {
      return api<VoiceCall>(`/calls/${id}/${action}`, 'POST', { device_id: device });
    },
    [device],
  );

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let lastHeartbeat = 0;
    const poll = async () => {
      const version = generation.current;
      try {
        const data = await api<{ enabled: boolean; calls: VoiceCall[] }>(
          `/calls?device_id=${device}`,
        );
        if (stopped || version !== generation.current || acting.current) return;
        setEnabled(data.enabled);
        if (!data.enabled) {
          update(null);
          stopped = true;
          return;
        }
        lastControl.current = Date.now();
        if (data.calls[0] && endedLocally.current.has(data.calls[0].id)) {
          await control(data.calls[0].id, 'end');
          return;
        }
        update(data.calls[0] ?? null);
        const active = data.calls[0];
        if (
          active?.status === 'active' &&
          active.owned &&
          room.current?.state === ConnectionState.Connected &&
          Date.now() - lastHeartbeat > 10000
        ) {
          lastHeartbeat = Date.now();
          const value = await control(active.id, 'heartbeat');
          if (!stopped && version === generation.current && !acting.current) update(value);
        }
      } catch {
        // The existing auth client handles session expiry. A live room is closed if control
        // remains unreachable; the server independently expires its participant leases.
      } finally {
        if (!stopped) timer = setTimeout(poll, 2000);
      }
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [device, control, update]);

  useEffect(() => {
    if (!call || call.status !== 'active' || !call.owned) return;
    let stopped = false;
    const id = call.id;
    const instance = new Room();
    room.current = instance;
    setMuted(false);
    setAudioBlocked(false);
    setConnection('Connecting…');
    const fail = async () => {
      if (stopped) return;
      setError('The voice connection ended. Please try calling again.');
      endedLocally.current.add(id);
      generation.current++;
      update(null);
      try {
        await control(id, 'end');
      } catch {
        /* Polling retries termination. */
      }
    };
    instance.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === Track.Kind.Audio && !stopped) {
        const element = track.attach();
        audio.current?.appendChild(element);
      }
    });
    instance.on(RoomEvent.TrackUnsubscribed, (track) =>
      track.detach().forEach((element) => element.remove()),
    );
    instance.on(RoomEvent.AudioPlaybackStatusChanged, () =>
      setAudioBlocked(!instance.canPlaybackAudio),
    );
    instance.on(RoomEvent.Reconnecting, () => setConnection('Reconnecting…'));
    instance.on(RoomEvent.Reconnected, () => setConnection('Connected'));
    instance.on(RoomEvent.Disconnected, () => void fail());
    void (async () => {
      try {
        const credentials = await api<{ url: string; token: string }>(
          `/calls/${id}/connection/token`,
          'POST',
          { device_id: device },
        );
        if (stopped) return;
        await instance.connect(credentials.url, credentials.token);
        if (stopped) {
          await instance.disconnect();
          return;
        }
        await instance.localParticipant.setMicrophoneEnabled(true);
        if (stopped) {
          await instance.disconnect();
          return;
        }
        setConnection('Connected');
        await instance.startAudio().catch(() => setAudioBlocked(true));
      } catch {
        if (!stopped) await fail();
      }
    })();
    return () => {
      stopped = true;
      instance.removeAllListeners();
      void instance.disconnect();
      if (room.current === instance) room.current = null;
      audio.current?.replaceChildren();
    };
  }, [call?.id, call?.status, call?.owned, device, control, update]);

  useEffect(() => {
    if (!call) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [call?.id]);

  useEffect(() => {
    const timer = setInterval(() => {
      const active = current.current;
      if (active?.owned && active.status === 'active' && Date.now() - lastControl.current > 45000) {
        endedLocally.current.add(active.id);
        update(null);
        setError('The connection was lost and your call ended.');
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [update]);

  async function start(channel: Channel) {
    if (acting.current || current.current) return;
    acting.current = true;
    generation.current++;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await checkMicrophone();
      if (pendingStart.current?.channel !== channel.id)
        pendingStart.current = { channel: channel.id, id: crypto.randomUUID() };
      const value = await api<VoiceCall>('/calls', 'POST', {
        workspace_id: channel.workspace_id,
        channel_id: channel.id,
        device_id: device,
        request_id: pendingStart.current.id,
      });
      pendingStart.current = null;
      update(value);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      acting.current = false;
      generation.current++;
      setBusy(false);
    }
  }

  async function action(kind: 'accept' | 'decline' | 'end') {
    const active = current.current;
    if (!active || acting.current) return;
    acting.current = true;
    generation.current++;
    setBusy(true);
    setError('');
    try {
      if (kind === 'accept') await checkMicrophone();
      if (kind === 'end') {
        endedLocally.current.add(active.id);
        update(null);
      }
      update(await control(active.id, kind));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      acting.current = false;
      generation.current++;
      setBusy(false);
    }
  }

  async function toggleMute() {
    if (!room.current || busy) return;
    setBusy(true);
    try {
      await room.current.localParticipant.setMicrophoneEnabled(muted);
      setMuted(!muted);
    } catch {
      setError('Could not change the microphone. Check your browser permissions.');
    } finally {
      setBusy(false);
    }
  }

  const incoming = call?.status === 'ringing' && call.callee_id === user.id;
  const seconds = call?.answered_at
    ? Math.max(0, Math.floor((now - Date.parse(call.answered_at)) / 1000))
    : 0;
  return (
    <Context.Provider value={{ call, enabled, busy, device, revision, start }}>
      {children}
      <div ref={audio} className="call-audio" />
      {incoming && (
        <Modal
          title="Incoming voice call"
          close={() => {
            if (!busy) void action('decline');
          }}
        >
          <div className="incoming-call">
            <span className="call-avatar">
              <PhoneIncoming size={32} />
            </span>
            <h3>{call.peer_name}</h3>
            <p>would like to talk with you.</p>
            <Alert>{error}</Alert>
            <div className="call-actions">
              <button
                className="call-decline"
                disabled={busy}
                onClick={() => void action('decline')}
              >
                <PhoneOff size={18} />
                Decline
              </button>
              <button className="primary" disabled={busy} onClick={() => void action('accept')}>
                <Phone size={18} />
                {busy ? 'Please wait…' : 'Accept'}
              </button>
            </div>
          </div>
        </Modal>
      )}
      {call && !incoming && (
        <section className="active-call" aria-label="Voice call">
          <Phone size={21} />
          <div className="call-summary">
            <strong>{call.peer_name}</strong>
            <span role="status">
              {call.status === 'ringing'
                ? 'Calling…'
                : !call.owned
                  ? 'Open in another tab'
                  : `${connection} · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`}
            </span>
          </div>
          {call.status === 'active' && call.owned && (
            <button
              className="icon-button"
              aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
              aria-pressed={muted}
              disabled={busy || connection !== 'Connected'}
              onClick={() => void toggleMute()}
            >
              {muted ? <MicOff /> : <Mic />}
            </button>
          )}
          {audioBlocked && call.owned && (
            <button onClick={() => void room.current?.startAudio()}>Enable sound</button>
          )}
          <button className="call-decline" disabled={busy} onClick={() => void action('end')}>
            <PhoneOff size={18} />
            {call.status === 'ringing' ? 'Cancel call' : 'Hang up'}
          </button>
        </section>
      )}
      {!incoming && (error || notice) && (
        <div className="call-notice" role={error ? 'alert' : 'status'}>
          <span>{error || notice}</span>
          <button
            className="icon-button"
            aria-label="Dismiss call notification"
            onClick={() => {
              setError('');
              setNotice('');
            }}
          >
            <X size={16} />
          </button>
        </div>
      )}
    </Context.Provider>
  );
}

export function CallHistory({ channel, user }: { channel: Channel; user: User }) {
  const { device, revision } = useCalls();
  const [calls, setCalls] = useState<VoiceCall[]>([]);
  const [before, setBefore] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const path = `${channelPath(channel.workspace_id, channel.id)}/calls?device_id=${device}`;
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    api<{ calls: VoiceCall[]; next_before: string | null }>(path)
      .then((data) => {
        if (!cancelled) {
          setCalls(data.calls);
          setBefore(data.next_before);
        }
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
  }, [path, revision, attempt]);
  async function more() {
    setLoading(true);
    try {
      const data = await api<{ calls: VoiceCall[]; next_before: string | null }>(
        `${path}&before=${before}`,
      );
      setCalls((rows) => [
        ...rows,
        ...data.calls.filter((row) => !rows.some((old) => old.id === row.id)),
      ]);
      setBefore(data.next_before);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }
  return (
    <div className="call-history">
      <h2>Call history</h2>
      <Alert>{error}</Alert>
      {error && <button onClick={() => setAttempt((n) => n + 1)}>Try again</button>}
      {!loading && !error && !calls.length && (
        <Empty title="No calls yet">Voice calls and missed calls will appear here.</Empty>
      )}
      <ul>
        {calls.map((entry) => {
          const incoming = entry.callee_id === user.id;
          const missed = entry.status === 'missed';
          const duration =
            entry.answered_at && entry.ended_at
              ? Math.max(
                  0,
                  Math.floor((Date.parse(entry.ended_at) - Date.parse(entry.answered_at)) / 1000),
                )
              : null;
          return (
            <li key={entry.id} className={missed ? 'missed-call' : ''}>
              {missed ? <PhoneMissed size={21} /> : <Phone size={21} />}
              <div>
                <strong>
                  {missed
                    ? 'Missed call'
                    : entry.status === 'ended'
                      ? 'Voice call'
                      : entry.status === 'cancelled'
                        ? 'Cancelled call'
                        : entry.status === 'declined'
                          ? 'Declined call'
                          : entry.status === 'ringing'
                            ? 'Calling…'
                            : 'Call in progress'}
                </strong>
                <span>
                  {incoming ? 'From' : 'To'} {entry.peer_name}
                  {duration !== null ? ` · ${Math.floor(duration / 60)}m ${duration % 60}s` : ''}
                </span>
              </div>
              <time dateTime={entry.created_at}>{new Date(entry.created_at).toLocaleString()}</time>
            </li>
          );
        })}
      </ul>
      {loading && <Loading label="Loading calls…" />}
      {before && !loading && <button onClick={() => void more()}>Older calls</button>}
    </div>
  );
}
