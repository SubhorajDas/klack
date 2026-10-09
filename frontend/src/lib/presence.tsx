'use client';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { api } from './api';
import type { User } from './types';

type Snapshot = { available: boolean; users: { user_id: string; online: boolean }[] };
export type OnlineStatus = 'online' | 'offline' | 'unknown';
const Presence = createContext<Record<string, OnlineStatus>>({});

export function PresenceProvider({ user, children }: { user: User; children: ReactNode }) {
  const [statuses, setStatuses] = useState<Record<string, OnlineStatus>>({});
  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    let refreshing = false;
    let pending = false;
    async function refresh() {
      if (stopped || document.visibilityState !== 'visible') return;
      if (refreshing) {
        pending = true;
        return;
      }
      refreshing = true;
      try {
        const snapshot = await api<Snapshot>('/presence');
        if (!stopped)
          setStatuses(
            snapshot.available
              ? Object.fromEntries(
                  snapshot.users.map((row) => [
                    row.user_id.replaceAll('-', ''),
                    row.online ? 'online' : 'offline',
                  ]),
                )
              : {},
          );
      } catch {
        if (!stopped) setStatuses({});
      } finally {
        refreshing = false;
        if (pending) {
          pending = false;
          void refresh();
        }
      }
    }
    function connect() {
      if (stopped) return;
      const url = new URL('/api/v1/realtime', window.location.href);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      const live = new WebSocket(url, 'klack.realtime.v1');
      socket = live;
      live.onmessage = (event) => {
        if (stopped || live !== socket) return;
        let data;
        try {
          data = JSON.parse(event.data);
        } catch {
          return;
        }
        if (data.type === 'ping') live.send(JSON.stringify({ type: 'pong' }));
        if (data.type === 'hello') {
          attempts = 0;
          void refresh();
        }
        if (data.type === 'presence.changed') void refresh();
      };
      live.onclose = () => {
        if (stopped) return;
        setStatuses({});
        retry = setTimeout(
          async () => {
            try {
              await api<User>('/auth/me');
            } catch {
              /* Session recovery handles sign-out. */
            }
            connect();
          },
          Math.min(1000 * 2 ** attempts++, 30000),
        );
      };
    }
    connect();
    void refresh();
    const timer = setInterval(() => void refresh(), 15000);
    const visible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    document.addEventListener('visibilitychange', visible);
    return () => {
      stopped = true;
      clearInterval(timer);
      clearTimeout(retry);
      document.removeEventListener('visibilitychange', visible);
      socket?.close();
    };
  }, [user.id]);
  return <Presence.Provider value={statuses}>{children}</Presence.Provider>;
}

export function useOnlineStatus(userId: string): OnlineStatus {
  return useContext(Presence)[userId.replaceAll('-', '')] || 'unknown';
}
