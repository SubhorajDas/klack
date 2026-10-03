'use client';

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, ApiError } from './api';

type Counts = {
  total: number;
  direct_messages: number;
  workspaces: Record<string, number>;
  channels: Record<string, number>;
};
const empty: Counts = { total: 0, direct_messages: 0, workspaces: {}, channels: {} };
const UnreadCounts = createContext<Counts>(empty);

export function UnreadCountsProvider({ children }: { children: ReactNode }) {
  const [counts, setCounts] = useState(empty);
  useEffect(() => {
    let stopped = false;
    let sequence = 0;
    let debounce: ReturnType<typeof setTimeout>;
    const broadcast = new BroadcastChannel('klack-unread');
    async function refresh() {
      if (document.visibilityState !== 'visible') return;
      const current = ++sequence;
      try {
        const data = await api<Counts>('/unread-counts');
        if (!stopped && current === sequence) setCounts(data);
      } catch (error) {
        if (!stopped && error instanceof ApiError && [401, 403].includes(error.status))
          setCounts(empty);
        // A transient failure must not incorrectly erase unread indicators.
      }
    }
    function changed() {
      clearTimeout(debounce);
      debounce = setTimeout(() => void refresh(), 100);
    }
    function localChange() {
      changed();
      broadcast.postMessage('refresh');
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    broadcast.onmessage = changed;
    document.addEventListener('visibilitychange', changed);
    window.addEventListener('focus', changed);
    window.addEventListener('klack:read', localChange);
    window.addEventListener('klack:messages-changed', localChange);
    return () => {
      stopped = true;
      clearInterval(timer);
      clearTimeout(debounce);
      broadcast.close();
      document.removeEventListener('visibilitychange', changed);
      window.removeEventListener('focus', changed);
      window.removeEventListener('klack:read', localChange);
      window.removeEventListener('klack:messages-changed', localChange);
    };
  }, []);
  return <UnreadCounts.Provider value={counts}>{children}</UnreadCounts.Provider>;
}

export const useUnreadCounts = () => useContext(UnreadCounts);

export function UnreadBadge({ count }: { count: number }) {
  return count > 0 ? (
    <span className="unread-badge" aria-label={`${count} unread messages`}>
      {count > 99 ? '99+' : count}
    </span>
  ) : null;
}
