'use client';

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, ApiError } from './api';
import { useCachedApiData } from './use-cached-data';
import { memberName, type Membership, type User } from './types';

const MemberNames = createContext<Membership[]>([]);

export function MemberNamesProvider({ children }: { children: ReactNode }) {
  const [members, setMembers] = useState<Membership[]>([]);
  const cached = useCachedApiData<{ memberships: Membership[] }>('/contacts');
  useEffect(() => {
    let stopped = false;
    async function refresh() {
      try {
        const data = await api<{ memberships: Membership[] }>('/contacts');
        if (!stopped) setMembers(data.memberships);
      } catch (failure) {
        if (!stopped && failure instanceof ApiError && [401, 403, 404].includes(failure.status))
          setMembers([]);
        // Keep the last known names during a temporary connection failure.
      }
    }
    void refresh();
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    const timer = setInterval(onVisible, 15000);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);
  return (
    <MemberNames.Provider value={cached?.memberships ?? members}>{children}</MemberNames.Provider>
  );
}

export function useMemberName() {
  const members = useContext(MemberNames);
  return (id: string, user: User) => memberName(id, user, members);
}
